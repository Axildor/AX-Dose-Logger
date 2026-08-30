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
    DEFAULT_PROFILE_ID,
    DOMAIN,
    DRINK_TYPE_ALCOHOL,
    DRINK_TYPE_CAFFEINE,
    tracker_id_for,
)
from ..sliding_window import local_date

__all__ = [
    "MASTER_TRACKERS",
    "local_date",
    "tracker_device_info",
    "tracker_substance",
]

# Per-substance common metadata for the Master Tracker virtual devices.
# The unit is substance-level (mg for caffeine, g for alcohol); the
# tracker_id + device_name are now profile-scoped (computed per-profile
# via ``tracker_id_for`` + the profile_name display string) so multiple
# profiles get distinct Master Tracker devices.  The legacy ``default``
# profile keeps the un-profiled ids (backwards compatibility).
MASTER_TRACKERS: dict[str, dict[str, str]] = {
    DRINK_TYPE_CAFFEINE: {
        "device_name": "Caffeine Tracker",
        "unit": "mg",
    },
    DRINK_TYPE_ALCOHOL: {
        "device_name": "Alcohol Tracker",
        "unit": "g",
    },
}


def tracker_device_info(
    profile_id: str,
    substance: str,
    *,
    with_name: bool = False,
    profile_name: str | None = None,
) -> DeviceInfo:
    """Build the ``DeviceInfo`` for a profile's Master Tracker virtual device.

    The device ``identifiers`` use the profile-scoped ``tracker_id_for``
    (immutable profile id + substance) so multiple profiles get distinct
    devices and the legacy ``default`` profile keeps its un-profiled id.
    The device ``name`` is the profile display name + the substance device
    name (e.g. "Alice Caffeine Tracker"); for the ``default`` profile with
    no profile_name it stays the legacy substance-only name ("Caffeine
    Tracker").  Only the namesake ``DrinkMasterSensor`` (``has_entity_name
    = False``) needs the device ``name``; the other sensors set
    ``has_entity_name = True`` so HA derives the name from the device.
    """
    info = MASTER_TRACKERS[substance]
    tracker_id = tracker_id_for(profile_id, substance)
    kwargs: dict = {
        "identifiers": {(DOMAIN, tracker_id)},
        "manufacturer": "AX Dose Logger",
        "model": "Master Tracker",
    }
    if with_name:
        if profile_name:
            kwargs["name"] = f"{profile_name} {info['device_name']}"
        else:
            kwargs["name"] = info["device_name"]
    return DeviceInfo(**kwargs)


def tracker_substance(tracker_id: str) -> tuple[str, str] | None:
    """Return ``(profile_id, substance)`` for a Master Tracker device id, or None.

    The tracker_id is profile-scoped (``{substance}_tracker`` for the
    legacy default profile, ``{substance}_tracker_{profile_id}`` for named
    profiles).  This parses the id back into its components so the REST
    history endpoint can route to the right per-profile master store.

    Used by the REST history endpoint to detect Master Tracker devices by
    their stable ``identifiers`` value and route to the aggregated master
    store for that (profile, substance) instead of the per-entry store.
    """
    for substance in (DRINK_TYPE_CAFFEINE, DRINK_TYPE_ALCOHOL):
        legacy = f"{substance}_tracker"
        if tracker_id == legacy:
            return (DEFAULT_PROFILE_ID, substance)
        prefix = f"{substance}_tracker_"
        if tracker_id.startswith(prefix):
            profile_id = tracker_id[len(prefix) :]
            return (profile_id, substance)
    return None
