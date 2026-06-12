from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.core import callback
import homeassistant.util.dt as dt_util
from ..const import DOMAIN

class PillLastDoseSensor(RestoreSensor):
    _attr_has_entity_name = True
    should_poll = False

    def __init__(self, name, entry_id):
        self._med_name = name
        self._attr_name = "Last Dose"
        self._attr_unique_id = f"{entry_id}_last_dose"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._entry_id = entry_id
        self._timestamps = []
        self._attr_extra_state_attributes = {"timestamps": []}
        self._attr_native_value = None

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
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_undone_{self._entry_id}", self._undo_last_dose)
        )

        last_state_obj = await self.async_get_last_state()
        if last_state_obj and "timestamps" in last_state_obj.attributes:
            saved_timestamps = last_state_obj.attributes["timestamps"]
            for ts_str in saved_timestamps:
                dt = dt_util.parse_datetime(ts_str)
                if dt:
                    self._timestamps.append(dt)
            self._update_native_value()
            self.async_write_ha_state()
        elif last_state_obj and last_state_obj.state not in (None, "unknown", "unavailable"):
            # Legacy restore: single timestamp state
            parsed = dt_util.parse_datetime(last_state_obj.state)
            if parsed:
                self._timestamps.append(parsed)
            self._update_native_value()
            self.async_write_ha_state()

    def _update_native_value(self):
        """Set native_value to the last timestamp or None."""
        if self._timestamps:
            self._attr_native_value = self._timestamps[-1]
        else:
            self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "timestamps": [ts.isoformat() for ts in self._timestamps],
        }

    @callback
    def _update_last_dose(self, timestamp, *args, **kwargs):
        """Handle pill_taken signal with synchronized timestamp payload."""
        self._timestamps.append(timestamp)
        self._update_native_value()
        self.async_write_ha_state()

    @callback
    def _undo_last_dose(self, *args, **kwargs):
        """Handle pill_undone signal: remove the most recent timestamp."""
        if self._timestamps:
            self._timestamps.pop()
        self._update_native_value()
        self.async_write_ha_state()

    @callback
    def _reset_data(self, *args, **kwargs):
        self._timestamps = []
        self._update_native_value()
        self.async_write_ha_state()
