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
        self._attr_name = f"{med_name} Amount in body"
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
        self._dose_history = []  # List of (datetime, float) tuples
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "mg"
        self._attr_suggested_display_precision = 1
        self._attr_native_value = 0.0
        self._attr_extra_state_attributes = {"last_updated": None, "gut_mass": 0.0, "ka": 0.0, "dose_history": []}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_taken_{self._entry_id}", self.handle_pill_taken)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_reset_{self._entry_id}", self.reset_data)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_undone_{self._entry_id}", self.handle_pill_undone)
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self.update_decay, timedelta(minutes=2)
            )
        )

        # Reload PK parameters from current config entry (options with data fallback)
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry:
            self._strength = entry.options.get("strength", entry.data.get("strength", 0))
            self._half_life = entry.options.get("half_life", entry.data.get("half_life", 0))
            self._hours_to_peak = entry.options.get("hours_to_peak", entry.data.get("hours_to_peak", 0.0))

        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            # Restore dose history from attributes (preferred method)
            dose_history_restored = False
            if "dose_history" in last_state.attributes and last_state.attributes["dose_history"]:
                for item in last_state.attributes["dose_history"]:
                    try:
                        ts_str, strength_val = item
                        dt = dt_util.parse_datetime(ts_str)
                        if dt:
                            self._dose_history.append((dt, float(strength_val)))
                            dose_history_restored = True
                    except (ValueError, TypeError, IndexError):
                        continue

            if dose_history_restored:
                # Recalculate from full dose history — mathematically exact
                self._recalculate_from_history()
            else:
                # Legacy restore: rebuild from saved mass values and timestamp
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
                self.update_state()
                # Broadcast initial value to steady state sensor
                async_dispatcher_send(self.hass, f"concentration_updated_{self._entry_id}", self._current_mass)
        else:
            self._current_mass = 0.0
            self._gut_mass = 0.0
            self._last_updated = dt_util.now()
            self.update_state()
            # Broadcast initial value to steady state sensor
            async_dispatcher_send(self.hass, f"concentration_updated_{self._entry_id}", self._current_mass)

    @callback
    def handle_pill_taken(self, timestamp, *args, **kwargs):
        """Handle pill_taken signal with synchronized timestamp payload."""
        now = timestamp  # Use the synchronized timestamp from the signal

        # Reload PK parameters in case they changed
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry:
            self._strength = entry.options.get("strength", entry.data.get("strength", 0))
            self._half_life = entry.options.get("half_life", entry.data.get("half_life", 0))
            self._hours_to_peak = entry.options.get("hours_to_peak", entry.data.get("hours_to_peak", 0.0))

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
        strength = float(self._strength)
        if self._hours_to_peak > 0:
            self._ka = self._solve_ka(self._hours_to_peak, k_e)
            # Add dose to gut compartment (absorption phase)
            self._gut_mass += strength
        else:
            # No absorption: add dose directly to body (immediate release)
            self._ka = 0.0
            self._current_mass += strength

        # Record dose in history
        self._dose_history.append((now, strength))

        self._last_updated = now
        self.update_state()

    @callback
    def handle_pill_undone(self, *args, **kwargs):
        """Handle pill_undone signal: remove last dose and recalculate from history."""
        if not self._dose_history:
            return
        self._dose_history.pop()
        self._recalculate_from_history()

    def _recalculate_from_history(self):
        """Recalculate gut_mass and current_mass from full dose history using superposition.

        Since the two-compartment PK model is linear, the total drug in each compartment
        at any time equals the sum of each individual dose's contribution. This method
        resets both compartments to zero and iterates through all recorded doses,
        computing each dose's contribution at the current time using the Bateman equation.
        This is mathematically exact and eliminates floating-point drift.
        """
        now = dt_util.now()

        # Reload PK parameters in case they changed
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry:
            self._strength = entry.options.get("strength", entry.data.get("strength", 0))
            self._half_life = entry.options.get("half_life", entry.data.get("half_life", 0))
            self._hours_to_peak = entry.options.get("hours_to_peak", entry.data.get("hours_to_peak", 0.0))

        k_e = math.log(2) / self._half_life if self._half_life > 0 else 0
        k_a = self._ka if self._ka > 0 else (self._solve_ka(self._hours_to_peak, k_e) if self._hours_to_peak > 0 else 0)
        if k_a > 0 and abs(k_a - k_e) < 0.0001:
            k_a *= 1.0001  # Avoid division by zero in limiting case
        self._ka = k_a

        total_body = 0.0
        total_gut = 0.0

        for dose_time, dose_strength in self._dose_history:
            elapsed_hours = (now - dose_time).total_seconds() / 3600.0
            if elapsed_hours < 0:
                # Future dose (shouldn't happen, but skip it)
                continue
            if k_a > 0 and abs(k_a - k_e) > 0.0001:
                # Two-compartment model (Bateman equation)
                # Gut contribution: remaining drug in gut at time t
                total_gut += dose_strength * math.exp(-k_a * elapsed_hours)
                # Body contribution from this dose: Bateman equation
                total_body += dose_strength * k_a / (k_a - k_e) * (
                    math.exp(-k_e * elapsed_hours) - math.exp(-k_a * elapsed_hours)
                )
            elif k_a > 0:
                # Limiting case ka ≈ ke
                total_gut += dose_strength * math.exp(-k_a * elapsed_hours)
                total_body += dose_strength * k_a * elapsed_hours * math.exp(-k_a * elapsed_hours)
            else:
                # Immediate release (no absorption phase)
                if k_e > 0:
                    total_body += dose_strength * math.exp(-k_e * elapsed_hours)
                else:
                    # No elimination either — drug stays forever
                    total_body += dose_strength

        self._current_mass = total_body
        self._gut_mass = total_gut
        self._last_updated = now
        self.update_state()

    @callback
    def reset_data(self, *args, **kwargs):
        self._current_mass = 0.0
        self._gut_mass = 0.0
        self._dose_history = []
        self._last_updated = None
        self.update_state()

    @callback
    def update_state(self):
        self._attr_native_value = round(self._current_mass, 1)
        self._attr_extra_state_attributes = {
            "last_updated": self._last_updated.isoformat() if self._last_updated else None,
            "gut_mass": round(self._gut_mass, 1),
            "ka": self._ka,
            "dose_history": [[ts.isoformat(), strength] for ts, strength in self._dose_history],
        }
        self.async_write_ha_state()
        # Broadcast the live drug amount so Steady State can instantly recalculate
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
        """Solve for absorption rate constant (ka) given desired time-to-peak (t_max)
        and elimination rate constant (ke), using the standard pharmacokinetic formula:
            t_max = ln(ka/ke) / (ka - ke)
        Uses binary search since the equation has no closed-form solution for ka.
        """
        low, high = 0.0001, 20.0
        for _ in range(50):
            mid_ka = (low + high) / 2
            if mid_ka == k_e: mid_ka += 0.0001
            try:
                calc_t_max = (math.log(mid_ka) - math.log(k_e)) / (mid_ka - k_e)
                # When calc_t_max < t_max, ka is too high (absorption too fast),
                # so we must decrease ka (move high down). When calc_t_max > t_max,
                # ka is too low (absorption too slow), so we must increase ka (move low up).
                if calc_t_max < t_max: high = mid_ka
                else: low = mid_ka
            except (ValueError, ZeroDivisionError):
                low = mid_ka
        return (low + high) / 2

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )
