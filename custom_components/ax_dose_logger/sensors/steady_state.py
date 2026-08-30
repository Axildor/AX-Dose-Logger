"""Steady-state progress sensor for scheduled medications.

Reworked (2026-08-08) to a **trough-anchored** pharmacokinetic model that
fixes three prior bugs; see ``plans/steady-state-trough-rework-plan.md``.

Prior bugs
----------
* **Bug A** — the sensor compared the *instantaneous* body mass (which
  oscillates each cycle between the steady-state peak ``C_max,ss`` and
  trough ``C_min,ss``) to ``0.90 × C_max,ss`` (90% of the *peak*). Near
  every trough the mass dipped below that line, flipping the sensor back
  to "days remaining" — so it never *stayed* reached. It also produced a
  false "reached" after a single dose for short-half-life drugs (the
  first-dose peak can clear 90% of the accumulated peak).
* **Bug B** — multi-dose-per-day schedules used ``τ = 24/N`` (the
  *average* inter-dose gap), so the steady-state band was computed
  against a fictional intermediate gap rather than the worst (longest)
  gap that produces the lowest trough.
* **Bug C** — Cyclic had no branch and fell into the ``else: τ = 24``
  case, assuming *daily* dosing. An every-second-day pill (real
  interval 48 h) with a long half-life was stuck at ~14.8–16.2 days and
  never reached steady state.

New model
---------
The clinically correct steady-state marker is the **trough**
concentration reaching 90% of its asymptotic value, anchored to the
*longest* nominal dosing gap. PK accumulation is multiplicative, so the
90% ratio is half-life-independent: the implied lateness slack
``t_buffer = −ln(0.9)/k_e`` is ~18 min for a 2 h half-life and ~3.7 h
for a 24 h half-life (short = tight, long = lenient — the correct
scaling). A flat-time buffer would scale the wrong way.

To remove the intra-cycle oscillation that caused Bug A, the
instantaneous mass is **projected forward to the next trough** before
the threshold test. This makes the comparison phase-independent, so
once steady state is reached the sensor *stays* at ``0.0`` regardless
of where in the cycle ``now`` sits.
"""

import math
from datetime import timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback

from ..const import (
    PK_DEFAULTS,
    RELEASE_INSTANT,
    TRACKING_CYCLIC,
    TRACKING_REGULAR_INTERVAL,
    TRACKING_TIME_OF_DAY,
    get_dose_times,
)
from ..entity import AxDoseLoggerSensorEntity
from ..pk_model import PKModel, PKParams
from ..schedule import get_next_dose_time
from ..sliding_window import is_on_day

# Standard clinical convention: steady state is reached when the trough
# concentration is >= 90% of its asymptotic value. Used both for the
# "reached" threshold (90% of C_min,ss) and for the no-dose baseline
# (time for the trough to reach 90% from zero, t_90 = -ln(0.1)/k_e).
STEADY_STATE_FRACTION = 0.90

# A mass above 110% of C_max,ss indicates a dosage reduction has just
# been made (the body is still decaying toward the new, lower steady
# state). The sensor reports the decay time to the new threshold.
ABOVE_RANGE_FACTOR = 1.10


class PillSteadyStateSensor(AxDoseLoggerSensorEntity, RestoreSensor):
    """Days remaining until 90% pharmacokinetic steady state.

    The state is the projected-trough-equivalent days remaining (float,
    1 decimal). ``0.0`` means steady state is reached. ``None`` renders
    as ``unknown`` when elimination is disabled or the schedule is
    invalid.
    """

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
                except (ValueError, TypeError):
                    pass
            # Restore _current_mass so update_state() produces correct
            # values before the first coordinator refresh completes.
            if "current_mass" in last_state.attributes:
                try:
                    self._current_mass = float(last_state.attributes["current_mass"])
                except (ValueError, TypeError):
                    pass

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator.

        Reads ``concentration`` and ``last_dose_time`` directly from
        coordinator data — no more ``concentration_updated`` signal.
        """
        if self.coordinator.data:
            self._current_mass = self.coordinator.data.concentration
            self._last_dose_timestamp = self.coordinator.data.last_dose_time
        self.update_state()

    # ------------------------------------------------------------------
    # Schedule helpers
    # ------------------------------------------------------------------
    def _compute_tau(self, entry) -> float:
        """Return the longest nominal inter-dose gap (hours) for the schedule.

        Anchoring to the *longest* gap (not the average) guarantees the
        steady-state band covers the lowest trough the schedule can
        produce, so a multi-dose or cyclic regimen stays reached across
        every one of its gaps.

        * **Time of Day** — circular max of consecutive slot gaps
          (including the wrap-around). For 13:00 + 21:00 this is 16 h.
        * **Regular Interval** — ``hours_between_doses`` (uniform gap).
        * **Cyclic** — the OFF-day span plus one day,
          ``(days_off + 1) × 24``. Cyclic is always 1 dose/ON-day at a
          single ``dose_time``, so the longest gap is the span from the
          last ON-day dose to the first ON-day dose of the next cycle.
          For an every-second-day pill this is 48 h.

        Returns ``0.0`` for an invalid/unparseable schedule so the
        caller emits ``None``.
        """
        tracking_type = entry.data.get("tracking_type")

        if tracking_type == TRACKING_TIME_OF_DAY:
            parsed_times = get_dose_times(entry)
            if len(parsed_times) < 1:
                return 0.0
            if len(parsed_times) == 1:
                return 24.0
            # Circular gaps between consecutive (hour, minute) slots.
            minutes = [h * 60 + m for h, m in parsed_times]
            minutes.sort()
            gaps = [minutes[i + 1] - minutes[i] for i in range(len(minutes) - 1)]
            # Wrap-around gap (last slot today -> first slot tomorrow).
            gaps.append(24 * 60 - minutes[-1] + minutes[0])
            return max(gaps) / 60.0

        if tracking_type == TRACKING_REGULAR_INTERVAL:
            hours = float(entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 24.0)))
            return hours if hours > 0 else 0.0

        if tracking_type == TRACKING_CYCLIC:
            days_off = float(entry.options.get("days_off", entry.data.get("days_off", 2)))
            days_off = max(days_off, 0)
            return (days_off + 1.0) * 24.0

        # As Needed (sensor not created) or unknown — fall back to daily.
        return 24.0

    def _next_dose_datetime(self, entry, tau: float, now):
        """Return the next scheduled dose datetime, clamped to at most ``tau`` ahead.

        The trough is the moment just *before* the next dose, so the sensor
        evaluates the PK model at this time. Clamping to ``tau`` bounds the
        projection to one dosing interval even if the schedule helper cannot
        resolve a next dose (defensive fallback = ``now + tau``, the trough).
        """
        if not self.coordinator.data or tau <= 0:
            return now + timedelta(hours=tau)

        tracking_type = entry.data.get("tracking_type")
        if tracking_type in (TRACKING_REGULAR_INTERVAL, TRACKING_TIME_OF_DAY):
            timestamps = [ts for ts, _ in self.coordinator.data.dose_history]
            next_dose = get_next_dose_time(entry, timestamps, now, tracking_type)
            if next_dose is not None:
                # next_dose may be in the past if overdue; clamp to >= now.
                next_dose = max(next_dose, now)
                # Clamp to at most tau ahead (one interval).
                if (next_dose - now).total_seconds() > tau * 3600.0:
                    next_dose = now + timedelta(hours=tau)
                return next_dose

        if tracking_type == TRACKING_CYCLIC:
            # Find the next ON-day at the configured dose_time.
            dose_time = entry.options.get("dose_time", entry.data.get("dose_time", "08:00"))
            try:
                parts = dose_time.split(":")
                dose_h, dose_m = int(parts[0]), int(parts[1])
            except ValueError, IndexError, AttributeError:
                dose_h, dose_m = 8, 0

            today = now.date()
            # HA's NumberSelector stores all numeric inputs as floats
            # (vol.Coerce(float)), so coerce to int here: range() below
            # requires an int and would otherwise raise TypeError for
            # cyclic entries whose days_on/days_off were saved as floats.
            days_on = int(entry.options.get("days_on", entry.data.get("days_on", 5)))
            days_off = int(entry.options.get("days_off", entry.data.get("days_off", 2)))
            cycle_length = days_on + days_off
            if cycle_length <= 0:
                cycle_length = 1
            for offset in range(cycle_length + 1):
                check_date = today + timedelta(days=offset)
                if is_on_day(entry, check_date):
                    candidate = now.replace(
                        year=check_date.year,
                        month=check_date.month,
                        day=check_date.day,
                        hour=dose_h,
                        minute=dose_m,
                        second=0,
                        microsecond=0,
                    )
                    if candidate > now:
                        if (candidate - now).total_seconds() > tau * 3600.0:
                            candidate = now + timedelta(hours=tau)
                        return candidate
            # No ON day found in range — fall back to now + tau (the trough).
            return now + timedelta(hours=tau)

        # Unknown tracking type — full interval (worst case = trough).
        return now + timedelta(hours=tau)

    def _build_pk_params(self, entry) -> PKParams:
        """Build a PKParams snapshot from the config entry (mirrors the coordinator)."""
        opts = entry.options
        data = entry.data
        return PKParams(
            release_type=data.get("release_type", RELEASE_INSTANT),
            strength=float(opts.get("strength", data.get("strength", 0))),
            half_life=float(opts.get("half_life", data.get("half_life", 0))),
            hours_to_peak=float(opts.get("hours_to_peak", data.get("hours_to_peak", 0.0))),
            bioavailability=float(
                opts.get("bioavailability", data.get("bioavailability", PK_DEFAULTS["bioavailability"]))
            ),
            ir_fraction=float(opts.get("ir_fraction", data.get("ir_fraction", PK_DEFAULTS["ir_fraction"]))),
            zero_order_duration=float(
                opts.get("zero_order_duration", data.get("zero_order_duration", PK_DEFAULTS["zero_order_duration"]))
            ),
            release_half_life=float(
                opts.get("release_half_life", data.get("release_half_life", PK_DEFAULTS["release_half_life"]))
            ),
            lag_time=float(opts.get("lag_time", data.get("lag_time", PK_DEFAULTS["lag_time"]))),
            ir_hours_to_peak=float(
                opts.get("ir_hours_to_peak", data.get("ir_hours_to_peak", PK_DEFAULTS["ir_hours_to_peak"]))
            ),
        )

    # ------------------------------------------------------------------
    # State computation
    # ------------------------------------------------------------------
    def update_state(self):
        entry = self.hass.config_entries.async_get_entry(self._entry_id)

        half_life = float(entry.options.get("half_life", entry.data.get("half_life", 0.0)))
        strength = float(entry.options.get("strength", entry.data.get("strength", 0.0)))
        bioavailability = float(
            entry.options.get("bioavailability", entry.data.get("bioavailability", PK_DEFAULTS["bioavailability"]))
        )

        tau = self._compute_tau(entry)
        F = bioavailability / 100.0
        effective_strength = strength * F

        # Invalid config (no elimination, no strength, no interval) → unknown.
        if half_life <= 0 or strength <= 0 or tau <= 0:
            self._attr_native_value = None
            self._attr_extra_state_attributes = {
                "dosing_interval_hours": round(tau, 2) if tau > 0 else None,
                "last_dose_timestamp": self._last_dose_timestamp.isoformat() if self._last_dose_timestamp else None,
            }
            self.async_write_ha_state()
            return

        k_e = math.log(2) / half_life
        accumulation_factor = 1.0 / (1.0 - math.exp(-k_e * tau))
        c_max_ss = effective_strength * accumulation_factor
        c_min_ss = c_max_ss * math.exp(-k_e * tau)
        threshold = STEADY_STATE_FRACTION * c_min_ss

        current_mass = self._current_mass

        # No doses yet — days to 90% from zero (the trough baseline).
        if current_mass <= 0:
            # Time for the accumulation envelope (1 - e^(-k_e*t)) to reach
            # 90% of the asymptote: 1 - e^(-k_e*t) = 0.9 -> t = -ln(0.1)/k_e.
            t_90 = -math.log(1.0 - STEADY_STATE_FRACTION) / k_e
            self._attr_native_value = round(t_90 / 24.0, 1)
            self._write_attrs(c_max_ss, c_min_ss, threshold, current_mass, current_mass, tau)
            return

        # Above-range (dosage reduction) — decay toward the new threshold.
        if current_mass > ABOVE_RANGE_FACTOR * c_max_ss:
            t_decay = math.log(current_mass / threshold) / k_e
            self._attr_native_value = round(max(0.0, t_decay) / 24.0, 1)
            self._write_attrs(c_max_ss, c_min_ss, threshold, current_mass, current_mass, tau)
            return

        # Normal branch — evaluate the PK model at the next-dose time to
        # get the TRUE trough (the concentration just before the next
        # dose), accounting for the full superposition of all doses still
        # absorbing. A naive monoexponential decay of the instantaneous
        # mass is wrong because the peak mass includes a just-absorbed
        # dose whose absorption tail still contributes at the trough;
        # only the full recompute captures every dose's contribution.
        # Evaluating at the next trough removes the intra-cycle
        # oscillation that caused the sensor to flip in and out of the
        # reached state (Bug A).
        now = dt_util.now()
        next_dose_dt = self._next_dose_datetime(entry, tau, now)
        if self.coordinator.data and self.coordinator.data.dose_history:
            pk_params = self._build_pk_params(entry)
            trough_now = PKModel.compute(pk_params, self.coordinator.data.dose_history, next_dose_dt).body
        else:
            # Defensive: no dose history but mass > 0 (shouldn't happen;
            # the no-dose branch above catches current_mass <= 0).
            trough_now = current_mass

        if trough_now >= threshold:
            self._attr_native_value = 0.0
        else:
            # Days remaining, computed from the trough-equivalent. p is
            # the trough-equivalent as a fraction of the TROUGH asymptote
            # (c_min_ss), so the accumulation-envelope inversion gives the
            # correct "accumulation age" for the trough trajectory.
            p = trough_now / c_min_ss if c_min_ss > 0 else 0.0
            if p >= 1.0:
                # Defensive: trough_now >= threshold already caught the
                # reached case; this guards against float edge cases.
                self._attr_native_value = 0.0
            elif p <= 0:
                t_90 = -math.log(1.0 - STEADY_STATE_FRACTION) / k_e
                self._attr_native_value = round(t_90 / 24.0, 1)
            else:
                # Invert the accumulation envelope (1 - e^(-k_e*t)) to get
                # the equivalent "accumulation age", then subtract from
                # t_90 (time to reach 90% of asymptote from zero).
                t_now = -math.log(1.0 - p) / k_e
                t_90 = -math.log(1.0 - STEADY_STATE_FRACTION) / k_e
                remaining_hours = max(0.0, t_90 - t_now)
                self._attr_native_value = round(remaining_hours / 24.0, 1)

        self._write_attrs(c_max_ss, c_min_ss, threshold, current_mass, trough_now, tau)

    def _write_attrs(  # noqa: PLR0913 - internal attribute helper; 6 derived values are all used
        self,
        c_max_ss: float,
        c_min_ss: float,
        threshold: float,
        current_mass: float,
        trough_now: float,
        tau: float,
    ) -> None:
        """Write the steady-state attributes and push the state."""
        # current_percentage: progress toward the TROUGH asymptote (the
        # clinically relevant marker), not the peak. ~100% once reached.
        denom = c_min_ss if c_min_ss > 0 else 1.0
        pct = (trough_now / denom) * 100 if trough_now > 0 else 0.0

        self._attr_extra_state_attributes = {
            "theoretical_max_mg": round(c_max_ss, 1),
            "steady_state_trough_mg": round(c_min_ss, 1),
            "threshold_mg": round(threshold, 2),
            "current_mass": round(current_mass, 2),
            "projected_trough_mg": round(trough_now, 2),
            "current_percentage": round(pct, 1),
            "dosing_interval_hours": round(tau, 2),
            "last_dose_timestamp": self._last_dose_timestamp.isoformat() if self._last_dose_timestamp else None,
        }
        self.async_write_ha_state()
