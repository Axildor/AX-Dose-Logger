from datetime import timedelta
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.core import callback
import homeassistant.util.dt as dt_util
from ..const import DOMAIN

class PillSteadyStateSensor(RestoreSensor):
    def __init__(self, entry):
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._attr_name = f"{med_name} Days to Steady State"
        self._attr_unique_id = f"{entry.entry_id}_steady_state"
        self._attr_icon = "mdi:chart-bell-curve"
        self._entry_id = entry.entry_id
        self._attr_suggested_display_precision = 1
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._last_dose_timestamp = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_taken_{self._entry_id}", self._handle_pill_taken)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_reset_{self._entry_id}", self._reset_data)
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._interval_update, timedelta(minutes=20)
            )
        )
        last_state = await self.async_get_last_state()
        if last_state and "last_dose_timestamp" in last_state.attributes:
            try:
                self._last_dose_timestamp = dt_util.parse_datetime(last_state.attributes["last_dose_timestamp"])
            except (ValueError, TypeError):
                pass
        self.update_state()

    @callback
    def _handle_pill_taken(self, *args, **kwargs):
        self._last_dose_timestamp = dt_util.now()
        self.update_state()

    @callback
    def _reset_data(self, *args, **kwargs):
        self._last_dose_timestamp = None
        self.update_state()

    @callback
    def _interval_update(self, now):
        self.update_state()

    def update_state(self):
        now = dt_util.now()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)

        half_life = entry.data.get("half_life", 0.0)
        strength = float(entry.options.get("strength", entry.data.get("strength", 0.0)))
        tau = float(entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 24.0)))

        if half_life <= 0 or strength <= 0 or tau <= 0 or not self._last_dose_timestamp:
            self._attr_native_value = "N/A"
            self.async_write_ha_state()
            return

        elapsed_time = now - self._last_dose_timestamp
        missed_dose = elapsed_time > timedelta(hours=24)
        
        if missed_dose:
             self._attr_native_value = 3.0 * (half_life / 24)
        else:
             time_to_ss_hours = 3.32 * half_life
             history_start = entry.data.get("history_start_date")
             if history_start:
                  try:
                      start_dt = dt_util.parse_datetime(history_start)
                      total_treatment_time = (now - start_dt).total_seconds() / 3600.0
                      if total_treatment_time >= time_to_ss_hours:
                          self._attr_native_value = 0.0
                      else:
                          remaining_hours = time_to_ss_hours - total_treatment_time
                          self._attr_native_value = round(max(0.0, remaining_hours) / 24.0, 1)
                  except (ValueError, TypeError):
                      self._attr_native_value = round(time_to_ss_hours / 24.0, 1)
             else:
                  self._attr_native_value = round(time_to_ss_hours / 24.0, 1)

        self._attr_extra_state_attributes = {
            "last_dose_timestamp": self._last_dose_timestamp.isoformat() if self._last_dose_timestamp else None
        }
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )  

    @property
    def native_value(self):
        return self._attr_native_value
