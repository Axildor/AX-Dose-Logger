from datetime import timedelta
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.core import callback
import homeassistant.util.dt as dt_util
import math
from ..const import DOMAIN, PK_DEFAULTS, get_dose_times

class PillSteadyStateSensor(RestoreSensor):
    _attr_has_entity_name = True
    should_poll = False

    def __init__(self, entry):
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._attr_name = "Days to Steady State"
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
            async_dispatcher_connect(self.hass, f"pill_undone_{self._entry_id}", self._handle_pill_undone)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"concentration_updated_{self._entry_id}", self._update_from_concentration)
        )

        last_state = await self.async_get_last_state()
        if last_state and "last_dose_timestamp" in last_state.attributes:
            try:
                self._last_dose_timestamp = dt_util.parse_datetime(last_state.attributes["last_dose_timestamp"])
            except (ValueError, TypeError):
                pass
        self.update_state()

    @callback
    def _handle_pill_taken(self, timestamp, *args, **kwargs):
        """Handle pill_taken signal with synchronized timestamp payload."""
        self._last_dose_timestamp = timestamp
        self.update_state()

    @callback
    def _handle_pill_undone(self, *args, **kwargs):
        """Handle pill_undone signal: clear last_dose_timestamp.

        The concentration sensor will broadcast a concentration_updated signal
        after recalculating, which will trigger _update_from_concentration.
        We set last_dose_timestamp to None since we don't know the previous
        dose time — the concentration sensor handles the actual PK state.
        """
        self._last_dose_timestamp = None
        self.update_state()

    @callback
    def _reset_data(self, *args, **kwargs):
        self._last_dose_timestamp = None
        self.update_state()

    @callback
    def _update_from_concentration(self, current_mass):
        self._current_mass = current_mass
        self.update_state()

    def update_state(self):
        entry = self.hass.config_entries.async_get_entry(self._entry_id)

        half_life = float(entry.options.get("half_life", entry.data.get("half_life", 0.0)))
        strength = float(entry.options.get("strength", entry.data.get("strength", 0.0)))
        bioavailability = float(entry.options.get("bioavailability", entry.data.get("bioavailability", PK_DEFAULTS["bioavailability"])))

        # Compute tau (dosing interval) based on tracking type
        tracking_type = entry.data.get("tracking_type")
        if tracking_type == "Time of Day":
            # For multi-dose Time of Day, use average interval: 24h / doses_per_day
            parsed_times = get_dose_times(entry)
            doses_per_day = max(1, len(parsed_times))
            tau = 24.0 / doses_per_day
        elif tracking_type == "Regular Interval":
            tau = float(entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 24.0)))
        else:
            # Cyclic and others default to 24h (daily dosing)
            tau = 24.0

        # Apply bioavailability to get effective strength
        F = bioavailability / 100.0
        effective_strength = strength * F

        # Must return None instead of "N/A" to satisfy MEASUREMENT state class requirements
        if half_life <= 0 or strength <= 0 or tau <= 0:
            self._attr_native_value = None
            self.async_write_ha_state()
            return

        k_e = math.log(2) / half_life
        accumulation_factor = 1.0 / (1.0 - math.exp(-k_e * tau))
        c_max_ss = effective_strength * accumulation_factor
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
            if self._current_mass <= 0:
                # Pre-dose calculation: time to reach 90% from zero
                t_90 = -math.log(0.1) / k_e
                self._attr_native_value = round(t_90 / 24.0, 1)
            else:
                # P is the fraction of steady state currently achieved
                p = self._current_mass / c_max_ss
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
