"""Constants for ax_dose_logger."""

import re
from logging import Logger, getLogger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

LOGGER: Logger = getLogger(__package__)

DOMAIN = "ax_dose_logger"
CURRENT_VERSION = 13

# --- Tracking type constants ---
TRACKING_REGULAR_INTERVAL = "regular_interval"
TRACKING_TIME_OF_DAY = "time_of_day"
TRACKING_AS_NEEDED = "as_needed"
TRACKING_CYCLIC = "cyclic"

TRACKING_TYPES: list[str] = [
    TRACKING_REGULAR_INTERVAL,
    TRACKING_TIME_OF_DAY,
    TRACKING_AS_NEEDED,
    TRACKING_CYCLIC,
]

# --- Release type constants ---
RELEASE_INSTANT = "instant_release"
RELEASE_SUSTAINED = "sustained_release"

RELEASE_TYPES: list[str] = [
    RELEASE_INSTANT,
    RELEASE_SUSTAINED,
]

# --- Device category constants (config flow router) ---
DEVICE_CATEGORY_MEDICINE = "medicine"
DEVICE_CATEGORY_DRINKS = "drinks"
DEVICE_CATEGORY_DRINK_SETTINGS = "drink_settings"

DEVICE_CATEGORIES: list[str] = [
    DEVICE_CATEGORY_MEDICINE,
    DEVICE_CATEGORY_DRINKS,
]
# NOTE: DEVICE_CATEGORY_DRINK_SETTINGS is intentionally NOT in this list.
# The Drink Settings singleton is auto-created by `_ensure_drink_settings_entry`
# in __init__.py the first time a drink device is set up. It is never a
# user-selectable config-flow device category -- the user edits its global
# constants via the options flow (Configure button). The constant is kept
# because __init__.py / sensor.py / config_flow.py route on it.

# --- Drink type constants ---
DRINK_TYPE_CAFFEINE = "caffeine"
DRINK_TYPE_ALCOHOL = "alcohol"

DRINK_TYPES: list[str] = [
    DRINK_TYPE_CAFFEINE,
    DRINK_TYPE_ALCOHOL,
]

# --- Substance tracker identifiers (stable across Drink Settings recreations) ---
CAFFEINE_TRACKER_ID = "caffeine_tracker"
ALCOHOL_TRACKER_ID = "alcohol_tracker"

# Drink master store key suffix per substance type
DRINK_MASTER_STORE_KEYS: dict[str, str] = {
    DRINK_TYPE_CAFFEINE: "ax_dose_logger_drink_master_caffeine",
    DRINK_TYPE_ALCOHOL: "ax_dose_logger_drink_master_alcohol",
}

# --- Global PK defaults (Drink Settings singleton) ---
GLOBAL_PK_DEFAULTS: dict[str, float] = {
    "global_caffeine_half_life": 5.0,        # hours
    "global_caffeine_tmax": 0.75,            # hours
    "global_alcohol_elimination_rate": 8.0,  # g/h
}

# --- Daily intake limits (24-hour window sensors) ---
# Caffeine: FDA recommends <=400 mg/day for healthy adults.
# User-overridable per-substance via Drink Settings.
CAFFEINE_DEFAULT_LIMIT_MG = 400
# Alcohol: no FDA limit. Default 0 = no limit (user can set in grams ethanol).
ALCOHOL_DEFAULT_LIMIT_G = 0

# --- Strength unit constants ---
# Micrograms use HA's canonical UnitOfMass.MICROGRAMS symbol ("μg", Greek mu
# U+03BC + g). The earlier "mcg" value failed SensorDeviceClass.WEIGHT unit
# validation in HA core (set(UnitOfMass) accepts only "μg"/"mg"/"g"/...).
# v13 migration converts any stored "mcg" to "μg".
STRENGTH_UNIT_MCG = "μg"
STRENGTH_UNIT_MG = "mg"
STRENGTH_UNIT_G = "g"

STRENGTH_UNITS: list[str] = [STRENGTH_UNIT_MCG, STRENGTH_UNIT_MG, STRENGTH_UNIT_G]

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

# --- Daily-locked metric constants ---
METRIC_SLIDER_DEFAULT = 0  # Slider UI position default (leftmost/neutral)
METRIC_STORE_KEY = "ax_dose_logger_metrics"  # Separate storage key for daily metric values

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
    """
    Generate n evenly-spaced dose times between 07:00 and 21:00.

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


def get_dose_times(entry: ConfigEntry) -> list[tuple[int, int]]:
    """
    Parse dose_times from config entry, returning sorted list of (hour, minute) pairs.

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
