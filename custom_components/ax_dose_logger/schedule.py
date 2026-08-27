"""
Shared next-dose-time scheduling helpers for Pill Logger.

Extracted from duplicated inline copies in ``adherence.py`` (Batch 4A of
the backend technical audit).  Only the *Regular Interval* and *Time of
Day* branches are shared here; the *Cyclic/Calendar Pattern* branch is
kept inline in each caller because ``next_dose.py`` and ``adherence.py``
use different algorithms for it.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry

from .const import TRACKING_REGULAR_INTERVAL, TRACKING_TIME_OF_DAY, get_dose_times

# Lateness-window modes for :func:`compute_slot_assignments`.
LATENESS_UNTIL_NEXT_SLOT = "until_next_slot"
LATENESS_CAPPED = "capped"


@dataclass
class SlotAssignment:
    """One scheduled slot and the dose (if any) assigned to it.

    ``covered`` is True when a dose was assigned to this slot.  ``next_slot_time``
    is the immediately following slot in the timeline (crosses midnight into
    the next day's first slot) and defines the lateness window upper bound
    when ``lateness_mode == LATENESS_UNTIL_NEXT_SLOT``.
    """

    slot_time: datetime
    next_slot_time: datetime
    assigned_ts: datetime | None
    covered: bool


def get_next_dose_time(
    entry: ConfigEntry,
    timestamps: list,
    now,
    tracking_type: str,
):
    """
    Return the next expected dose datetime, or ``None`` if unknown.

    Handles *Regular Interval* and *Time of Day* tracking types.
    Returns ``None`` for *Cyclic/Calendar Pattern* and *As Needed* —
    callers must handle those branches inline.
    """
    if tracking_type == TRACKING_REGULAR_INTERVAL:
        hours_between = entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 0))
        if timestamps:
            # Chained-deadline model (parity with the overdue + next_dose
            # sensors): return the next *future* chained deadline
            # ``anchor + (n+1) * interval`` so callers (steady_state) see
            # the same schedule the sensors expose.  max() not [-1]: the
            # timestamp list is not guaranteed sorted.
            interval = timedelta(hours=hours_between)
            anchor = max(timestamps)
            elapsed = now - anchor
            if elapsed <= timedelta(0):
                return anchor + interval
            n = int(elapsed.total_seconds() // interval.total_seconds())
            return anchor + (n + 1) * interval
        return now

    if tracking_type == TRACKING_TIME_OF_DAY:
        parsed_times = get_dose_times(entry)
        if not parsed_times:
            return now

        for hour, minute in parsed_times:
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target > now:
                return target

        first_hour, first_minute = parsed_times[0]
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=first_hour, minute=first_minute, second=0, microsecond=0)

    return None


def compute_slot_assignments(  # noqa: PLR0913 - policy-rich helper; callers pass 2 required + up to 5 knobs
    parsed_times: list[tuple[int, int]],
    timestamps: list,
    now: datetime,
    *,
    lookback_days: int = 2,
    future_days: int = 1,
    early_grace: timedelta,
    lateness_mode: str,
    lateness_cap: timedelta | None = None,
) -> list[SlotAssignment]:
    """Assign each scheduled slot the earliest unassigned dose it covers.

    This is the greedy, chronological slot-assignment model shared by the
    overdue, next_dose and adherence sensors for *Time of Day* tracking.
    It fixes two related bugs the prior "each slot independently matches
    any dose within ±grace" model produced:

    1. **Dose stealing** — a dose taken late for slot A but early for the
       later slot B was matched to B (the future slot), leaving A marked
       missed.  Greedy chronological order means the earlier slot always
       claims the dose first, so a dose between 13:00 and 21:00 is the
       *late 13:00 dose*, never the *early 21:00 dose*.
    2. **Midnight reset** — overdue dropped to 0 at 00:01 because only
       today's slots were considered.  The timeline spans
       ``lookback_days`` back and ``future_days`` forward, so a missed
       slot from yesterday keeps counting until a dose covers it.

    Args:
        parsed_times: Sorted ``[(hour, minute), ...]`` from
            :func:`get_dose_times`.  Must be non-empty.
        timestamps: Dose datetimes (tz-aware, unsorted).  Timestamps
            before the first slot in the timeline are ignored.
        now: Tz-aware "current" time; used to anchor the timeline.
        lookback_days: How many calendar days before ``now.date()`` to
            seed slots.  Caps how far back a missed slot can anchor
            overdue (prevents a runaway value for a user many days
            behind).  Default 2.
        future_days: How many calendar days after ``now.date()`` to seed
            slots.  Default 1.
        early_grace: A dose this far *before* ``slot_time`` still covers
            the slot (``[slot_time - early_grace, slot_time)``).
        lateness_mode: ``LATENESS_UNTIL_NEXT_SLOT`` (lateness extends
            until the following slot) or ``LATENESS_CAPPED`` (lateness
            capped at ``lateness_cap``).  Adherence uses ``CAPPED`` so a
            very late dose does not count as on-time; overdue/next_dose
            use ``UNTIL_NEXT_SLOT`` so a late-but-taken dose clears
            overdue.
        lateness_cap: Required when ``lateness_mode == LATENESS_CAPPED``;
            ignored otherwise.

    Returns:
        List of :class:`SlotAssignment` in chronological slot order.
    """
    if lateness_mode == LATENESS_CAPPED and lateness_cap is None:
        msg = "lateness_cap is required when lateness_mode == LATENESS_CAPPED"
        raise ValueError(msg)

    # Build the slot timeline: for each calendar day in the window, one
    # slot per parsed time, in chronological order across the whole span.
    slot_times: list[datetime] = []
    base_date = now.date()
    for day_offset in range(-lookback_days, future_days + 1):
        slot_date = base_date + timedelta(days=day_offset)
        for hour, minute in parsed_times:
            slot_times.append(datetime.combine(slot_date, time(hour, minute), tzinfo=now.tzinfo))
    slot_times.sort()

    if not slot_times:
        return []

    # next_slot_time per slot = the following slot (last slot's next is
    # extrapolated as its own time + the median inter-slot gap, so the
    # final slot still gets a defined lateness window for UNTIL_NEXT_SLOT).
    if len(slot_times) >= 2:
        gaps = [slot_times[i + 1] - slot_times[i] for i in range(len(slot_times) - 1)]
        median_gap = sorted(gaps)[len(gaps) // 2]
    else:
        median_gap = timedelta(days=1)

    sorted_doses = sorted(timestamps)
    dose_idx = 0
    assignments: list[SlotAssignment] = []

    for i, slot_time in enumerate(slot_times):
        next_slot_time = slot_times[i + 1] if i + 1 < len(slot_times) else slot_time + median_gap

        if lateness_mode == LATENESS_UNTIL_NEXT_SLOT:
            late_bound = next_slot_time
        else:  # LATENESS_CAPPED
            late_bound = slot_time + lateness_cap

        window_start = slot_time - early_grace
        # window is [window_start, late_bound).  A dose exactly at
        # late_bound belongs to the next slot, not this one.

        assigned_ts: datetime | None = None
        # Advance past doses that fall before this window (too early for
        # this slot and any later slot — they belong to earlier slots
        # we've already processed, or pre-lookback noise).
        while dose_idx < len(sorted_doses) and sorted_doses[dose_idx] < window_start:
            dose_idx += 1

        if dose_idx < len(sorted_doses) and sorted_doses[dose_idx] < late_bound:
            assigned_ts = sorted_doses[dose_idx]
            dose_idx += 1  # consume — one dose per slot

        assignments.append(
            SlotAssignment(
                slot_time=slot_time,
                next_slot_time=next_slot_time,
                assigned_ts=assigned_ts,
                covered=assigned_ts is not None,
            )
        )

    return assignments
