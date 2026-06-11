from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    med_name = entry.data["medication_name"]
    async_add_entities([
        PillTakeButton(med_name, entry.entry_id),
        PillResetButton(med_name, entry.entry_id)
    ])

class PillTakeButton(ButtonEntity):
    def __init__(self, name, entry_id):
        self._name = name
        self._med_name = name
        self._attr_name = f"Take {name}"
        self._attr_unique_id = f"{entry_id}_take"
        self._attr_icon = "mdi:pill"
        self._entry_id = entry_id

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )

    async def async_press(self):
        """When pressed, send a signal to update the sensor and number entities."""
        async_dispatcher_send(self.hass, f"pill_taken_{self._entry_id}")
        self.hass.bus.async_fire(
            "pill_logger_pill_taken",
            {"entity_id": self.entity_id},
        )

class PillResetButton(ButtonEntity):
    def __init__(self, name, entry_id):
        self._name = name
        self._med_name = name
        self._attr_name = f"Reset {name} History"
        self._attr_unique_id = f"{entry_id}_reset"
        self._attr_icon = "mdi:history"
        self._entry_id = entry_id
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )

    async def async_press(self):
        """When pressed, send a signal to clear the test data."""
        async_dispatcher_send(self.hass, f"pill_reset_{self._entry_id}")
