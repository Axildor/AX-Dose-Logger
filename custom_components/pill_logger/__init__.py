import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "button", "number", "calendar"]


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry to new version."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    new_data = {**config_entry.data}
    new_options = {**config_entry.options}

    if config_entry.version == 2:
        # Version 3 added ER pharmacokinetics fields
        new_data.setdefault("release_type", "Instant Release")
        new_data.setdefault("bioavailability", 100)
        new_data.setdefault("ir_fraction", 100)
        new_data.setdefault("zero_order_duration", 0)
        new_data.setdefault("release_half_life", 0)
        # Also ensure these exist in options (user may have partially saved)
        new_options.setdefault("bioavailability", 100)
        new_options.setdefault("ir_fraction", 100)
        new_options.setdefault("zero_order_duration", 0)
        new_options.setdefault("release_half_life", 0)

    if config_entry.version <= 3:
        # Version 4 added lag_time
        new_data.setdefault("lag_time", 0)
        new_options.setdefault("lag_time", 0)

    if config_entry.version <= 4:
        # Version 5: Convert time_of_day string to dose_times list
        # Old format: time_of_day = "08:00"
        # New format: dose_times = ["08:00"], doses_per_day = 1
        old_time = new_data.pop("time_of_day", None)
        if old_time:
            new_data["dose_times"] = [old_time]
            new_data["doses_per_day"] = 1
        else:
            new_data.setdefault("dose_times", ["08:00"])
            new_data.setdefault("doses_per_day", 1)

        old_time_opt = new_options.pop("time_of_day", None)
        if old_time_opt:
            new_options["dose_times"] = [old_time_opt]
            new_options["doses_per_day"] = 1
        else:
            new_options.setdefault("dose_times", ["08:00"])
            new_options.setdefault("doses_per_day", 1)

    if config_entry.version <= 5:
        # Version 6: Rename safe_doses → pill_limit
        if "safe_doses" in new_data:
            new_data["pill_limit"] = new_data.pop("safe_doses")
        if "safe_doses" in new_options:
            new_options["pill_limit"] = new_options.pop("safe_doses")

    if config_entry.version <= 6:
        # Version 7: Force calendar and adherence off for As Needed entries
        if new_data.get("tracking_type") == "As Needed":
            new_data["enable_calendar"] = False
            new_data["enable_adherence"] = False
            new_options["enable_calendar"] = False
            new_options["enable_adherence"] = False

    hass.config_entries.async_update_entry(
        config_entry, data=new_data, options=new_options, version=7
    )

    _LOGGER.info(
        "Migration to version %s successful for %s",
        7, config_entry.title,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data
    
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry, with entity cleanup for disabled features."""
    ent_reg = er.async_get(hass)

    enable_calendar = entry.options.get(
        "enable_calendar", entry.data.get("enable_calendar", True)
    )
    if not enable_calendar:
        # Remove the calendar entity from the entity registry to prevent
        # an "unavailable" ghost entity after reload
        entity_id = ent_reg.async_get_entity_id(
            "calendar", DOMAIN, f"{entry.entry_id}_calendar"
        )
        if entity_id:
            ent_reg.async_remove(entity_id)

    # Steady state, calendar, and adherence are only meaningful for scheduled
    # medications. Remove their entities for As Needed entries to prevent
    # ghost "unavailable" entities.
    tracking_type = entry.data.get("tracking_type")
    if tracking_type == "As Needed":
        ss_entity_id = ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_steady_state"
        )
        if ss_entity_id:
            ent_reg.async_remove(ss_entity_id)

        # Remove calendar entity (no future events for PRN)
        cal_entity_id = ent_reg.async_get_entity_id(
            "calendar", DOMAIN, f"{entry.entry_id}_calendar"
        )
        if cal_entity_id:
            ent_reg.async_remove(cal_entity_id)

        # Remove adherence entities (undefined for PRN)
        for window in (7, 14, 30, 365):
            adh_entity_id = ent_reg.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_adherence_{window}"
            )
            if adh_entity_id:
                ent_reg.async_remove(adh_entity_id)

    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok