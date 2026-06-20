"""Pure-math pharmacokinetic model for the Pill Logger integration.

This module contains NO Home Assistant imports — only ``math`` and
``dataclasses`` — so it can be unit-tested standalone via
``scripts/verify_pk_model.py``.

The model implements two compartmental pharmacokinetic schemes:

* **IR (Instant Release)** — a 2-compartment Bateman equation
  (gut → body, first-order absorption + first-order elimination).
* **ER (Sustained Release)** — a 4-compartment hybrid model
  (gut_ir, matrix_sr, gut_sr, body) combining a fast IR coat with a
  zero-order + first-order SR matrix release.

Both models are *linear*, so the total drug in each compartment at any
time equals the superposition (sum) of every individual dose's
contribution.  This makes full-history recalculation the exact, drift-free
reference implementation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

__all__ = ["PKParams", "PKResult", "PKModel"]

# Tolerance used to decide whether two rate constants are "equal".
# Using an epsilon instead of exact float equality avoids spurious
# division-by-zero in the limiting-case branches of the Bateman equation.
_EPS = 0.0001


@dataclass(frozen=True)
class PKParams:
    """All pharmacokinetic parameters for one medication config entry.

    Built by the sensor from ``ConfigEntry`` data/options each time a
    recalculation is performed (parameters may change via the options flow).
    """

    release_type: str           # "instant_release" | "sustained_release"
    strength: float             # mg per dose (raw; bioavailability applied at compute time)
    half_life: float            # elimination half-life (hours)
    hours_to_peak: float        # SR absorption time-to-peak (hours)
    bioavailability: float      # % (0–100)
    ir_fraction: float          # % of dose in the IR coat (0–100, ER only)
    zero_order_duration: float  # T_dur: zero-order release window (hours, ER only)
    release_half_life: float    # SR matrix first-order tail half-life (hours, ER only)
    lag_time: float             # absorption lag (minutes)
    ir_hours_to_peak: float     # IR coat absorption time-to-peak (hours, ER only)


@dataclass(frozen=True)
class PKResult:
    """Compartment masses returned by :meth:`PKModel.compute`.

    For IR mode ``gut_ir`` holds the gut mass, ``matrix_sr`` and
    ``gut_sr`` are always 0, and ``kr`` is always 0.
    """

    body: float       # A_B: drug in the central (body) compartment (mg)
    gut_ir: float     # A_G_IR: IR gut compartment (mg)
    matrix_sr: float  # A_M_SR: SR matrix compartment (mg)
    gut_sr: float     # A_G_SR: SR gut compartment (mg)
    ka: float         # SR absorption rate constant (h⁻¹), cached for attributes
    kr: float         # SR matrix first-order release rate (h⁻¹), cached for attributes


class PKModel:
    """Stateless pharmacokinetic computation engine.

    All methods are static; the class holds no instance state.  Callers
    build a :class:`PKParams` and pass a dose history
    (``list[(datetime, float)]``) plus the evaluation ``now`` timestamp.
    """

    # ------------------------------------------------------------------
    # Absorption-rate solver
    # ------------------------------------------------------------------
    @staticmethod
    def solve_ka(t_max: float, k_e: float) -> float:
        """Solve for the absorption rate constant ``k_a`` given a desired
        time-to-peak ``t_max`` and elimination rate ``k_e``.

        Uses the standard pharmacokinetic relationship::

            t_max = ln(k_a / k_e) / (k_a - k_e)

        which has no closed-form solution for ``k_a``, so a binary search
        is used.
        """
        low, high = 0.0001, 20.0
        for _ in range(50):
            mid_ka = (low + high) / 2
            if mid_ka == k_e:
                mid_ka += 0.0001
            try:
                calc_t_max = (math.log(mid_ka) - math.log(k_e)) / (mid_ka - k_e)
                if calc_t_max < t_max:
                    high = mid_ka
                else:
                    low = mid_ka
            except (ValueError, ZeroDivisionError):
                low = mid_ka
        return (low + high) / 2

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    @staticmethod
    def compute(params: PKParams, dose_history: Sequence[tuple[datetime, float]],
                now: datetime) -> PKResult:
        """Recalculate all compartments from the full dose history.

        Routes to the IR or ER model based on ``params.release_type``.
        Returns a :class:`PKResult` with the four compartment masses and
        the cached rate constants.
        """
        if params.release_type == "sustained_release":
            return PKModel._compute_er(params, dose_history, now)
        return PKModel._compute_ir(params, dose_history, now)

    # ------------------------------------------------------------------
    # IR model (2-compartment Bateman)
    # ------------------------------------------------------------------
    @staticmethod
    def _compute_ir(params: PKParams, dose_history: Sequence[tuple[datetime, float]],
                    now: datetime) -> PKResult:
        """Standard 2-compartment IR model (Bateman equation) via superposition."""
        k_e = math.log(2) / params.half_life if params.half_life > 0 else 0
        k_a = PKModel.solve_ka(params.hours_to_peak, k_e) if params.hours_to_peak > 0 else 0
        if k_a > 0 and abs(k_a - k_e) < _EPS:
            k_a *= 1.0001  # Avoid division by zero in the limiting case

        F = float(params.bioavailability) / 100.0
        lag = float(params.lag_time) / 60.0 if params.lag_time else 0.0  # minutes → hours
        total_body = 0.0
        total_gut = 0.0

        for dose_time, dose_strength in dose_history:
            t = (now - dose_time).total_seconds() / 3600.0
            if t < 0:
                continue
            t_eff = t - lag
            if t_eff < 0:
                continue  # Dose hasn't started releasing yet

            effective_dose = F * dose_strength
            if k_a > 0 and abs(k_a - k_e) > _EPS:
                # Two-compartment Bateman equation (distinct rates)
                total_gut += effective_dose * math.exp(-k_a * t_eff)
                total_body += effective_dose * k_a / (k_a - k_e) * (
                    math.exp(-k_e * t_eff) - math.exp(-k_a * t_eff)
                )
            elif k_a > 0:
                # Limiting case k_a ≈ k_e
                total_gut += effective_dose * math.exp(-k_a * t_eff)
                total_body += effective_dose * k_a * t_eff * math.exp(-k_a * t_eff)
            else:
                # Immediate release (no absorption phase)
                if k_e > 0:
                    total_body += effective_dose * math.exp(-k_e * t_eff)
                else:
                    total_body += effective_dose

        return PKResult(
            body=total_body,
            gut_ir=total_gut,
            matrix_sr=0.0,
            gut_sr=0.0,
            ka=k_a,
            kr=0.0,
        )

    # ------------------------------------------------------------------
    # ER model (4-compartment hybrid IR + SR)
    # ------------------------------------------------------------------
    @staticmethod
    def _compute_er(params: PKParams, dose_history: Sequence[tuple[datetime, float]],
                    now: datetime) -> PKResult:
        """4-compartment ER model (hybrid IR coat + SR matrix).

        For each dose:
        - **IR component**: standard Bateman equation with
          ``D_IR = F * strength * ir_fraction``, absorbed via a fast
          ``k_a_ir`` solved from ``ir_hours_to_peak``.
        - **SR component**: piecewise analytical solution.
          Phase 1 (``0 ≤ t ≤ T_dur``): zero-order release at
          ``R0 = D_SR / T_dur``.
          Phase 2 (``t > T_dur``): first-order release with
          ``k_r = ln(2) / release_half_life``.

        PK note: in Phase 2 the forward-decay of the Phase 1 state
        (body + gut_sr) depends ONLY on the natural rates ``k_a`` and
        ``k_e``, so it is applied unconditionally.  The additional matrix
        first-order release terms depend on ``k_r`` and are gated on
        ``k_r > 0``.  This prevents the cliff drop at ``t = T_dur`` when
        ``k_r = 0`` (no first-order tail configured).
        """
        k_e = math.log(2) / params.half_life if params.half_life > 0 else 0
        k_a = PKModel.solve_ka(params.hours_to_peak, k_e) if params.hours_to_peak > 0 else 0
        if k_a > 0 and abs(k_a - k_e) < _EPS:
            k_a *= 1.0001

        # Fast absorption rate for the IR fraction (D_IR).  Solved from
        # ir_hours_to_peak (default 1.0 h) — decoupled from the slow ER
        # k_a that governs the SR matrix.  Gives the IR coat a realistic
        # fast absorption phase instead of teleporting into the body.
        k_a_ir = PKModel.solve_ka(params.ir_hours_to_peak, k_e) if k_e > 0 else 0
        if k_a_ir > 0 and abs(k_a_ir - k_e) < _EPS:
            k_a_ir *= 1.0001

        k_r = math.log(2) / params.release_half_life if params.release_half_life > 0 else 0
        T_dur = float(params.zero_order_duration)
        F = float(params.bioavailability) / 100.0
        ir_frac = float(params.ir_fraction) / 100.0
        lag = float(params.lag_time) / 60.0 if params.lag_time else 0.0

        total_body = 0.0
        total_gut_ir = 0.0
        total_matrix_sr = 0.0
        total_gut_sr = 0.0

        for dose_time, dose_strength in dose_history:
            t = (now - dose_time).total_seconds() / 3600.0
            if t < 0:
                continue
            t_eff = t - lag
            if t_eff < 0:
                continue

            effective_dose = F * dose_strength
            D_IR = effective_dose * ir_frac
            D_SR = effective_dose * (1 - ir_frac)

            # --- IR component: dual-absorption Bateman via k_a_ir ---
            if D_IR > 0:
                if k_a_ir > 0 and abs(k_a_ir - k_e) > _EPS:
                    total_gut_ir += D_IR * math.exp(-k_a_ir * t_eff)
                    total_body += D_IR * k_a_ir / (k_a_ir - k_e) * (
                        math.exp(-k_e * t_eff) - math.exp(-k_a_ir * t_eff)
                    )
                elif k_a_ir > 0:
                    total_gut_ir += D_IR * math.exp(-k_a_ir * t_eff)
                    total_body += D_IR * k_a_ir * t_eff * math.exp(-k_a_ir * t_eff)
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

                    if k_a > 0 and abs(k_a - k_e) > _EPS:
                        total_gut_sr += (R0 / k_a) * (1 - math.exp(-k_a * t_eff))
                        total_body += (R0 / k_e) * (1 - math.exp(-k_e * t_eff)) - \
                                      (R0 * k_a) / ((k_a - k_e) * k_e) * (
                                          math.exp(-k_e * t_eff) - math.exp(-k_a * t_eff)
                                      )
                    elif k_a > 0:
                        total_gut_sr += (R0 / k_a) * (1 - math.exp(-k_a * t_eff))
                        total_body += (R0 / k_e) * (1 - math.exp(-k_e * t_eff)) - \
                                      (R0 / k_e) * k_a * t_eff * math.exp(-k_e * t_eff)
                    else:
                        total_gut_sr += R0 * t_eff
                        if k_e > 0:
                            total_body += (R0 / k_e) * (1 - math.exp(-k_e * t_eff))
                        else:
                            total_body += R0 * t_eff

                else:
                    # Phase 2: first-order tail (t_eff > T_dur)
                    M_SR_at_T = D_SR - R0 * T_dur  # Remaining matrix at T_dur

                    if k_a > 0 and abs(k_a - k_e) > _EPS:
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

                    # Matrix decay: M_SR(t) = M_SR(T_dur) * exp(-k_r * tau)
                    if k_r > 0:
                        total_matrix_sr += M_SR_at_T * math.exp(-k_r * tau)
                    else:
                        total_matrix_sr += M_SR_at_T

                    # --- Forward-decay of Phase 1 state (ALWAYS applies) ---
                    if k_a > 0 and abs(k_a - k_e) > _EPS:
                        total_body += B_SR_at_T * math.exp(-k_e * tau) + \
                                      G_SR_at_T * k_a / (k_a - k_e) * (
                                          math.exp(-k_e * tau) - math.exp(-k_a * tau))
                        total_gut_sr += G_SR_at_T * math.exp(-k_a * tau)
                    elif k_a > 0:
                        total_body += B_SR_at_T * math.exp(-k_e * tau) + \
                                      G_SR_at_T * k_a * tau * math.exp(-k_e * tau)
                        total_gut_sr += G_SR_at_T * math.exp(-k_a * tau)
                    else:
                        total_body += B_SR_at_T * math.exp(-k_e * tau) if k_e > 0 else B_SR_at_T

                    # --- Additional matrix first-order release (ONLY when k_r > 0) ---
                    if k_r > 0 and M_SR_at_T > 1e-9 and k_a > 0 and abs(k_a - k_e) > _EPS:
                        if abs(k_r - k_e) > _EPS and abs(k_r - k_a) > _EPS:
                            total_body += k_r * M_SR_at_T * k_a / (k_a - k_e) * (
                                (math.exp(-k_e * tau) - math.exp(-k_a * tau)) / (k_r - k_e) -
                                (math.exp(-k_r * tau) - math.exp(-k_a * tau)) / (k_r - k_a)
                            )
                        elif abs(k_r - k_e) <= _EPS and abs(k_r - k_a) > _EPS:
                            total_body += k_r * M_SR_at_T * k_a / (k_a - k_e) * (
                                tau * math.exp(-k_e * tau) +
                                (math.exp(-k_a * tau) - math.exp(-k_e * tau)) / (k_a - k_e)
                            )
                        elif abs(k_r - k_a) <= _EPS and abs(k_r - k_e) > _EPS:
                            total_body += k_r * k_a * M_SR_at_T / (k_a - k_e) * (
                                (math.exp(-k_e * tau) - math.exp(-k_a * tau)) / (k_a - k_e) -
                                tau * math.exp(-k_a * tau)
                            )
                        else:
                            k = (k_r + k_a + k_e) / 3.0
                            total_body += k * k * M_SR_at_T * tau * tau / 2.0 * math.exp(-k * tau)

                        if abs(k_a - k_r) > _EPS:
                            total_gut_sr += k_r * M_SR_at_T / (k_a - k_r) * (
                                math.exp(-k_r * tau) - math.exp(-k_a * tau)
                            )
                        else:
                            total_gut_sr += k_r * M_SR_at_T * tau * math.exp(-k_a * tau)

                    elif k_r > 0 and M_SR_at_T > 1e-9 and k_a > 0:
                        total_body += k_r * M_SR_at_T * k_a * tau * math.exp(-k_e * tau)
                        total_gut_sr += k_r * M_SR_at_T * tau * math.exp(-k_a * tau)

                    elif k_r > 0 and M_SR_at_T > 1e-9:
                        if k_e > 0 and abs(k_r - k_e) > _EPS:
                            total_body += k_r * M_SR_at_T / (k_r - k_e) * (
                                math.exp(-k_e * tau) - math.exp(-k_r * tau)
                            )
                        elif k_e > 0:
                            total_body += k_r * M_SR_at_T * tau * math.exp(-k_e * tau)
                        else:
                            total_body += M_SR_at_T * (1 - math.exp(-k_r * tau))

            elif D_SR > 0 and k_r > 0:
                # No zero-order phase: pure first-order SR release
                total_matrix_sr += D_SR * math.exp(-k_r * t_eff)

                if abs(k_r - k_a) > _EPS:
                    total_gut_sr += D_SR * k_r / (k_r - k_a) * (
                        math.exp(-k_a * t_eff) - math.exp(-k_r * t_eff)
                    )
                else:
                    total_gut_sr += D_SR * k_r * t_eff * math.exp(-k_a * t_eff)

                if k_a > 0 and abs(k_a - k_e) > _EPS:
                    if abs(k_r - k_e) > _EPS and abs(k_r - k_a) > _EPS:
                        total_body += D_SR * k_r * k_a * (
                            math.exp(-k_e * t_eff) / ((k_r - k_e) * (k_a - k_e)) +
                            math.exp(-k_r * t_eff) / ((k_e - k_r) * (k_a - k_r)) +
                            math.exp(-k_a * t_eff) / ((k_e - k_a) * (k_r - k_a))
                        )
                    elif abs(k_r - k_a) <= _EPS:
                        k = k_r
                        total_body += D_SR * k * k / ((k - k_e) ** 2) * (
                            math.exp(-k_e * t_eff) - math.exp(-k * t_eff) * (1 + (k - k_e) * t_eff)
                        )
                    elif abs(k_r - k_e) <= _EPS:
                        total_body += D_SR * k_r * k_a / ((k_a - k_e) ** 2) * (
                            math.exp(-k_e * t_eff) * (1 + (k_a - k_e) * t_eff) - math.exp(-k_a * t_eff)
                        )
                elif k_a > 0:
                    if abs(k_r - k_a) > _EPS:
                        total_body += D_SR * k_r * k_a / (k_r - k_a) * t_eff * math.exp(-k_a * t_eff)
                    else:
                        k = k_r
                        total_body += D_SR * k * k * t_eff * t_eff / 2.0 * math.exp(-k * t_eff)
                else:
                    if k_e > 0 and abs(k_r - k_e) > _EPS:
                        total_body += D_SR * k_r / (k_r - k_e) * (
                            math.exp(-k_e * t_eff) - math.exp(-k_r * t_eff)
                        )
                    elif k_e > 0:
                        total_body += D_SR * k_r * t_eff * math.exp(-k_e * t_eff)
                    else:
                        total_body += D_SR * (1 - math.exp(-k_r * t_eff))

            elif D_SR > 0:
                # Degenerate fallback: T_dur = 0 AND k_r = 0.
                # Treat SR fraction as instant release merged into the IR path.
                if k_a > 0 and abs(k_a - k_e) > _EPS:
                    total_gut_sr += D_SR * math.exp(-k_a * t_eff)
                    total_body += D_SR * k_a / (k_a - k_e) * (
                        math.exp(-k_e * t_eff) - math.exp(-k_a * t_eff)
                    )
                elif k_a > 0:
                    total_gut_sr += D_SR * math.exp(-k_a * t_eff)
                    total_body += D_SR * k_a * t_eff * math.exp(-k_a * t_eff)
                else:
                    if k_e > 0:
                        total_body += D_SR * math.exp(-k_e * t_eff)
                    else:
                        total_body += D_SR

        return PKResult(
            body=total_body,
            gut_ir=total_gut_ir,
            matrix_sr=total_matrix_sr,
            gut_sr=total_gut_sr,
            ka=k_a,
            kr=k_r,
        )

    # ------------------------------------------------------------------
    # Incremental IR decay (2-min timer optimization)
    # ------------------------------------------------------------------
    @staticmethod
    def decay_ir(params: PKParams, body: float, gut: float,
                 elapsed_hours: float) -> tuple[float, float]:
        """Decay the IR model compartments by ``elapsed_hours`` using the
        exact Bateman equation.

        This is an optimization used by the 2-minute decay timer: instead
        of re-iterating the full dose history, it advances the current
        ``(body, gut)`` state forward in time.  Returns the new
        ``(body, gut)`` tuple.
        """
        k_e = math.log(2) / params.half_life
        k_a = PKModel.solve_ka(params.hours_to_peak, k_e) if params.hours_to_peak > 0 else 0
        if k_a > 0 and abs(k_a - k_e) < _EPS:
            k_a *= 1.0001

        if params.hours_to_peak <= 0:
            new_gut = 0.0
            new_body = body * math.exp(-k_e * elapsed_hours)
        else:
            new_gut = gut * math.exp(-k_a * elapsed_hours)
            new_body = (body * math.exp(-k_e * elapsed_hours) +
                        (gut * k_a / (k_a - k_e)) *
                        (math.exp(-k_e * elapsed_hours) - math.exp(-k_a * elapsed_hours)))
        return new_body, new_gut