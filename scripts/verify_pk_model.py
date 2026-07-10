#!/usr/bin/env python3
"""
Verify the extracted PKModel produces a continuous, mass-conserving curve.

This script imports :mod:`custom_components.ax_dose_logger.pk_model` directly —
no Home Assistant stubbing is required because the module is pure math.

It ports the three assertions from the legacy ``scripts/verify_fix.py`` and
adds one IR-model test:

1. **ER continuity** — 12 h curve at 1-min steps, max step-to-step body
   jump < 15 mg (accommodates the fast IR-coat rise while catching genuine
   cliff drops, e.g. at ``t = T_dur``).
2. **ER mass balance at t=0** — ``gut_ir + matrix_sr == dose``.
3. **ER continuity at T_dur** — ``|body(8h+eps) - body(8h-eps)| < 0.01``.
4. **IR mass balance at t=0** — ``gut == dose``.

The former 5th test (``decay_ir`` matches full recalc) was removed when
``PKModel.decay_ir`` was deleted as dead code — the incremental-decay
optimization it validated is no longer used (the coordinator does a
full-history recompute on every 1-min tick instead).
"""

import os
import sys
from datetime import datetime, timedelta

# Make the custom_components package importable without HA.
sys.path.insert(0, os.path.abspath("custom_components"))
from ax_dose_logger.pk_model import PKModel, PKParams

# ---------------------------------------------------------------------------
# ER model parameters (same as the legacy verify_fix.py user config)
# ---------------------------------------------------------------------------
ER_PARAMS = PKParams(
    release_type="Sustained Release",
    strength=665.0,
    half_life=2.0,
    hours_to_peak=2.8,
    bioavailability=100.0,
    ir_fraction=31.0,
    zero_order_duration=8.0,
    release_half_life=0.0,
    lag_time=0.0,
    ir_hours_to_peak=1.0,
)

IR_PARAMS = PKParams(
    release_type="Instant Release",
    strength=100.0,
    half_life=4.0,
    hours_to_peak=1.0,
    bioavailability=100.0,
    ir_fraction=100.0,
    zero_order_duration=0.0,
    release_half_life=0.0,
    lag_time=0.0,
    ir_hours_to_peak=1.0,
)

DOSE_TIME = datetime(2026, 6, 13, 18, 28, 0)


def test_er_continuity():
    """ER curve must be continuous (no cliff drops)."""
    dose_history = [(DOSE_TIME, 665.0)]
    print("=== ER curve (user params) ===")
    print("t_hours, body, gut_ir, matrix_sr, gut_sr, total_in_system")
    prev = None
    max_jump = 0.0
    for i in range(721):  # 0 to 12h in 1-min steps
        t_h = i / 60.0
        now = DOSE_TIME + timedelta(hours=t_h)
        r = PKModel.compute(ER_PARAMS, dose_history, now)
        total = r.body + r.gut_ir + r.matrix_sr + r.gut_sr
        if prev is not None:
            max_jump = max(max_jump, abs(r.body - prev))
        prev = r.body
        if i % 30 == 0 or abs(t_h - 8.0) < 0.02:
            print(f"{t_h:6.3f}, {r.body:7.2f}, {r.gut_ir:6.2f}, {r.matrix_sr:6.2f}, {r.gut_sr:6.2f}, {total:7.2f}")

    print()
    print(f"Max step-to-step body jump (1-min sampling): {max_jump:.4f} mg")
    assert max_jump < 15.0, f"FAIL: cliff drop detected ({max_jump:.2f} mg)"
    print("PASS: no cliff drop (continuity verified)")


def test_er_mass_balance_t0():
    """At t=0 all dose sits in gut_ir + matrix (no teleport into body)."""
    dose_history = [(DOSE_TIME, 665.0)]
    r = PKModel.compute(ER_PARAMS, dose_history, DOSE_TIME)
    assert abs(r.gut_ir + r.matrix_sr - 665.0) < 0.01, (
        f"FAIL: mass not conserved at t=0 (gut_ir={r.gut_ir}, matrix={r.matrix_sr})"
    )
    print(f"PASS: mass conserved at t=0 (gut_ir + matrix = {r.gut_ir + r.matrix_sr:.2f})")


def test_er_continuity_at_tdur():
    """Body must be continuous across the Phase 1 → Phase 2 boundary."""
    dose_history = [(DOSE_TIME, 665.0)]
    eps = timedelta(seconds=1e-3)
    r_before = PKModel.compute(ER_PARAMS, dose_history, DOSE_TIME + timedelta(hours=8.0) - eps)
    r_after = PKModel.compute(ER_PARAMS, dose_history, DOSE_TIME + timedelta(hours=8.0) + eps)
    jump = r_after.body - r_before.body
    print(f"Continuity at T_dur: body(8h-eps)={r_before.body:.4f}, body(8h+eps)={r_after.body:.4f}, jump={jump:.6f}")
    assert abs(jump) < 0.01, f"FAIL: discontinuity at T_dur ({jump:.6f})"
    print("PASS: continuous at t = T_dur")


def test_ir_mass_balance_t0():
    """IR model: at t=0 all dose sits in the gut compartment."""
    dose_history = [(DOSE_TIME, 100.0)]
    r = PKModel.compute(IR_PARAMS, dose_history, DOSE_TIME)
    assert abs(r.gut_ir - 100.0) < 0.01, f"FAIL: IR mass not conserved at t=0 (gut={r.gut_ir})"
    print(f"PASS: IR mass conserved at t=0 (gut = {r.gut_ir:.2f})")


def main():
    print("=" * 60)
    print("PKModel verification — no HA imports required")
    print("=" * 60)
    print()
    test_er_continuity()
    print()
    test_er_mass_balance_t0()
    print()
    test_er_continuity_at_tdur()
    print()
    test_ir_mass_balance_t0()
    print()
    print("=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
