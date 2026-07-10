import math

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback

from ..const import PK_DEFAULTS, get_dose_times
from ..entity import AxDoseLoggerSensorEntity


class PillSteadyStateSensor(AxDoseLoggerSensorEntity, RestoreSensor):
    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "days_to_steady_state"
        self._attr_unique_id = f"{entry.entry_id}_steady_state"
        self._attr_icon = "mdi:chart-bell-curve"
        self._attr_suggested_display_precision = 1
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._last_dose_timestamp = None
        self._current_mass = 0.0
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Legacy restore for smooth UI transition; coordinator overrides.
        last_state = await self.async_get_last_state()
        if last_state:
            if "last_dose_timestamp" in last_state.attributes:
                try:
                    self._last_dose_timestamp = dt_util.parse_datetime(last_state.attributes["last_dose_timestamp"])
                except ValueError, TypeError:
                    pass
            # Restore _current_mass so update_state() produces correct
            # values before the first coordinator refresh completes.
            if "current_mass" in last_state.attributes:
                try:
                    self._current_mass = float(last_state.attributes["current_mass"])
                except ValueError, TypeError:
                    pass

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Handle updated data from the coordinator.

        Reads ``concentration`` and ``last_dose_time`` directly from
        coordinator data — no more ``concentration_updated`` signal.
        """
        if self.coordinator.data:
            self._current_mass = self.coordinator.data.concentration
            self._last_dose_timestamp = self.coordinator.data.last_dose_time
        self.update_state()

    def update_state(self):
        entry = self.hass.config_entries.async_get_entry(self._entry_id)

        half_life = float(entry.options.get("half_life", entry.data.get("half_life", 0.0)))
        strength = float(entry.options.get("strength", entry.data.get("strength", 0.0)))
        bioavailability = float(
            entry.options.get("bioavailability", entry.data.get("bioavailability", PK_DEFAULTS["bioavailability"]))
        )

        # Compute tau (dosing interval) based on tracking type
        from ..const import TRACKING_REGULAR_INTERVAL, TRACKING_TIME_OF_DAY

        tracking_type = entry.data.get("tracking_type")
        if tracking_type == TRACKING_TIME_OF_DAY:
            parsed_times = get_dose_times(entry)
            doses_per_day = max(1, len(parsed_times))
            tau = 24.0 / doses_per_day
        elif tracking_type == TRACKING_REGULAR_INTERVAL:
            tau = float(entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 24.0)))
        else:
            tau = 24.0

        F = bioavailability / 100.0
        effective_strength = strength * F

        if half_life <= 0 or strength <= 0 or tau <= 0:
            self._attr_native_value = None
            self.async_write_ha_state()
            return

        k_e = math.log(2) / half_life
        accumulation_factor = 1.0 / (1.0 - math.exp(-k_e * tau))
        c_max_ss = effective_strength * accumulation_factor
        target_ss = c_max_ss * 0.90

        if self._current_mass > c_max_ss * 1.1:
            t_decay = math.log(self._current_mass / (0.9 * c_max_ss)) / k_e
            self._attr_native_value = round(t_decay / 24.0, 1)
        elif self._current_mass >= target_ss:
            self._attr_native_value = 0.0
        elif self._current_mass <= 0:
            t_90 = -math.log(0.1) / k_e
            self._attr_native_value = round(t_90 / 24.0, 1)
        else:
            p = self._current_mass / c_max_ss
            if p >= 0.90:
                self._attr_native_value = 0.0
            else:
                t_current = -math.log(1.0 - p) / k_e
                t_90 = -math.log(0.1) / k_e
                remaining_hours = max(0.0, t_90 - t_current)
                self._attr_native_value = round(remaining_hours / 24.0, 1)

        self._attr_extra_state_attributes = {
            "theoretical_max_mg": round(c_max_ss, 1),
            "current_mass": round(self._current_mass, 2),
            "current_percentage": round((self._current_mass / c_max_ss) * 100, 1),
            "last_dose_timestamp": self._last_dose_timestamp.isoformat() if self._last_dose_timestamp else None,
        }
        self.async_write_ha_state()
