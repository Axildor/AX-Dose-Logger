#!/usr/bin/env python3
"""
Standalone verification of the Low-Time / Hours-Until sensor behaviour.

Confirms two properties against a live ``DrinkMasterCoordinator`` (no HA
imports — ``dt_util.now()`` is monkeypatched to a fixed deterministic clock):

1. **Peak-anchored gate** — the sensors emit a value the instant a caffeine
   dose is logged (forecasted peak already exceeds the Low band), instead of
   staying ``unknown`` until the instantaneous body-mass crosses the
   threshold.
2. **Correct Low boundary** — ``DRINK_LOW_THRESHOLD`` is the UPPER bound of
   the Low band (Moderate -> Low crossing): 31 mg caffeine / 11 g alcohol.
   The sensors predict decay to THAT boundary (not the lower None/Low
   boundary), and go ``None`` once the body-mass is at or below it.

Run:  python3 scripts/sim_low_estimate.py
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath("custom_components"))

import ax_dose_logger.drink_coordinator as dc
from ax_dose_logger.const import (
    DRINK_LOW_THRESHOLD,
    DRINK_TYPE_ALCOHOL,
    DRINK_TYPE_CAFFEINE,
)

# Fixed clock so the simulation is deterministic.
CLOCK = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017


class FakeDT:
    """Monkeypatch for homeassistant.util.dt — returns the fixed CLOCK."""

    def now(self, tz=None):
        return CLOCK


# Patch dt_util on the coordinator module before any coordinator uses it.
dc.dt_util = FakeDT()


class FakeHass:
    pass


class FakeStore:
    def get_drink_master(self, substance):
        return {}


class FakeEntry:
    entry_id = "fake_settings"
    data = {}
    options = {}


def _make_master(substance: str) -> dc.DrinkMasterCoordinator:
    """Construct a DrinkMasterCoordinator without HA's DataUpdateCoordinator.

    base-class machinery (which requires a real ConfigEntry with
    ``async_on_unload``).  We only need the PK methods
    (``_recompute_data`` / ``estimate_time_to_body_mass`` / ``_forecast_caffeine_peak``)
    which depend solely on ``self._substance``, the PK constants, and
    ``self.data`` — so we bypass ``__init__`` and set those fields directly.
    """
    master = dc.DrinkMasterCoordinator.__new__(dc.DrinkMasterCoordinator)
    master._substance = substance
    master._store = FakeStore()
    master._store_key = f"fake_key_{substance}"
    # PK defaults (mirrors update_global_constants on a FakeEntry with empty opts/data).
    master._caffeine_half_life = dc.GLOBAL_PK_DEFAULTS["global_caffeine_half_life"]
    master._caffeine_tmax = dc.GLOBAL_PK_DEFAULTS["global_caffeine_tmax"]
    master._alcohol_elimination_rate = dc.GLOBAL_PK_DEFAULTS["global_alcohol_elimination_rate"]
    master._last_decay = None
    master.data = dc.DrinkMasterCoordinatorData()
    return master


def _run(substance: str, dose_strength: float, t_dur_hours: float, label: str) -> None:
    global CLOCK  # noqa: PLW0603 — deterministic clock for the simulation
    CLOCK = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
    master = _make_master(substance)
    # Mimic _async_setup: load empty history, then _recompute_data (Fix E).
    master.data = master._recompute_data()

    threshold = DRINK_LOW_THRESHOLD[substance]
    print(f"\n=== {label} ===  (substance={substance}, strength={dose_strength})")
    print(f"  DRINK_LOW_THRESHOLD (Moderate->Low upper bound) = {threshold}")

    def snapshot(minutes: int) -> None:
        data = master.data
        anchor = float(data.peak_body_mass) if data.peak_body_mass else float(data.body_mass)
        eta = master.estimate_time_to_body_mass(threshold)
        low_time = CLOCK + eta if eta is not None else None
        gate_passes = anchor > threshold
        print(
            f"t={minutes:4d}min: body={data.body_mass:7.3f} peak={data.peak_body_mass:7.3f} "
            f"anchor(peak_or_body)={anchor:7.3f} "
            f"gate_passes={'YES' if gate_passes else 'no '} "
            f"eta_low={eta} low_time={low_time.isoformat() if low_time else None}"
        )
        if not gate_passes and data.body_mass <= threshold:
            print(f"          -> body {data.body_mass:.3f} <= threshold {threshold}: "
                  f"sensor correctly reads None (already in Low band or below)")

    snapshot(0)  # pre-dose baseline

    # Log the drink (mimic DrinkLogButton.press -> master.async_add_dose).
    drink_time = CLOCK
    print(f"  -- logging {dose_strength} at {drink_time.isoformat()} (t_dur={t_dur_hours}h) --")
    # async_add_dose is a coroutine; run it synchronously via the loop is overkill
    # for a pure-PK test, so replicate its mutation + _push_update inline.
    master.data.dose_history.append((drink_time, dose_strength, t_dur_hours))
    master.data.last_dose_time = drink_time
    if substance == DRINK_TYPE_ALCOHOL:
        master.data.body_mass += dose_strength
    master.data = master._recompute_data()

    for minutes in [0, 5, 15, 30, 60, 120, 240]:
        CLOCK = drink_time + timedelta(minutes=minutes)
        master.data = master._recompute_data()
        snapshot(minutes)


def main() -> None:
    # Caffeine: 90 mg, 15-min drink duration.
    #   - t=0min: peak-anchored gate passes (forecasted peak > 31 mg) so the
    #     sensor emits a real Low time immediately. eta targets crossing 31 mg
    #     (Moderate -> Low), NOT 11 mg.
    #   - late samples: once body decays to <= 31 mg the gate fails and the
    #     sensor reads None (Low band reached / entered).
    _run(DRINK_TYPE_CAFFEINE, 90.0, 0.25, "Caffeine 90mg / 15min")
    # Alcohol: 14 g, instant absorption.
    #   - t=0min: gate passes (peak == body == 14 > 11 g). eta targets
    #     crossing 11 g (Moderate -> Low), NOT 1 g.
    #   - late samples: once body decays to <= 11 g the sensor reads None.
    _run(DRINK_TYPE_ALCOHOL, 14.0, 0.0, "Alcohol 14g / instant")

    print("\n=== Interpretation ===")
    print("DRINK_LOW_THRESHOLD is the UPPER bound of the Low band:")
    print(f"  caffeine = {DRINK_LOW_THRESHOLD[DRINK_TYPE_CAFFEINE]} mg (Moderate->Low)")
    print(f"  alcohol  = {DRINK_LOW_THRESHOLD[DRINK_TYPE_ALCOHOL]} g  (Moderate->Low)")
    print("t=0min gate_passes=YES (peak-anchored for caffeine; instant for alcohol).")
    print("eta_low targets the Moderate->Low boundary; sensor reads None once")
    print("body-mass is at or below that boundary (Low band reached).")


if __name__ == "__main__":
    main()
