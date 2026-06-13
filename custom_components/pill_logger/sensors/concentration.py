from datetime import timedelta
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.core import callback
import homeassistant.util.dt as dt_util
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
import math
from ..const import DOMAIN, PK_DEFAULTS


class PillConcentrationSensor(RestoreSensor):
    _attr_has_entity_name = True
    should_poll = False

    def __init__(self, entry):
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._attr_name = "Amount in body"
        self._attr_unique_id = f"{entry.entry_id}_concentration"
        self._attr_icon = "mdi:chart-bell-curve"
        self._entry_id = entry.entry_id
        self._strength = entry.options.get("strength", entry.data.get("strength", 0))
        self._half_life = entry.options.get("half_life", entry.data.get("half_life", 0))
        self._hours_to_peak = entry.options.get("hours_to_peak", entry.data.get("hours_to_peak", 0.0))
        self._release_type = entry.data.get("release_type", "Instant Release")
        self._bioavailability = entry.options.get("bioavailability", entry.data.get("bioavailability", PK_DEFAULTS["bioavailability"]))
        self._ir_fraction = entry.options.get("ir_fraction", entry.data.get("ir_fraction", PK_DEFAULTS["ir_fraction"]))
        self._zero_order_duration = entry.options.get("zero_order_duration", entry.data.get("zero_order_duration", PK_DEFAULTS["zero_order_duration"]))
        self._release_half_life = entry.options.get("release_half_life", entry.data.get("release_half_life", PK_DEFAULTS["release_half_life"]))
        self._lag_time = entry.options.get("lag_time", entry.data.get("lag_time", PK_DEFAULTS["lag_time"]))

        # IR model state (also used as IR component in ER model)
        self._current_mass = 0.0
        self._gut_mass = 0.0  # Legacy: maps to _gut_ir_mass for IR mode
        self._last_updated = None
        self._ka = 0.0
        self._dose_history = []  # List of (datetime, float) tuples

        # ER model state
        self._gut_ir_mass = 0.0      # A_G_IR: IR gut compartment (mg)
        self._matrix_sr_mass = 0.0   # A_M_SR: SR matrix compartment (mg)
        self._gut_sr_mass = 0.0      # A_G_SR: SR gut compartment (mg)
        self._kr = 0.0               # SR first-order release rate (h⁻¹)

        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "mg"
        self._attr_suggested_display_precision = 1
        self._attr_native_value = 0.0
        self._attr_extra_state_attributes = {"last_updated": None, "gut_mass": 0.0, "ka": 0.0, "dose_history": []}

    def _load_pk_params(self):
        """Reload all PK parameters from the current config entry."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry:
            self._strength = entry.options.get("strength", entry.data.get("strength", 0))
            self._half_life = entry.options.get("half_life", entry.data.get("half_life", 0))
            self._hours_to_peak = entry.options.get("hours_to_peak", entry.data.get("hours_to_peak", 0.0))
            self._release_type = entry.data.get("release_type", "Instant Release")
            self._bioavailability = entry.options.get("bioavailability", entry.data.get("bioavailability", PK_DEFAULTS["bioavailability"]))
            self._ir_fraction = entry.options.get("ir_fraction", entry.data.get("ir_fraction", PK_DEFAULTS["ir_fraction"]))
            self._zero_order_duration = entry.options.get("zero_order_duration", entry.data.get("zero_order_duration", PK_DEFAULTS["zero_order_duration"]))
            self._release_half_life = entry.options.get("release_half_life", entry.data.get("release_half_life", PK_DEFAULTS["release_half_life"]))
            self._lag_time = entry.options.get("lag_time", entry.data.get("lag_time", PK_DEFAULTS["lag_time"]))

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

        # Reload PK parameters from current config entry
        self._load_pk_params()

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

                # Restore ER compartments if present
                gut_ir_mass = last_state.attributes.get("gut_ir_mass", None)
                matrix_sr_mass = last_state.attributes.get("matrix_sr_mass", None)
                gut_sr_mass = last_state.attributes.get("gut_sr_mass", None)

                if last_ts_str:
                    try:
                        last_ts = dt_util.parse_datetime(last_ts_str)
                        now = dt_util.now()
                        elapsed_hours = (now - last_ts).total_seconds() / 3600.0

                        if self._release_type == "Sustained Release" and gut_ir_mass is not None:
                            # ER mode: approximate decay for legacy restore without dose_history
                            # Body decays exponentially; gut/matrix compartments set to 0
                            # (will be corrected on next dose event via _recalculate_from_history)
                            k_e = math.log(2) / self._half_life if self._half_life > 0 else 0
                            self._current_mass = old_mass * math.exp(-k_e * elapsed_hours) if k_e > 0 else old_mass
                            self._gut_ir_mass = 0.0
                            self._matrix_sr_mass = 0.0
                            self._gut_sr_mass = 0.0
                            self._last_updated = last_ts
                        else:
                            # IR mode: legacy 2-compartment restore
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
            self._gut_ir_mass = 0.0
            self._matrix_sr_mass = 0.0
            self._gut_sr_mass = 0.0
            self._last_updated = dt_util.now()
            self.update_state()
            # Broadcast initial value to steady state sensor
            async_dispatcher_send(self.hass, f"concentration_updated_{self._entry_id}", self._current_mass)

    @callback
    def handle_pill_taken(self, timestamp, *args, **kwargs):
        """Handle pill_taken signal with synchronized timestamp payload."""
        now = timestamp  # Use the synchronized timestamp from the signal

        # Reload PK parameters in case they changed
        self._load_pk_params()

        # Record dose in history (raw strength, not effective — bioavailability applied at computation time)
        self._dose_history.append((now, float(self._strength)))

        # Recalculate all compartments from full dose history
        # This handles both IR and ER models correctly, including lag time
        self._recalculate_from_history(now=now)

    @callback
    def handle_pill_undone(self, *args, **kwargs):
        """Handle pill_undone signal: remove last dose and recalculate from history."""
        if not self._dose_history:
            return
        self._dose_history.pop()
        self._recalculate_from_history()

    def _recalculate_from_history(self, now=None):
        """Recalculate all compartments from full dose history using superposition.

        Routes to IR or ER model based on release_type.
        """
        self._load_pk_params()

        if self._release_type == "Sustained Release":
            self._recalculate_er(now=now)
        else:
            self._recalculate_ir(now=now)

    def _recalculate_ir(self, now=None):
        """Recalculate using the standard 2-compartment IR model (Bateman equation).

        Since the model is linear, the total drug in each compartment at any time
        equals the sum of each individual dose's contribution.
        """
        if now is None:
            now = dt_util.now()

        k_e = math.log(2) / self._half_life if self._half_life > 0 else 0
        k_a = self._ka if self._ka > 0 else (self._solve_ka(self._hours_to_peak, k_e) if self._hours_to_peak > 0 else 0)
        if k_a > 0 and abs(k_a - k_e) < 0.0001:
            k_a *= 1.0001  # Avoid division by zero in limiting case
        self._ka = k_a

        F = float(self._bioavailability) / 100.0
        lag = float(self._lag_time) / 60.0 if self._lag_time else 0.0  # Convert minutes to hours
        total_body = 0.0
        total_gut = 0.0

        for dose_time, dose_strength in self._dose_history:
            t = (now - dose_time).total_seconds() / 3600.0
            if t < 0:
                continue
            # Apply lag time: dose is inert during lag period
            t_eff = t - lag
            if t_eff < 0:
                continue  # Dose hasn't started releasing yet

            effective_dose = F * dose_strength
            if k_a > 0 and abs(k_a - k_e) > 0.0001:
                # Two-compartment model (Bateman equation)
                total_gut += effective_dose * math.exp(-k_a * t_eff)
                total_body += effective_dose * k_a / (k_a - k_e) * (
                    math.exp(-k_e * t_eff) - math.exp(-k_a * t_eff)
                )
            elif k_a > 0:
                # Limiting case ka ≈ ke
                total_gut += effective_dose * math.exp(-k_a * t_eff)
                total_body += effective_dose * k_a * t_eff * math.exp(-k_a * t_eff)
            else:
                # Immediate release (no absorption phase)
                if k_e > 0:
                    total_body += effective_dose * math.exp(-k_e * t_eff)
                else:
                    total_body += effective_dose

        self._current_mass = total_body
        self._gut_mass = total_gut
        self._gut_ir_mass = total_gut  # Keep in sync for ER→IR transitions
        self._matrix_sr_mass = 0.0
        self._gut_sr_mass = 0.0
        self._last_updated = now
        self.update_state()

    def _recalculate_er(self, now=None):
        """Recalculate using the 4-compartment ER model (hybrid IR + SR).

        For each dose, computes:
        - IR component: standard Bateman equation with D_IR = F * strength * ir_fraction
        - SR component: piecewise analytical solution
          Phase 1 (0 ≤ t ≤ T_dur): zero-order release at rate R₀ = D_SR / T_dur
          Phase 2 (t > T_dur): first-order release with rate k_r = ln(2) / release_half_life
        """
        if now is None:
            now = dt_util.now()

        k_e = math.log(2) / self._half_life if self._half_life > 0 else 0
        k_a = self._ka if self._ka > 0 else (self._solve_ka(self._hours_to_peak, k_e) if self._hours_to_peak > 0 else 0)
        if k_a > 0 and abs(k_a - k_e) < 0.0001:
            k_a *= 1.0001
        self._ka = k_a

        k_r = math.log(2) / self._release_half_life if self._release_half_life > 0 else 0
        self._kr = k_r
        T_dur = float(self._zero_order_duration)
        F = float(self._bioavailability) / 100.0
        ir_frac = float(self._ir_fraction) / 100.0
        lag = float(self._lag_time) / 60.0 if self._lag_time else 0.0  # Convert minutes to hours

        total_body = 0.0
        total_gut_ir = 0.0
        total_matrix_sr = 0.0
        total_gut_sr = 0.0

        for dose_time, dose_strength in self._dose_history:
            t = (now - dose_time).total_seconds() / 3600.0
            if t < 0:
                continue

            # Apply lag time: dose is inert during lag period
            t_eff = t - lag
            if t_eff < 0:
                continue  # Dose hasn't started releasing yet

            effective_dose = F * dose_strength
            D_IR = effective_dose * ir_frac
            D_SR = effective_dose * (1 - ir_frac)

            # --- IR component: standard Bateman equation ---
            if D_IR > 0:
                if k_a > 0 and abs(k_a - k_e) > 0.0001:
                    total_gut_ir += D_IR * math.exp(-k_a * t_eff)
                    total_body += D_IR * k_a / (k_a - k_e) * (
                        math.exp(-k_e * t_eff) - math.exp(-k_a * t_eff)
                    )
                elif k_a > 0:
                    total_gut_ir += D_IR * math.exp(-k_a * t_eff)
                    total_body += D_IR * k_a * t_eff * math.exp(-k_a * t_eff)
                else:
                    if k_e > 0:
                        total_body += D_IR * math.exp(-k_e * t_eff)
                    else:
                        total_body += D_IR

            # --- SR component: piecewise analytical solution ---
            if D_SR > 0 and T_dur > 0:
                R0 = D_SR / T_dur

                if t_eff <= T_dur:
                    # Phase 1: zero-order release (0 ≤ t_eff ≤ T_dur)
                    total_matrix_sr += D_SR - R0 * t_eff

                    if k_a > 0 and abs(k_a - k_e) > 0.0001:
                        # G_SR(t) = (R0/ka) * (1 - exp(-ka*t))
                        total_gut_sr += (R0 / k_a) * (1 - math.exp(-k_a * t_eff))

                        # C_SR(t) = (R0/ke)*(1 - exp(-ke*t)) - (R0*ka)/((ka-ke)*ke) * (exp(-ke*t) - exp(-ka*t))
                        total_body += (R0 / k_e) * (1 - math.exp(-k_e * t_eff)) - \
                                      (R0 * k_a) / ((k_a - k_e) * k_e) * (
                                          math.exp(-k_e * t_eff) - math.exp(-k_a * t_eff)
                                      )
                    elif k_a > 0:
                        # Limiting case ka ≈ ke
                        total_gut_sr += (R0 / k_a) * (1 - math.exp(-k_a * t_eff))
                        total_body += (R0 / k_e) * (1 - math.exp(-k_e * t_eff)) - \
                                      (R0 / k_e) * k_a * t_eff * math.exp(-k_e * t_eff)
                    else:
                        # No absorption: drug goes directly to body
                        total_gut_sr += R0 * t_eff
                        if k_e > 0:
                            total_body += (R0 / k_e) * (1 - math.exp(-k_e * t_eff))
                        else:
                            total_body += R0 * t_eff

                else:
                    # Phase 2: first-order tail (t_eff > T_dur)
                    # Compute state at T_dur first
                    M_SR_at_T = D_SR - R0 * T_dur  # Remaining matrix at T_dur

                    if k_a > 0 and abs(k_a - k_e) > 0.0001:
                        G_SR_at_T = (R0 / k_a) * (1 - math.exp(-k_a * T_dur))
                        B_SR_at_T = (R0 / k_e) * (1 - math.exp(-k_e * T_dur)) - \
                                    (R0 * k_a) / ((k_a - k_e) * k_e) * (
                                        math.exp(-k_e * T_dur) - math.exp(-k_a * T_dur)
                                    )
                    elif k_a > 0:
                        G_SR_at_T = (R0 / k_a) * (1 - math.exp(-k_a * T_dur))
                        B_SR_at_T = (R0 / k_e) * (1 - math.exp(-k_e * T_dur)) - \
                                    (R0 / k_e) * k_a * T_dur * math.exp(-k_e * T_dur)
                    else:
                        G_SR_at_T = R0 * T_dur
                        if k_e > 0:
                            B_SR_at_T = (R0 / k_e) * (1 - math.exp(-k_e * T_dur))
                        else:
                            B_SR_at_T = R0 * T_dur

                    tau = t_eff - T_dur  # Time since end of Phase 1

                    # Matrix decay: M_SR(t) = M_SR(T_dur) * exp(-kr * tau)
                    if k_r > 0:
                        total_matrix_sr += M_SR_at_T * math.exp(-k_r * tau)
                    else:
                        total_matrix_sr += M_SR_at_T

                    # SR gut and body from Phase 2
                    if k_r > 0 and k_a > 0 and abs(k_a - k_e) > 0.0001:
                        # Body contribution from Phase 1 state decaying forward
                        total_body += B_SR_at_T * math.exp(-k_e * tau) + \
                                      G_SR_at_T * k_a / (k_a - k_e) * (
                                          math.exp(-k_e * tau) - math.exp(-k_a * tau))

                        # Body contribution from SR matrix first-order release in Phase 2
                        if abs(k_r - k_e) > 0.0001 and abs(k_r - k_a) > 0.0001:
                            # Three distinct exponentials: ke, ka, kr
                            total_body += k_r * M_SR_at_T * k_a / (k_a - k_e) * (
                                (math.exp(-k_e * tau) - math.exp(-k_a * tau)) / (k_r - k_e) -
                                (math.exp(-k_r * tau) - math.exp(-k_a * tau)) / (k_r - k_a)
                            )
                        elif abs(k_r - k_e) <= 0.0001 and abs(k_r - k_a) > 0.0001:
                            # Limiting case: k_r ≈ k_e, k_a distinct
                            total_body += k_r * M_SR_at_T * k_a / (k_a - k_e) * (
                                tau * math.exp(-k_e * tau) +
                                (math.exp(-k_a * tau) - math.exp(-k_e * tau)) / (k_a - k_e)
                            )
                        elif abs(k_r - k_a) <= 0.0001 and abs(k_r - k_e) > 0.0001:
                            # Limiting case: k_r ≈ k_a, k_e distinct
                            total_body += k_r * k_a * M_SR_at_T / (k_a - k_e) * (
                                (math.exp(-k_e * tau) - math.exp(-k_a * tau)) / (k_a - k_e) -
                                tau * math.exp(-k_a * tau)
                            )
                        else:
                            # Limiting case: k_r ≈ k_a ≈ k_e (extremely rare)
                            k = (k_r + k_a + k_e) / 3.0
                            total_body += k * k * M_SR_at_T * tau * tau / 2.0 * math.exp(-k * tau)

                        # Gut SR from Phase 2
                        total_gut_sr += G_SR_at_T * math.exp(-k_a * tau) + \
                                        k_r * M_SR_at_T / (k_a - k_r) * (
                                            math.exp(-k_r * tau) - math.exp(-k_a * tau)
                                        ) if abs(k_a - k_r) > 0.0001 else \
                                        k_r * M_SR_at_T * tau * math.exp(-k_a * tau)

                    elif k_r > 0 and k_a > 0:
                        # Limiting case ka ≈ ke for Phase 2
                        total_body += B_SR_at_T * math.exp(-k_e * tau) + \
                                      G_SR_at_T * k_a * tau * math.exp(-k_e * tau)
                        total_gut_sr += G_SR_at_T * math.exp(-k_a * tau) + \
                                        k_r * M_SR_at_T * tau * math.exp(-k_a * tau)
                    elif k_r > 0:
                        # No absorption: SR release goes directly to body
                        total_body += B_SR_at_T * math.exp(-k_e * tau) if k_e > 0 else B_SR_at_T
                        total_gut_sr += G_SR_at_T * math.exp(-k_r * tau) if k_a > 0 else 0

            elif D_SR > 0 and k_r > 0:
                # No zero-order phase: pure first-order SR release (like a second ka)
                total_matrix_sr += D_SR * math.exp(-k_r * t_eff)

                # Gut SR
                if abs(k_r - k_a) > 0.0001:
                    total_gut_sr += D_SR * k_r / (k_r - k_a) * (
                        math.exp(-k_a * t_eff) - math.exp(-k_r * t_eff)
                    )
                else:
                    # Limiting case: k_r ≈ k_a
                    total_gut_sr += D_SR * k_r * t_eff * math.exp(-k_a * t_eff)

                # Body from SR
                if k_a > 0 and abs(k_a - k_e) > 0.0001:
                    if abs(k_r - k_e) > 0.0001 and abs(k_r - k_a) > 0.0001:
                        # Three distinct exponentials
                        total_body += D_SR * k_r * k_a * (
                            math.exp(-k_e * t_eff) / ((k_r - k_e) * (k_a - k_e)) +
                            math.exp(-k_r * t_eff) / ((k_e - k_r) * (k_a - k_r)) +
                            math.exp(-k_a * t_eff) / ((k_e - k_a) * (k_r - k_a))
                        )
                    elif abs(k_r - k_a) <= 0.0001:
                        # Limiting case: k_r ≈ k_a, k_e distinct
                        k = k_r  # k_r ≈ k_a
                        total_body += D_SR * k * k / ((k - k_e) ** 2) * (
                            math.exp(-k_e * t_eff) - math.exp(-k * t_eff) * (1 + (k - k_e) * t_eff)
                        )
                    elif abs(k_r - k_e) <= 0.0001:
                        # Limiting case: k_r ≈ k_e, k_a distinct
                        total_body += D_SR * k_r * k_a / ((k_a - k_e) ** 2) * (
                            math.exp(-k_e * t_eff) * (1 + (k_a - k_e) * t_eff) - math.exp(-k_a * t_eff)
                        )
                elif k_a > 0:
                    # Limiting case: k_a ≈ k_e
                    if abs(k_r - k_a) > 0.0001:
                        total_body += D_SR * k_r * k_a / (k_r - k_a) * t_eff * math.exp(-k_a * t_eff)
                    else:
                        # k_r ≈ k_a ≈ k_e
                        k = k_r
                        total_body += D_SR * k * k * t_eff * t_eff / 2.0 * math.exp(-k * t_eff)
                else:
                    # No absorption: direct to body
                    if k_e > 0 and abs(k_r - k_e) > 0.0001:
                        total_body += D_SR * k_r / (k_r - k_e) * (
                            math.exp(-k_e * t_eff) - math.exp(-k_r * t_eff)
                        )
                    elif k_e > 0:
                        total_body += D_SR * k_r * t_eff * math.exp(-k_e * t_eff)
                    else:
                        total_body += D_SR * (1 - math.exp(-k_r * t_eff))

        self._current_mass = total_body
        self._gut_ir_mass = total_gut_ir
        self._matrix_sr_mass = total_matrix_sr
        self._gut_sr_mass = total_gut_sr
        self._gut_mass = total_gut_ir  # Keep legacy attribute in sync
        self._last_updated = now
        self.update_state()

    @callback
    def reset_data(self, *args, **kwargs):
        self._current_mass = 0.0
        self._gut_mass = 0.0
        self._gut_ir_mass = 0.0
        self._matrix_sr_mass = 0.0
        self._gut_sr_mass = 0.0
        self._dose_history = []
        self._last_updated = None
        self.update_state()

    @callback
    def update_state(self):
        self._attr_native_value = round(self._current_mass, 1)
        if self._release_type == "Sustained Release":
            self._attr_extra_state_attributes = {
                "last_updated": self._last_updated.isoformat() if self._last_updated else None,
                "gut_mass": round(self._gut_ir_mass, 1),
                "gut_ir_mass": round(self._gut_ir_mass, 1),
                "matrix_sr_mass": round(self._matrix_sr_mass, 1),
                "gut_sr_mass": round(self._gut_sr_mass, 1),
                "ka": self._ka,
                "kr": self._kr,
                "lag_time": self._lag_time,
                "dose_history": [[ts.isoformat(), strength] for ts, strength in self._dose_history],
            }
        else:
            self._attr_extra_state_attributes = {
                "last_updated": self._last_updated.isoformat() if self._last_updated else None,
                "gut_mass": round(self._gut_mass, 1),
                "ka": self._ka,
                "lag_time": self._lag_time,
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

        self._load_pk_params()

        if self._release_type == "Sustained Release":
            # Use exact analytical recalculation for ER mode (no Euler drift)
            self._recalculate_from_history()
        else:
            # IR mode: use exact Bateman equation decay
            elapsed_hours = (now - self._last_updated).total_seconds() / 3600.0
            if elapsed_hours <= 0:
                return
            self._decay_ir(elapsed_hours)
            self._last_updated = now
            self.update_state()

    def _decay_ir(self, elapsed_hours):
        """Decay IR model compartments by elapsed_hours using exact Bateman equation."""
        k_e = math.log(2) / self._half_life
        k_a = self._ka if self._ka > 0 else (self._solve_ka(self._hours_to_peak, k_e) if self._hours_to_peak > 0 else 0)
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
