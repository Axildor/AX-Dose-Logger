"""Constants for pill_logger."""

import re
from logging import Logger, getLogger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

LOGGER: Logger = getLogger(__package__)

DOMAIN = "pill_logger"
CURRENT_VERSION = 8

STANDARD_EFFECTIVENESS_METRICS: dict[str, str] = {
    "pain": "Pain",
    "mood": "Mood",
    "nausea": "Nausea",
    "fatigue": "Fatigue",
}

EFFECTIVENESS_METRIC_ICONS: dict[str, str] = {
    "pain": "mdi:emoticon-cry",
    "mood": "mdi:emoticon-happy",
    "nausea": "mdi:emoticon-sick",
    "fatigue": "mdi:sleep",
}

DEFAULT_METRIC_ICON = "mdi:chart-line"

RELEASE_TYPES: list[str] = [
    "Instant Release",
    "Sustained Release",
]

PK_DEFAULTS: dict[str, float] = {
    "bioavailability": 100,
    "ir_fraction": 100,
    "zero_order_duration": 0,
    "release_half_life": 0,
    "lag_time": 0,
    "ir_hours_to_peak": 1.0,
}

MAX_DOSES_PER_DAY = 18

DEFAULT_DOSE_TIMES: dict[int, list[str]] = {
    1: ["08:00"],
    2: ["08:00", "20:00"],
    3: ["08:00", "14:00", "20:00"],
    4: ["08:00", "12:00", "16:00", "20:00"],
    5: ["08:00", "11:00", "14:00", "17:00", "20:00"],
    6: ["08:00", "10:00", "12:00", "14:00", "16:00", "20:00"],
}


def generate_default_dose_times(n: int) -> list[str]:
    """Generate n evenly-spaced dose times between 07:00 and 21:00.

    For n <= 6, use the hand-tuned DEFAULT_DOSE_TIMES dict instead.
    """
    if n in DEFAULT_DOSE_TIMES:
        return DEFAULT_DOSE_TIMES[n]
    # Spread n times evenly from 07:00 to 21:00 (14-hour window)
    start_minutes = 7 * 60   # 07:00
    end_minutes = 21 * 60     # 21:00
    span = end_minutes - start_minutes
    times: list[str] = []
    for i in range(n):
        minutes = start_minutes + int(span * i / (n - 1)) if n > 1 else start_minutes
        hour = minutes // 60
        minute = minutes % 60
        times.append(f"{hour:02d}:{minute:02d}")
    return times


def get_dose_times(entry: "ConfigEntry") -> list[tuple[int, int]]:
    """Parse dose_times from config entry, returning sorted list of (hour, minute) pairs.

    Falls back to the legacy time_of_day field for entries that haven't been
    migrated yet, and ultimately defaults to ["08:00"].
    """
    dose_times = entry.options.get(
        "dose_times", entry.data.get("dose_times", None)
    )
    # Legacy fallback: single time_of_day string
    if dose_times is None:
        old_time = entry.options.get(
            "time_of_day", entry.data.get("time_of_day", "08:00")
        )
        dose_times = [old_time] if old_time else ["08:00"]

    parsed: list[tuple[int, int]] = []
    for ts in dose_times:
        try:
            parts = ts.split(":")
            parsed.append((int(parts[0]), int(parts[1])))
        except (ValueError, AttributeError, IndexError):
            parsed.append((8, 0))
    parsed.sort()
    return parsed


def sanitize_key(name: str) -> str:
    """Convert a human-readable metric name into a safe entity key component."""
    return re.sub(r"[^a-z0-9]", "_", name.lower().strip())

STRENGTH_UNITS: list[str] = ["µg", "mg", "g"]
