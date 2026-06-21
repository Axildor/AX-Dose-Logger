import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback

from ..entity import PillLoggerSensorEntity


class PillDaysSinceFirstDoseSensor(PillLoggerSensorEntity, RestoreSensor):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "days_since_first_dose"
        self._attr_unique_id = f"{entry.entry_id}_days_since_first_dose"
        self._attr_icon = "mdi:calendar-start"
        self._attr_native_value = 0

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Legacy restore for smooth UI transition; coordinator overrides.
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            try:
                self._attr_native_value = int(float(last_state.state))
            except (ValueError, TypeError):
                pass

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Handle updated data from the coordinator.

        Computes days since the earliest dose in coordinator dose_history.
        """
        if self.coordinator.data and self.coordinator.data.dose_history:
            earliest = min(ts for ts, _ in self.coordinator.data.dose_history)
            now = dt_util.now()
            self._attr_native_value = max(0, (now.date() - earliest.date()).days)
        else:
            self._attr_native_value = 0
        self.async_write_ha_state()
