"""Constants for ax_dose_logger."""

import re
from datetime import time as time_sys
from logging import Logger, getLogger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

LOGGER: Logger = getLogger(__package__)

DOMAIN = "ax_dose_logger"
CURRENT_VERSION = 14

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

# --- Per-substance Low band UPPER bound (Moderate -> Low crossing) ---
# Single source of truth shared by DrinkMasterEstimatedLowTimeSensor /
# DrinkMasterLowHoursUntilSensor (sensors/drink_master_sleep_disruption.py)
# and DrinkMasterCoordinator.predict_low_time_if_dose (drink_coordinator.py).
# Caffeine body-mass is in mg; alcohol body-mass is in g.
#
# This is the body-mass level the user crosses DOWN into the Low band from
# above (the Moderate to Low boundary), i.e. the moment "Low is reached".
# It is the UPPER bound of the Low band as defined by the per-substance
# `bands` list in sensors/drink_master_sleep_disruption.py:
#   caffeine Low band spans 11..31 mg -> upper bound (entry from Moderate) is 31 mg
#   alcohol  Low band spans  1..11 g  -> upper bound (entry from Moderate) is 11 g
# The Low band's LOWER bound (Low to None crossing) is tracked separately as
# `none_threshold` in _TRACKER_INFO, NOT here; see estimated_none_time /
# estimated_none_hours in the Low sensors.
DRINK_LOW_THRESHOLD: dict[str, float] = {
    DRINK_TYPE_CAFFEINE: 31.0,
    DRINK_TYPE_ALCOHOL: 11.0,
}

# Drink master store key suffix per substance type
DRINK_MASTER_STORE_KEYS: dict[str, str] = {
    DRINK_TYPE_CAFFEINE: "ax_dose_logger_drink_master_caffeine",
    DRINK_TYPE_ALCOHOL: "ax_dose_logger_drink_master_alcohol",
}

# --- Global PK defaults (Drink Settings singleton) ---
GLOBAL_PK_DEFAULTS: dict[str, float] = {
    "global_caffeine_half_life": 5.0,  # hours
    "global_caffeine_tmax": 0.75,  # hours
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
    start_minutes = 7 * 60  # 07:00
    end_minutes = 21 * 60  # 21:00
    span = end_minutes - start_minutes
    times: list[str] = []
    for i in range(n):
        minutes = start_minutes + int(span * i / (n - 1)) if n > 1 else start_minutes
        hour = minutes // 60
        minute = minutes % 60
        times.append(f"{hour:02d}:{minute:02d}")
    return times


def parse_dose_time(value) -> tuple[int, int]:
    """Parse a dose-time value into a ``(hour, minute)`` tuple.

    Accepts every form the config flow may store:

    * ``datetime.time`` — the actual return type of HA's ``TimeSelector``
      (``cv.time`` parses the frontend string into ``time(h, m, s)``;
      ``cast(str, data)`` in the selector is a typing-only no-op, so a
      ``time`` object is what lands in ``entry.data["dose_time"]``).
    * ``"HH:MM"`` or ``"HH:MM:SS"`` string — the canonical serialized
      form, produced by :func:`_time_to_str` in the config flow and by
      legacy/YAML entries.
    * ``None`` / empty / unparseable — falls back to ``(8, 0)`` to match
      the config-flow schema default (and the prior inline fallbacks).

    Centralizing this here removes 7 duplicated ``try/except .split(":")``
    blocks across ``next_dose``, ``overdue``, ``adherence``, ``calendar``,
    and ``get_dose_times`` — all of which silently fell back to ``(8, 0)``
    when a ``datetime.time`` object lacked ``.split``, causing every
    non-08:00 scheduled dose time to be ignored (bug 3).
    """
    if isinstance(value, time_sys):
        return (value.hour, value.minute)
    if isinstance(value, str) and value:
        try:
            parts = value.split(":")
            return (int(parts[0]), int(parts[1]))
        except ValueError, IndexError:
            return (8, 0)
    return (8, 0)


def get_dose_times(entry: ConfigEntry) -> list[tuple[int, int]]:
    """
    Parse dose_times from config entry, returning sorted list of (hour, minute) pairs.

    Falls back to the legacy time_of_day field for entries that haven't been
    migrated yet, and ultimately defaults to ["08:00"].
    """
    dose_times = entry.options.get("dose_times", entry.data.get("dose_times", None))
    # Legacy fallback: single time_of_day string
    if dose_times is None:
        old_time = entry.options.get("time_of_day", entry.data.get("time_of_day", "08:00"))
        dose_times = [old_time] if old_time else ["08:00"]

    parsed: list[tuple[int, int]] = [parse_dose_time(ts) for ts in dose_times]
    parsed.sort()
    return parsed


def sanitize_key(name: str) -> str:
    """Convert a human-readable metric name into a safe entity key component."""
    return re.sub(r"[^a-z0-9]", "_", name.lower().strip())
