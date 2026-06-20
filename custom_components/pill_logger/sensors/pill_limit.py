from datetime import timedelta
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback
import homeassistant.util.dt as dt_util
from ..const import TRACKING_CYCLIC
from ..entity import PillLoggerSensorEntity
from ..sliding_window import get_time_window, is_on_day

# Cap for timestamps attribute: prune older than 365 days, keep last 100
_TIMESTAMPS_MAX_DAYS = 365
_TIMESTAMPS_MAX_COUNT = 100


class PillLimitSensor(PillLoggerSensorEntity, RestoreSensor):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "pills_safe_to_take"
        self._attr_unique_id = f"{entry.entry_id}_pills_safe_to_take"
        self._attr_icon = "mdi:pill"
        self._tracking_type = entry.data.get("tracking_type")
        self._attr_extra_state_attributes = {"timestamps": []}
        self._attr_native_value = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Legacy restore for smooth UI transition; coordinator is
        # authoritative so _handle_coordinator_update overrides.
        last_state_obj = await self.async_get_last_state()
        if last_state_obj and "timestamps" in last_state_obj.attributes:
            self._update_state()
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator (dose event or 1-min tick)."""
        self._update_state()
        self.async_write_ha_state()

    def _get_timestamps(self) -> list:
        """Read dose timestamps from the coordinator."""
        if self.coordinator.data:
            return [ts for ts, _ in self.coordinator.data.dose_history]
        return []

    def _update_state(self):
        now = dt_util.now()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        max_pills = entry.options.get("pill_limit", entry.data.get("pill_limit", 1))
        time_window = get_time_window(entry, self._tracking_type)
        timestamps = self._get_timestamps()

        # Cyclic OFF days: force pill_limit to 0 regardless of window
        if self._tracking_type == TRACKING_CYCLIC and not is_on_day(entry, now.date(), now.date()):
            self._attr_native_value = 0
            # Prune timestamps to last 365 days and cap at 100 entries
            cutoff = now - timedelta(days=_TIMESTAMPS_MAX_DAYS)
            recent = [ts for ts in timestamps if ts >= cutoff][-_TIMESTAMPS_MAX_COUNT:]
            self._attr_extra_state_attributes = {
                "timestamps": [ts.isoformat() for ts in recent],
                "time_window_hours": time_window,
                "in_on_window": False,
            }
            return

        # Unified sliding window for ALL modes
        cutoff = now - timedelta(hours=time_window)
        valid_timestamps = [ts for ts in timestamps if ts >= cutoff]
        self._attr_native_value = max(0, max_pills - len(valid_timestamps))

        window_expires_at = None
        if max_pills > 0 and len(valid_timestamps) >= max_pills and valid_timestamps:
            window_expires_at = (valid_timestamps[0] + timedelta(hours=time_window)).isoformat()

        self._attr_extra_state_attributes = {
            "timestamps": [ts.isoformat() for ts in valid_timestamps],
            "time_window_hours": time_window,
            "window_expires_at": window_expires_at,
        }