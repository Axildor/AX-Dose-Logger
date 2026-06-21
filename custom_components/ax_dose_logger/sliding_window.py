"""
Shared sliding-window and cyclic ON/OFF helpers for Pill Logger.

These pure functions were extracted from duplicated inline copies in
``pill_limit.py``, ``next_dose.py``, ``adherence.py``, ``avg_doses.py``,
and ``calendar.py`` (Batch 4A of the backend technical audit).
"""

from datetime import date, timedelta

from homeassistant.config_entries import ConfigEntry

from .const import TRACKING_AS_NEEDED, TRACKING_CYCLIC, TRACKING_REGULAR_INTERVAL


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
    days_on = entry.options.get("days_on", entry.data.get("days_on", 5))
    days_off = entry.options.get("days_off", entry.data.get("days_off", 2))
    anchor_str = entry.options.get(
        "cycle_anchor_date", entry.data.get("cycle_anchor_date")
    )

    try:
        anchor_date = date.fromisoformat(anchor_str)
    except (ValueError, TypeError):
        anchor_date = fallback_date if fallback_date is not None else check_date

    cycle_length = days_on + days_off
    if cycle_length <= 0:
        cycle_length = 1

    days_since_anchor = (check_date - anchor_date).days
    position_in_cycle = days_since_anchor % cycle_length
    return position_in_cycle < days_on


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
    """
    max_pills = entry.options.get("pill_limit", entry.data.get("pill_limit", 1))
    time_window = get_time_window(entry, tracking_type)
    cutoff = now - timedelta(hours=time_window)
    valid_timestamps = [ts for ts in timestamps if ts >= cutoff]
    safe_to_take = max(0, max_pills - len(valid_timestamps))

    if tracking_type == TRACKING_CYCLIC and not is_on_day(entry, now.date(), now.date()):
        safe_to_take = 0

    return safe_to_take
