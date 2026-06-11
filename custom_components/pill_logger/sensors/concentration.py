from datetime import timedelta
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.core import callback
import homeassistant.util.dt as dt_util
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
import math
from ..const import DOMAIN

class PillConcentrationSensor(RestoreSensor):
    should_poll = False

    def __init__(self, entry):
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._attr_name = f"{med_name} Concentration"
        self._attr_unique_id = f"{entry.entry_id}_concentration"
        self._attr_icon = "mdi:chart-bell-curve"
        self._entry_id = entry.entry_id
        self._strength = entry.options.get("strength", entry.data.get("strength", 0))
        self._half_life = entry.options.get("half_life", entry.data.get("half_life", 0))
        self._hours_to_peak = entry.options.get("hours_to_peak", entry.data.get("hours_to_peak", 0.0))
        self._current_mass = 0.0
        self._gut_mass = 0.0
        self._last_updated = None
        self._ka = 0.0
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "mg"
        self._attr_suggested_display_precision = 1
        self._attr_native_value = 0.0
        self._attr_extra_state_attributes = {"last_updated": None}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_taken_{self._entry_id}", self.handle_pill_taken)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_reset_{self._entry_id}", self.reset_data)
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self.update_decay, timedelta(minutes=2)
            )
        )
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            try:
                old_mass = float(last_state.state)
            except (ValueError, TypeError):
                old_mass = 0.0
            last_ts_str = last_state.attributes.get("last_updated")
            gut_mass = last_state.attributes.get("gut_mass", 0.0)
            if last_ts_str:
                try:
                    last_ts = dt_util.parse_datetime(last_ts_str)
                    now = dt_util.now()
                    elapsed_hours = (now - last_ts).total_seconds() / 3600.0
                    self._gut_mass = gut_mass
                    k_e = math.log(2) / self._half_life if self._half_life > 0 else 0
                    k_a = self._ka if self._ka > 0 else (self._solve_ka(self._hours_to_peak, k_e) if self._hours_to_peak > 0 else 0)
                    if k_e > 0 and k_a > 0 and abs(k_a - k_e) > 0.0001:
                        self._current_mass = (old_mass * math.exp(-k_e * elapsed_hours) +
                                              (gut_mass * k_a / (k_a - k_e)) *
                                              (math.exp(-k_e * elapsed_hours) - math.exp(-k_a * elapsed_hours)))
                        self._gut_mass = gut_mass * math.exp(-k_a * elapsed_hours)
                    elif k_e > 0:
                        self._current_mass = old_mass * math.exp(-k_e * elapsed_hours)
                        self._gut_mass = 0
                    else:
                        self._current_mass = old_mass
                    self._last_updated = last_ts
                except (ValueError, TypeError):
                    self._gut_mass = gut_mass
                    self._current_mass = old_mass
                    self._last_updated = dt_util.now()
            else:
                self._current_mass = old_mass
                self._gut_mass = gut_mass
                self._last_updated = dt_util.now()
        else:
            self._current_mass = 0.0
            self._gut_mass = 0.0
            self._last_updated = dt_util.now()
        self.update_state()
        # Ensure the steady state sensor gets the initial value immediately
        async_dispatcher_send(self.hass, f"concentration_updated_{self._entry_id}", self._current_mass)

    @callback
    def handle_pill_taken(self, *args, **kwargs):
        now = dt_util.now()

        # Calculate elapsed time and decay current masses
        if self._last_updated:
            elapsed_hours = (now - self._last_updated).total_seconds() / 3600.0
            if elapsed_hours > 0:
                # Decay both gut and body compartments before adding new dose
                k_e = math.log(2) / self._half_life if self._half_life > 0 else 0
                k_a = getattr(self, '_ka', 0)
                if k_a == 0 and self._hours_to_peak > 0:
                    # Calculate ka from hours_to_peak if not already set
                    k_a = self._solve_ka(self._hours_to_peak, k_e)

                if k_a > 0 and k_a != k_e:
                    # Two-compartment decay: G and C both decay
                    old_gut = self._gut_mass
                    old_body = self._current_mass
                    self._gut_mass = old_gut * math.exp(-k_a * elapsed_hours)
                    self._current_mass = (old_body * math.exp(-k_e * elapsed_hours) +
                                          (old_gut * k_a / (k_a - k_e)) *
                                          (math.exp(-k_e * elapsed_hours) - math.exp(-k_a * elapsed_hours)))
                elif k_a > 0 and abs(k_a - k_e) < 0.0001:
                    # Limiting case when ka ≈ ke
                    old_gut = self._gut_mass
                    old_body = self._current_mass
                    self._gut_mass = old_gut * math.exp(-k_a * elapsed_hours)
                    self._current_mass = old_body * math.exp(-k_e * elapsed_hours) + old_gut * k_a * elapsed_hours * math.exp(-k_a * elapsed_hours)
                else:
                    # No absorption (ka = 0): just eliminate from body
                    self._gut_mass = 0
                    if self._half_life > 0:
                        self._current_mass *= math.exp(-k_e * elapsed_hours)

        # Calculate elimination rate constant
        k_e = math.log(2) / self._half_life if self._half_life > 0 else 0

        # Calculate absorption rate constant if needed
        if self._hours_to_peak > 0:
            self._ka = self._solve_ka(self._hours_to_peak, k_e)
            # Add dose to gut compartment (absorption phase)
            self._gut_mass += float(self._strength)
        else:
            # No absorption: add dose directly to body (immediate release)
            self._ka = 0.0
            self._current_mass += float(self._strength)

        self._last_updated = now
        self.update_state()
        self.async_write_ha_state()

    @callback
    def reset_data(self, *args, **kwargs):
        self._current_mass = 0.0
        self._gut_mass = 0.0
        self._last_updated = None
        self.update_state()

    @callback
    def update_state(self):
        self._attr_native_value = round(self._current_mass, 1)
        self._attr_extra_state_attributes = {
            "last_updated": self._last_updated.isoformat() if self._last_updated else None,
            "gut_mass": round(self._gut_mass, 1),
            "ka": self._ka
        }
        self.async_write_ha_state()
        # Broadcast the live concentration so Steady State can instantly recalculate
        async_dispatcher_send(self.hass, f"concentration_updated_{self._entry_id}", self._current_mass)

    @callback
    def update_decay(self, now):
        if not self._last_updated or self._half_life <= 0:
            return
        now = dt_util.now()
        elapsed_hours = (now - self._last_updated).total_seconds() / 3600.0
        k_e = math.log(2) / self._half_life
        k_a = getattr(self, '_ka', 0)
        if k_a == 0 and self._hours_to_peak > 0:
            k_a = self._solve_ka(self._hours_to_peak, k_e)
        if k_a == k_e and k_a != 0:
            k_a *= 1.0001
        if self._hours_to_peak <= 0:
            self._gut_mass = 0
            self._current_mass *= math.exp(-k_e * elapsed_hours)
        else:
            new_gut = self._gut_mass * math.exp(-k_a * elapsed_hours)
            new_body = (self._current_mass * math.exp(-k_e * elapsed_hours) +
                        (self._gut_mass * k_a / (k_a - k_e)) *
                        (math.exp(-k_e * elapsed_hours) - math.exp(-k_a * elapsed_hours)))
            self._gut_mass = new_gut
            self._current_mass = new_body
        self._last_updated = now
        self.update_state()

    def _solve_ka(self, t_max, k_e):
        low, high = 0.0001, 20.0
        for _ in range(50):
            mid_ka = (low + high) / 2
            if mid_ka == k_e: mid_ka += 0.0001
            try:
                calc_t_max = (math.log(mid_ka) - math.log(k_e)) / (mid_ka - k_e)
                if calc_t_max < t_max: low = mid_ka
                else: high = mid_ka
            except (ValueError, ZeroDivisionError):
                low = mid_ka
        return low

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )
