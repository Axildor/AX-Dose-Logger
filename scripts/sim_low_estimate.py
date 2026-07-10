#!/usr/bin/env python3
"""
Standalone verification that the Low-Time / Hours-Until sensors emit a value
the instant a caffeine dose is logged (peak-anchored), instead of staying
``unknown`` until the instantaneous body-mass crosses the Low threshold.

No HA imports are needed — ``dt_util.now()`` is monkeypatched to a fixed clock
so the simulation is deterministic.  Reproduces the sensor gate logic from
``sensors/drink_master_sleep_disruption.py`` against a live
``DrinkMasterCoordinator`` to confirm the post-fix behaviour.

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

    def snapshot(minutes: int) -> None:
        data = master.data
        anchor = float(data.peak_body_mass) if data.peak_body_mass else float(data.body_mass)
        eta = master.estimate_time_to_body_mass(threshold)
        low_time = CLOCK + eta if eta is not None else None
        print(
            f"t={minutes:4d}min: body={data.body_mass:7.3f} peak={data.peak_body_mass:7.3f} "
            f"anchor(peak_or_body)={anchor:7.3f} "
            f"gate_passes={'YES' if anchor > threshold else 'no '} "
            f"eta_low={eta} low_time={low_time.isoformat() if low_time else None}"
        )

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
    # Caffeine: 90 mg, 15-min drink duration. Pre-fix the sensors stayed
    # ``unknown`` until the body-mass rose above 11 mg (~30+ min).  Post-fix
    # (peak-anchored) the gate passes at t=0 because the forecasted peak
    # immediately exceeds 11 mg.
    _run(DRINK_TYPE_CAFFEINE, 90.0, 0.25, "Caffeine 90mg / 15min")
    # Alcohol: 14 g, instant absorption. Already worked pre-fix; confirm no
    # regression — gate passes at t=0 because peak == body == 14 > 1.
    _run(DRINK_TYPE_ALCOHOL, 14.0, 0.0, "Alcohol 14g / instant")

    print("\n=== Interpretation ===")
    print("Caffeine t=0min gate_passes should be YES post-fix (peak-anchored).")
    print("Pre-fix it was 'no' (body still ~0) -> sensors read unknown.")
    print("Alcohol t=0min gate_passes is YES both pre+post (instant absorption).")


if __name__ == "__main__":
    main()
