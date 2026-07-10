"""
Shared next-dose-time scheduling helpers for Pill Logger.

Extracted from duplicated inline copies in ``adherence.py`` (Batch 4A of
the backend technical audit).  Only the *Regular Interval* and *Time of
Day* branches are shared here; the *Cyclic/Calendar Pattern* branch is
kept inline in each caller because ``next_dose.py`` and ``adherence.py``
use different algorithms for it.
"""

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry

from .const import TRACKING_REGULAR_INTERVAL, TRACKING_TIME_OF_DAY, get_dose_times


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
            return timestamps[-1] + timedelta(hours=hours_between)
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
