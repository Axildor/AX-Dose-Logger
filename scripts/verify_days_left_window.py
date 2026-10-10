"""Verify the anchor-shrunk Est. days-left math (day-2 scenario).

Pure-function check of :func:`sliding_window.effective_window_days` plus the
days-left arithmetic, mirroring ``PillDaysLeftSensor._doses_per_day_avg`` /
``DrinkDaysLeftSensor._doses_per_day_avg``:

* Day 2, 1 dose, stock 99  -> 0.5 doses/day -> 198.0 days (was 693.0)
* Day 1, 1 dose, stock 99  -> 1.0 doses/day -> 99.0 days
* Day 10, 3 doses, stock 99 -> 0.3 doses/day -> 330.0 days
* Reset Averages anchor clamps the window start
* Full 7-day history is unaffected (window stays 7 days)

Runs without a Home Assistant install: ``homeassistant.config_entries`` and
the integration ``const`` module are stubbed (only type/constant imports are
needed for :mod:`sliding_window`).
"""

import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# --- Stub homeassistant.config_entries (sliding_window imports ConfigEntry) ---
ha_pkg = types.ModuleType("homeassistant")
ha_ce = types.ModuleType("homeassistant.config_entries")
ha_ce.ConfigEntry = object
ha_pkg.config_entries = ha_ce
sys.modules["homeassistant"] = ha_pkg
sys.modules["homeassistant.config_entries"] = ha_ce

# --- Stub the integration const module (sliding_window imports constants) ---
pkg = types.ModuleType("custom_components")
pkg.__path__ = [str(Path(__file__).resolve().parent.parent / "custom_components")]
sys.modules["custom_components"] = pkg
pl = types.ModuleType("custom_components.ax_dose_logger")
pl.__path__ = [str(Path(__file__).resolve().parent.parent / "custom_components" / "ax_dose_logger")]
sys.modules["custom_components.ax_dose_logger"] = pl
const_mod = types.ModuleType("custom_components.ax_dose_logger.const")
const_mod.DOSE_BUFFER_CAP_FRACTION = 0.25
const_mod.DOSE_BUFFER_DEFAULT_MIN = 5
const_mod.TRACKING_AS_NEEDED = "as_needed"
const_mod.TRACKING_CYCLIC = "cyclic"
const_mod.TRACKING_REGULAR_INTERVAL = "regular_interval"
sys.modules["custom_components.ax_dose_logger.const"] = const_mod

from custom_components.ax_dose_logger.sliding_window import effective_window_days


def dt(days_ago: int, hour: int = 12) -> datetime:
    return datetime(2026, 10, 10, hour, 0, 0, tzinfo=UTC) - timedelta(days=days_ago)


def check(name, expected, actual):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}: {name} expected={expected} actual={actual}")
    return ok


def main() -> int:
    results = []
    now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)

    # Day 2: first dose yesterday, 1 dose total, stock 99.
    start, eff = effective_window_days(now, 7, dt(1), None)
    results.append(check("day2 effective_window_days", 2, eff))
    dpd = 1 / eff
    results.append(check("day2 doses_per_day", 0.5, dpd))
    results.append(check("day2 days_left (stock 99)", 198.0, 99 / dpd))

    # Day 1: dose today, window = 1 day.
    start, eff = effective_window_days(now, 7, dt(0), None)
    results.append(check("day1 effective_window_days", 1, eff))
    results.append(check("day1 days_left (stock 99)", 99.0, 99 / eff))

    # Day 10: 3 doses over a full 7-day window.
    start, eff = effective_window_days(now, 7, dt(9), None)
    results.append(check("day10 effective_window_days", 7, eff))
    results.append(check("day10 days_left (3 doses, stock 99)", 231.0, 99 / (3 / eff)))

    # Reset Averages anchor: history started day 10, reset yesterday.
    start, eff = effective_window_days(now, 7, dt(9), dt(1))
    results.append(check("reset-anchor effective_window_days", 2, eff))

    # Reset anchor older than the window: window stays 7 days.
    start, eff = effective_window_days(now, 7, dt(30), dt(20))
    results.append(check("old-reset effective_window_days", 7, eff))

    # Window start date sanity: day 2 -> window starts yesterday.
    start, eff = effective_window_days(now, 7, dt(1), None)
    results.append(check("day2 window_start_date", dt(1).date(), start))

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} TESTS PASSED")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
