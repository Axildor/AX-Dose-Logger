from datetime import timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass
from homeassistant.core import callback

from ..entity import PillLoggerSensorEntity

# Cap for timestamps attribute: prune older than 365 days, keep last 100
_TIMESTAMPS_MAX_DAYS = 365
_TIMESTAMPS_MAX_COUNT = 100


class PillLastDoseSensor(PillLoggerSensorEntity, RestoreSensor):
    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "last_dose"
        self._attr_unique_id = f"{entry.entry_id}_last_dose"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_native_value = None
        self._attr_extra_state_attributes = {"timestamps": []}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Legacy restore for smooth UI transition; coordinator is
        # authoritative so we override in _handle_coordinator_update.
        last_state_obj = await self.async_get_last_state()
        if last_state_obj and last_state_obj.state not in (None, "unknown", "unavailable"):
            parsed = dt_util.parse_datetime(last_state_obj.state)
            if parsed:
                self._attr_native_value = parsed

    @property
    def native_value(self):
        """Last dose timestamp from coordinator dose_history."""
        if self.coordinator.data and self.coordinator.data.dose_history:
            return self.coordinator.data.dose_history[-1][0]
        return None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.data:
            history = self.coordinator.data.dose_history
            if history:
                self._attr_native_value = history[-1][0]
                # Prune timestamps to last 365 days and cap at 100 entries
                now = dt_util.now()
                cutoff = now - timedelta(days=_TIMESTAMPS_MAX_DAYS)
                recent = [ts for ts, _ in history if ts >= cutoff][-_TIMESTAMPS_MAX_COUNT:]
                self._attr_extra_state_attributes = {
                    "timestamps": [ts.isoformat() for ts in recent],
                }
            else:
                self._attr_native_value = None
                self._attr_extra_state_attributes = {"timestamps": []}
        self.async_write_ha_state()
