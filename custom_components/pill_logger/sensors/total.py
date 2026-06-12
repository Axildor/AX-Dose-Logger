from homeassistant.components.sensor import RestoreSensor
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.core import callback
from ..const import DOMAIN

class PillTotalSensor(RestoreSensor):
    _attr_has_entity_name = True
    should_poll = False

    def __init__(self, name, entry_id):
        self._med_name = name
        self._attr_name = "Total Doses"
        self._attr_unique_id = f"{entry_id}_total"
        self._attr_icon = "mdi:chart-line"
        self._entry_id = entry_id
        self._state = 0

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_taken_{self._entry_id}", self.increment)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_reset_{self._entry_id}", self.reset_data)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_undone_{self._entry_id}", self.decrement)
        )
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            self._state = int(last_state.native_value)

    @property
    def native_value(self):
        return self._state

    @callback
    def increment(self, *args, **kwargs):
        self._state += 1
        self.async_write_ha_state()

    @callback
    def decrement(self, *args, **kwargs):
        """Decrement total by 1 when a dose is undone (minimum 0)."""
        if self._state > 0:
            self._state -= 1
        self.async_write_ha_state()

    @callback
    def reset_data(self, *args, **kwargs):
        self._state = 0
        self.async_write_ha_state()
