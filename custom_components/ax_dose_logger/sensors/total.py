from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback

from ..entity import PillLoggerSensorEntity


class PillTotalSensor(PillLoggerSensorEntity, RestoreSensor):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "total"
        self._attr_unique_id = f"{entry.entry_id}_total"
        self._attr_icon = "mdi:chart-line"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Restore last value for smooth UI transition; coordinator is
        # authoritative so we override in _handle_coordinator_update.
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            self._attr_native_value = int(last_state.native_value)

    @property
    def native_value(self):
        """Total doses = length of coordinator dose_history."""
        if self.coordinator.data:
            return len(self.coordinator.data.dose_history)
        return 0

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
