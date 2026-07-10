"""Shared Master Tracker metadata registry + helpers.

Centralizes the per-substance metadata that was previously duplicated across
six sensor files as ``_TRACKER_INFO`` / ``_MASTER_TRACKER_INFO`` dicts.  The
common keys (``tracker_id``, ``device_name``, ``unit``) live here; each sensor
keeps its own small ``_SENSOR_INFO`` dict for genuinely sensor-specific keys
(``unique_id`` stem, ``translation_key``, ``icon``, ``bands``, etc.).

Adding a third substance in the future requires editing only this registry
instead of eight places in lockstep.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from ..const import (
    ALCOHOL_TRACKER_ID,
    CAFFEINE_TRACKER_ID,
    DOMAIN,
    DRINK_TYPE_ALCOHOL,
    DRINK_TYPE_CAFFEINE,
)
from ..sliding_window import local_date

__all__ = [
    "MASTER_TRACKERS",
    "local_date",
    "tracker_device_info",
    "tracker_substance",
]

# Per-substance common metadata for the Master Tracker virtual devices
# (Caffeine Tracker / Alcohol Tracker).  Created by the Drink Settings
# singleton; stable identifiers survive Drink Settings entry recreation.
MASTER_TRACKERS: dict[str, dict[str, str]] = {
    DRINK_TYPE_CAFFEINE: {
        "tracker_id": CAFFEINE_TRACKER_ID,
        "device_name": "Caffeine Tracker",
        "unit": "mg",
    },
    DRINK_TYPE_ALCOHOL: {
        "tracker_id": ALCOHOL_TRACKER_ID,
        "device_name": "Alcohol Tracker",
        "unit": "g",
    },
}

# Inverse lookup: tracker_id -> substance (replaces views.py _TRACKER_SUBSTANCE).
_TRACKER_ID_TO_SUBSTANCE: dict[str, str] = {
    info["tracker_id"]: substance for substance, info in MASTER_TRACKERS.items()
}


def tracker_device_info(substance: str, *, with_name: bool = False) -> DeviceInfo:
    """Build the ``DeviceInfo`` for a Master Tracker virtual device.

    All Master Tracker sensors share the same ``identifiers`` / ``manufacturer``
    / ``model``.  Only the namesake ``DrinkMasterSensor`` (``has_entity_name =
    False``) needs the device ``name``; the other sensors set
    ``has_entity_name = True`` so HA derives the name from the device.
    """
    info = MASTER_TRACKERS[substance]
    kwargs: dict = {
        "identifiers": {(DOMAIN, info["tracker_id"])},
        "manufacturer": "AX Dose Logger",
        "model": "Master Tracker",
    }
    if with_name:
        kwargs["name"] = info["device_name"]
    return DeviceInfo(**kwargs)


def tracker_substance(tracker_id: str) -> str | None:
    """Return the substance for a Master Tracker device identifier, or None.

    Used by the REST history endpoint to detect Master Tracker devices by
    their stable ``identifiers`` value (e.g. ``(DOMAIN, "caffeine_tracker")``)
    and route to the aggregated master store instead of the per-entry store.
    """
    return _TRACKER_ID_TO_SUBSTANCE.get(tracker_id)
