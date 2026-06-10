from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.core import callback
import homeassistant.util.dt as dt_util
from ..const import DOMAIN

class PillLastDoseSensor(RestoreSensor):
    def __init__(self, name, entry_id):
        self._med_name = name
        self._attr_name = f"{name} Last Dose"
        self._attr_unique_id = f"{entry_id}_last_dose"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._entry_id = entry_id
        self._state = None

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
            async_dispatcher_connect(self.hass, f"pill_taken_{self._entry_id}", self._update_last_dose)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_reset_{self._entry_id}", self._reset_data)
        )
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            self._state = last_state.native_value

    @property
    def native_value(self):
        return self._state

    @callback
    def _update_last_dose(self, *args, **kwargs):
        self._state = dt_util.now()
        self.async_write_ha_state()

    @callback
    def _reset_data(self, *args, **kwargs):
        self._state = None
        self.async_write_ha_state()
