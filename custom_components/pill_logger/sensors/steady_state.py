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
        self._current_mass = 0.0
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
            async_dispatcher_connect(self.hass, f"concentration_updated_{self._entry_id}", self._update_from_concentration)
        )
        
        # Trigger update when options are updated in the config flow
        self.async_on_remove(
            self.hass.config_entries.async_runtime_dispatcher_connect(
                f"updated_options_{self._entry_id}", self._handle_options_updated
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
    def _handle_options_updated(self, *args, **kwargs):
        self.update_state()

    @callback
    def _update_from_concentration(self, current_mass):
        self._current_mass = current_mass
        self.update_state()

    def update_state(self):
        entry = self.hass.config_entries.async_get_entry(self._entry_id)

        half_life = float(entry.options.get("half_life", entry.data.get("half_life", 0.0)))
        strength = float(entry.options.get("strength", entry.data.get("strength", 0.0)))
        tau = float(entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 24.0)))

        # Must return None instead of "N/A" to satisfy MEASUREMENT state class requirements
        if half_life <= 0 or strength <= 0 or tau <= 0:
            self._attr_native_value = None
            self.async_write_ha_state()
            return

        import math
        k_e = math.log(2) / half_life
        accumulation_factor = 1.0 / (1.0 - math.exp(-k_e * tau))
        c_max_ss = strength * accumulation_factor
        target_ss = c_max_ss * 0.90

        if self._current_mass > c_max_ss * 1.1:
            # Case: Current mass is significantly above the new stable max (dosage reduction)
            # Calculate time to decay down to 90% of the new Cmax_ss
            # C(t) = C_current * exp(-k_e * t) = 0.9 * Cmax_ss
            # t = ln(C_current / (0.9 * Cmax_ss)) / k_e
            t_decay = math.log(self._current_mass / (0.9 * c_max_ss)) / k_e
            self._attr_native_value = round(t_decay / 24.0, 1)
        elif self._current_mass >= target_ss:
            # Within the 90%-110% window of the new steady state
            self._attr_native_value = 0.0
        else:
            # Case: Climbing up to steady state
            # P is the fraction of steady state currently achieved
            p = max(0.0001, self._current_mass / c_max_ss)
            if p >= 0.90:
                 self._attr_native_value = 0.0
            else:
                 # Continuous time equivalent math
                 t_current = -math.log(1.0 - p) / k_e
                 t_90 = -math.log(0.1) / k_e
                 remaining_hours = max(0.0, t_90 - t_current)
                 self._attr_native_value = round(remaining_hours / 24.0, 1)

        self._attr_extra_state_attributes = {
            "theoretical_max_mg": round(c_max_ss, 1),
            "current_percentage": f"{round((self._current_mass / c_max_ss) * 100, 1)}%",
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
