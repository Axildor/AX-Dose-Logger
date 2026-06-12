from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from .const import DOMAIN

PLATFORMS = ["sensor", "button", "number", "calendar"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data
    
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry, with calendar entity cleanup if disabled."""
    enable_calendar = entry.options.get(
        "enable_calendar", entry.data.get("enable_calendar", True)
    )
    if not enable_calendar:
        # Remove the calendar entity from the entity registry to prevent
        # an "unavailable" ghost entity after reload
        ent_reg = er.async_get(hass)
        entity_id = ent_reg.async_get_entity_id(
            "calendar", DOMAIN, f"{entry.entry_id}_calendar"
        )
        if entity_id:
            ent_reg.async_remove(entity_id)
    
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok