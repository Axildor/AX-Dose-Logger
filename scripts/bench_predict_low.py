#!/usr/bin/env python3
"""Benchmark predict_low_time_if_dose before/after the solve_ka lru_cache.

No HA imports — monkeypatches dt_util + bypasses __init__ (see sim_low_estimate.py).
Run:  python3 scripts/bench_predict_low.py
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath("custom_components"))

import ax_dose_logger.drink_coordinator as dc
from ax_dose_logger.const import DRINK_TYPE_CAFFEINE

CLOCK = datetime(2026, 7, 9, 6, 0, 0, tzinfo=timezone.utc)  # noqa: UP017


class FakeDT:
    """Monkeypatch for homeassistant.util.dt — returns the fixed CLOCK."""

    def now(self, tz=None):
        return CLOCK


dc.dt_util = FakeDT()


class FakeStore:
    def get_drink_master(self, substance):
        return {}


def _make_master():
    """Bypass DataUpdateCoordinator.__init__ (needs a real ConfigEntry)."""
    master = dc.DrinkMasterCoordinator.__new__(dc.DrinkMasterCoordinator)
    master._substance = DRINK_TYPE_CAFFEINE
    master._store = FakeStore()
    master._store_key = "fake"
    master._caffeine_half_life = dc.GLOBAL_PK_DEFAULTS["global_caffeine_half_life"]
    master._caffeine_tmax = dc.GLOBAL_PK_DEFAULTS["global_caffeine_tmax"]
    master._alcohol_elimination_rate = dc.GLOBAL_PK_DEFAULTS["global_alcohol_elimination_rate"]
    master._last_decay = None
    master.data = dc.DrinkMasterCoordinatorData()
    return master


def main() -> None:
    global CLOCK  # noqa: PLW0603 — deterministic clock for the benchmark
    master = _make_master()

    for n_doses in [5, 20, 50, 100, 200]:
        master.data = dc.DrinkMasterCoordinatorData()
        CLOCK = datetime(2026, 7, 9, 0, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        for _i in range(n_doses):
            master.data.dose_history.append((CLOCK, 90.0, 0.25))
            CLOCK += timedelta(hours=1)
        master.data.last_dose_time = master.data.dose_history[-1][0]
        master.data = master._recompute_data()

        t0 = time.perf_counter()
        result = master.predict_low_time_if_dose(90.0, 0.25)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        print(f"N={n_doses:3d} doses: predict_low={elapsed_ms:8.1f} ms  result={result is not None}")

    from ax_dose_logger.pk_model import PKModel

    print("\nsolve_ka cache_info:", PKModel.solve_ka.cache_info())


if __name__ == "__main__":
    main()

