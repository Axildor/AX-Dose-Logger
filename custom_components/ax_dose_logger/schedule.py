"""
Shared next-dose-time scheduling helpers for Pill Logger.

Extracted from duplicated inline copies in ``adherence.py`` (Batch 4A of
the backend technical audit).  Only the *Regular Interval* and *Time of
Day* branches are shared here; the *Cyclic/Calendar Pattern* branch is
kept inline in each caller because ``next_dose.py`` and ``adherence.py``
use different algorithms for it.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry

from .const import TRACKING_REGULAR_INTERVAL, TRACKING_TIME_OF_DAY, get_dose_times, parse_dose_time
from .sliding_window import is_on_day

# Lateness-window modes for :func:`compute_slot_assignments`.
LATENESS_UNTIL_NEXT_SLOT = "until_next_slot"
LATENESS_CAPPED = "capped"


@dataclass
class SlotAssignment:
    """One scheduled slot and the dose(s) assigned to it.

    ``assigned_ts`` is the FIRST dose assigned to this slot (None when the
    slot has no doses yet); ``assigned_count`` is how many doses it has
    consumed.  ``covered`` is True when the slot has consumed
    ``pills_per_slot`` doses (1 in the default single-pill-per-slot model).
    ``next_slot_time`` is the immediately following slot in the timeline
    (crosses midnight into the next day's first slot) and defines the
    lateness window upper bound when ``lateness_mode ==
    LATENESS_UNTIL_NEXT_SLOT``.
    """

    slot_time: datetime
    next_slot_time: datetime
    assigned_ts: datetime | None
    covered: bool
    assigned_count: int = 1


def get_next_cyclic_dose_time(entry: ConfigEntry, now):
    """Return the next Cyclic ON-day slot strictly after the current one.

    This is the *next ON day* question, not the *next cycle* question.  The
    prior implementation computed ``cycle_length - position_in_cycle``, which
    measures to the first ON day of the NEXT cycle.  On a 5-on/2-off schedule,
    taking today's dose on the 4th ON day therefore reported "next dose in 4
    days" when it was actually tomorrow — the dose was reported as skipped for
    every remaining ON day of the run.

    Walking forward day by day is the only formulation that stays correct for
    every cycle shape (runs of ON days, single ON day per cycle, daily, and
    cycles longer than the search window).

    Args:
        entry: The config entry carrying the cycle config.
        now: Tz-aware "current" time.

    Returns:
        The next ON-day slot datetime.  Falls back to the next calendar day at
        the configured ``dose_time`` when no ON day is found inside a full
        cycle (defensive — ``is_on_day`` guarantees one exists).
    """
    days_on = int(entry.options.get("days_on", entry.data.get("days_on", 5)))
    days_off = int(entry.options.get("days_off", entry.data.get("days_off", 2)))
    cycle_length = max(1, days_on + days_off)

    # Scan a FULL cycle forward.  A shorter horizon is not enough: on the LAST
    # ON day of an ON run, every ON slot is in the past and the next ON day sits
    # up to ``days_off`` days away (plus the rest of today), so a 1-day horizon
    # would find nothing and fall through to the bogus fallback below.
    slot_times = cyclic_slot_times(entry, now, lookback_days=0, future_days=cycle_length)
    for slot in slot_times:
        if slot > now:
            return slot
    # Defensive only: a full cycle always contains an ON day, so this is
    # unreachable unless the cycle anchor is in the future or the config is
    # degenerate (e.g. days_on = 0).  Returning a real future slot beats
    # returning a silently-wrong "now + 1 day"; surface the degenerate case.
    return now + timedelta(days=1)


def get_next_dose_time(
    entry: ConfigEntry,
    timestamps: list,
    now,
    tracking_type: str,
):
    """
    Return the next expected dose datetime, or ``None`` if unknown.

    Handles *Regular Interval*, *Time of Day* and *Cyclic/Calendar Pattern*.
    Returns ``None`` for *As Needed* — callers must handle that branch.
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


def cyclic_slot_times(
    entry: ConfigEntry,
    now,
    lookback_days: int,
    future_days: int = 1,
) -> list:
    """Build the Cyclic ON-day slot timeline (one slot per ON day at ``dose_time``).

    Only ON days contribute a slot, so an OFF day is structurally incapable of
    being "overdue".  The timeline spans ``lookback_days`` back (clamped to the
    cycle anchor so no slot predates the schedule) and ``future_days`` forward,
    which is what lets a missed ON slot survive midnight into the following
    OFF span instead of vanishing at 00:00.

    Args:
        entry: The config entry carrying ``days_on`` / ``days_off`` /
            ``cycle_anchor_date`` / ``dose_time``.
        now: Tz-aware "current" time used to anchor the timeline and supply
            the tzinfo for the slots.
        lookback_days: How many calendar days before ``now.date()`` to seed
            slots.  Callers pass a full cycle (``days_on + days_off``) so
            nothing inside the running cycle is ever lost.
        future_days: How many calendar days after ``now.date()`` to seed
            slots.  Default 1 (the next ON day is always resolvable).

    Returns:
        Chronologically sorted list of slot datetimes (possibly empty when the
        anchor is in the future).
    """
    days_on = int(entry.options.get("days_on", entry.data.get("days_on", 5)))
    days_off = int(entry.options.get("days_off", entry.data.get("days_off", 2)))
    dose_time_str = entry.options.get("dose_time", entry.data.get("dose_time", "08:00"))
    dose_hour, dose_minute = parse_dose_time(dose_time_str)

    try:
        anchor_date = date.fromisoformat(entry.options.get("cycle_anchor_date", entry.data.get("cycle_anchor_date")))
    except ValueError, TypeError:
        anchor_date = now.date()

    cycle_length = days_on + days_off
    if cycle_length <= 0:
        cycle_length = 1

    slot_times: list = []
    base_date = now.date()
    for day_offset in range(-lookback_days, future_days + 1):
        slot_date = base_date + timedelta(days=day_offset)
        # Never anchor a slot before the cycle started — the user was not
        # yet prescribed this medication, so it cannot be a missed dose.
        if slot_date < anchor_date:
            continue
        if not is_on_day(entry, slot_date, anchor_date):
            continue
        slot_times.append(datetime.combine(slot_date, time(dose_hour, dose_minute), tzinfo=now.tzinfo))
    slot_times.sort()
    return slot_times


def cyclic_early_grace(entry: ConfigEntry) -> timedelta:
    """Return the Cyclic early-dose tolerance (a dose this early still counts).

    Derived from the shortest ON-to-ON gap so the tolerance can never span two
    scheduled doses:

    * alternating (1 on / 1 off) → 48 h gap → 24 h grace
    * 5 on / 2 off → 24 h gap → 12 h grace
    * daily (7 on / 0 off) → 24 h gap → 12 h grace

    The 30-minute floor is the same absolute minimum the Time of Day model
    uses; the 24-hour ceiling keeps a long alternating cycle from letting a
    dose a full day early cover the slot.
    """
    days_on = int(entry.options.get("days_on", entry.data.get("days_on", 5)))
    days_off = int(entry.options.get("days_off", entry.data.get("days_off", 2)))
    cycle_length = days_on + days_off
    if cycle_length <= 0:
        cycle_length = 1
    # Gap between consecutive ON slots, in days.  A single ON day per cycle
    # leaves the whole cycle as the gap; a run of ON days gives a 1-day gap.
    on_run_gap_days = 1 if days_on > 1 else cycle_length
    grace_hours = min(24.0, (on_run_gap_days * 24.0) / 2.0)
    return timedelta(minutes=max(30, int(grace_hours * 60)))


def compute_cyclic_slot_assignments(  # noqa: PLR0913 - signature mirrors compute_slot_assignments
    entry: ConfigEntry,
    timestamps: list,
    now,
    *,
    lookback_days: int,
    future_days: int = 1,
    pills_per_slot: int = 1,
) -> list[SlotAssignment]:
    """Assign doses to Cyclic ON-day slots (lateness extends to the next ON slot).

    This is the Cyclic member of the shared slot family: same greedy,
    chronological, one-dose-per-slot model as :func:`compute_slot_assignments`,
    but the slot grid contains ON days only.

    The lateness policy is ``LATENESS_UNTIL_NEXT_SLOT``: a dose taken anywhere
    between the missed ON slot and the following ON slot consumes that slot.
    That is deliberate and clinically motivated — the label guidance for a
    missed dose is "take it as soon as you remember", so a dose taken after
    midnight on the following OFF day must clear the miss.  Without it, a
    midnight-crossing catch-up dose would leave the ON slot flagged overdue for
    the rest of the cycle simply because the dose landed on a different
    calendar date.

    Args:
        entry: The config entry (cycle + dose_time + pills_per_slot).
        timestamps: Dose datetimes plus any deliberately-skipped slots (both
            mean "this slot is discharged").
        now: Tz-aware "current" time.
        lookback_days: Slots to seed before today — callers pass a full cycle.
        future_days: Slots to seed after today (default 1).
        pills_per_slot: Doses each slot may consume (default 1).

    Returns:
        List of :class:`SlotAssignment` in chronological slot order.  Empty
        when the cycle has no ON day inside the window.
    """
    slot_times = cyclic_slot_times(entry, now, lookback_days, future_days)
    return _assign_doses_to_slots(
        slot_times,
        timestamps,
        early_grace=cyclic_early_grace(entry),
        pills_per_slot=pills_per_slot,
    )


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
    pills_per_slot: int = 1,
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
        pills_per_slot: Number of doses each slot can cover (default 1).
            Values below 1 are clamped to 1.

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

    return _assign_doses_to_slots(
        slot_times,
        timestamps,
        early_grace=early_grace,
        pills_per_slot=pills_per_slot,
        lateness_mode=lateness_mode,
        lateness_cap=lateness_cap,
    )


def _assign_doses_to_slots(  # noqa: PLR0913 - mirrors compute_slot_assignments' knob set
    slot_times: list[datetime],
    timestamps: list,
    *,
    early_grace: timedelta,
    pills_per_slot: int = 1,
    lateness_mode: str = LATENESS_UNTIL_NEXT_SLOT,
    lateness_cap: timedelta | None = None,
) -> list[SlotAssignment]:
    """Greedily assign doses to an already-built, sorted slot timeline.

    Shared by :func:`compute_slot_assignments` (Time of Day / Regular
    Interval) and :func:`compute_cyclic_slot_assignments` (Cyclic ON days), so
    all three scheduled tracking types run one assignment algorithm.  A
    divergence between them is therefore impossible by construction.

    Each slot's window is ``[slot_time - early_grace, late_bound)`` where
    ``late_bound`` is the following slot (``LATENESS_UNTIL_NEXT_SLOT``) or
    ``slot_time + lateness_cap`` (``LATENESS_CAPPED``).  A dose exactly at
    ``late_bound`` belongs to the next slot, not this one.
    """
    if lateness_mode == LATENESS_CAPPED and lateness_cap is None:
        msg = "lateness_cap is required when lateness_mode == LATENESS_CAPPED"
        raise ValueError(msg)

    pills_per_slot = max(pills_per_slot, 1)

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
        assigned_count = 0
        # Advance past doses that fall before this window (too early for
        # this slot and any later slot — they belong to earlier slots
        # we've already processed, or pre-lookback noise).
        while dose_idx < len(sorted_doses) and sorted_doses[dose_idx] < window_start:
            dose_idx += 1

        # Consume up to ``pills_per_slot`` doses for this slot (all within
        # the same lateness window).  With the default pills_per_slot=1
        # this is exactly the historical one-dose-per-slot behavior.
        while assigned_count < pills_per_slot and dose_idx < len(sorted_doses) and sorted_doses[dose_idx] < late_bound:
            if assigned_ts is None:
                assigned_ts = sorted_doses[dose_idx]
            assigned_count += 1
            dose_idx += 1

        assignments.append(
            SlotAssignment(
                slot_time=slot_time,
                next_slot_time=next_slot_time,
                assigned_ts=assigned_ts,
                covered=assigned_count >= pills_per_slot,
                assigned_count=assigned_count,
            )
        )

    return assignments
