"""
Shared sliding-window and cyclic ON/OFF helpers for Pill Logger.

These pure functions were extracted from duplicated inline copies in
``pill_limit.py``, ``next_dose.py``, ``adherence.py``, ``avg_doses.py``,
and ``calendar.py`` (Batch 4A of the backend technical audit).
"""

from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry

from .const import (
    DOSE_BUFFER_CAP_FRACTION,
    DOSE_BUFFER_DEFAULT_MIN,
    TRACKING_AS_NEEDED,
    TRACKING_CYCLIC,
    TRACKING_REGULAR_INTERVAL,
)


def get_time_window(entry: ConfigEntry, tracking_type: str) -> float:
    """
    Return ``time_window_hours`` with mode-specific fallbacks.

    * Regular Interval → falls back to ``hours_between_doses`` (default 8)
    * As Needed → default 8
    * everything else (Cyclic, Time of Day) → default 24
    """
    if tracking_type == TRACKING_REGULAR_INTERVAL:
        return entry.options.get(
            "time_window_hours",
            entry.data.get(
                "time_window_hours",
                entry.options.get(
                    "hours_between_doses",
                    entry.data.get("hours_between_doses", 8),
                ),
            ),
        )
    if tracking_type == TRACKING_AS_NEEDED:
        return entry.options.get(
            "time_window_hours",
            entry.data.get("time_window_hours", 8),
        )
    return entry.options.get(
        "time_window_hours",
        entry.data.get("time_window_hours", 24),
    )


def is_on_day(entry: ConfigEntry, check_date: date, fallback_date: date | None = None) -> bool:
    """
    Return ``True`` when *check_date* falls on an ON day of the cyclic cycle.

    *fallback_date* is used only when ``cycle_anchor_date`` cannot be parsed
    (which should never happen post-config-flow).  It defaults to
    *check_date*, which is the safest behaviour: an invalid anchor makes
    every day an ON day rather than silently dropping doses.
    """
    # HA's NumberSelector stores these as floats; coerce to int for
    # correct modulo/cycle arithmetic (covers is_on_day callers:
    # overdue, next_dose, adherence, steady_state).
    days_on = int(entry.options.get("days_on", entry.data.get("days_on", 5)))
    days_off = int(entry.options.get("days_off", entry.data.get("days_off", 2)))
    anchor_str = entry.options.get("cycle_anchor_date", entry.data.get("cycle_anchor_date"))

    try:
        anchor_date = date.fromisoformat(anchor_str)
    except ValueError, TypeError:
        anchor_date = fallback_date if fallback_date is not None else check_date

    cycle_length = days_on + days_off
    if cycle_length <= 0:
        cycle_length = 1

    days_since_anchor = (check_date - anchor_date).days
    position_in_cycle = days_since_anchor % cycle_length
    return position_in_cycle < days_on


def effective_dose_buffer_minutes(
    entry: ConfigEntry,
    time_window_hours: float,
) -> int:
    """Return the effective anti-drift dose buffer in whole minutes.

    Reads the user-configured ``dose_buffer_minutes`` (default 5, range 0-120)
    and caps it at ``DOSE_BUFFER_CAP_FRACTION`` (25%) of the rolling window so
    a misconfigured large buffer cannot collapse a short interval. The cap is
    a safety guardrail; the default is the conservative operating value.

    Clinical basis: accepted on-time windows are ABSOLUTE (ISMP/PQA/NCQA +-2h
    for most oral meds; levodopa +-15-30 min is the tightest). The buffer is an
    AVAILABILITY relaxation only -- it deliberately does not touch the 24h
    strength safety limit (Pill24hLimitExceededSensor) or adherence grading.

    A ``time_window_hours`` of 0 (no window configured) yields 0 buffer.
    """
    raw = entry.options.get(
        "dose_buffer_minutes",
        entry.data.get("dose_buffer_minutes", DOSE_BUFFER_DEFAULT_MIN),
    )
    try:
        raw_f = float(raw)
    except TypeError, ValueError:
        raw_f = float(DOSE_BUFFER_DEFAULT_MIN)
    if time_window_hours <= 0:
        return 0
    cap_minutes = time_window_hours * 60.0 * DOSE_BUFFER_CAP_FRACTION
    return int(min(raw_f, cap_minutes))


def compute_safe_to_take(
    entry: ConfigEntry,
    timestamps: list,
    now,
    tracking_type: str,
) -> int:
    """
    Compute remaining pills safe to take using the unified sliding window.

    Returns ``0`` on Cyclic OFF days.  This is the pure (side-effect-free)
    version of the logic that lived inline in ``next_dose._compute_safe_to_take``.

    The rolling window is relaxed by the anti-drift dose buffer
    (:func:`effective_dose_buffer_minutes`): doses expire ``buffer`` minutes
    earlier than the strict window, so a dose taken a few minutes late still
    allows the next scheduled dose to re-anchor on time (bounds schedule
    creep). The buffer is capped at 25% of the window so it cannot collapse a
    short interval.
    """
    max_pills = entry.options.get("pill_limit", entry.data.get("pill_limit", 1))
    time_window = get_time_window(entry, tracking_type)
    buffer_minutes = effective_dose_buffer_minutes(entry, time_window)
    cutoff = now - timedelta(hours=time_window) + timedelta(minutes=buffer_minutes)
    valid_timestamps = [ts for ts in timestamps if ts >= cutoff]
    safe_to_take = max(0, max_pills - len(valid_timestamps))

    if tracking_type == TRACKING_CYCLIC and not is_on_day(entry, now.date(), now.date()):
        safe_to_take = 0

    return safe_to_take


def local_date(dt: datetime) -> date:
    """Convert a datetime to its local calendar date (tz-safe).

    If ``dt`` is timezone-aware, convert to the local timezone before
    extracting the date so a UTC-stored timestamp maps to the user's
    local calendar day, not the UTC day.  If naive, return ``dt.date()``
    directly (no timezone conversion possible).

    This is the canonical shared copy — previously duplicated as
    ``avg_doses._local_date``, ``drink_master_avg._local_date``, and
    inlined in ``is_day_covered``.  Re-exported via
    ``sensors._tracker_info`` so sensor files can import it from one place.
    """
    if dt.tzinfo is not None:
        return dt.astimezone().date()
    return dt.date()


def is_day_covered(check_date: date, timestamps: list[datetime]) -> bool:
    """
    Return ``True`` when any dose timestamp falls on *check_date* (local calendar day).

    Uses :func:`local_date` for tz-safe local-date conversion so a UTC-stored
    timestamp is compared against the user's local calendar day, not the UTC
    day.  This is the day-level coverage model used by ``avg_doses`` (PDC) and
    ``next_dose`` for cyclic schedules, and by the overdue sensor for
    single-slot Time of Day schedules.

    Args:
        check_date: The local calendar date to test (typically ``now.date()``
            where ``now`` is tz-aware via ``dt_util.now()``).
        timestamps: List of dose datetimes (may be tz-aware or naive).

    Returns:
        True if at least one timestamp's local date equals *check_date*.
    """
    return any(local_date(ts) == check_date for ts in timestamps)
