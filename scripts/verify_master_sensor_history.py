"""Verify the Master Tracker sensors survive the B1 4-tuple dose history.

Regression harness for the "too many values to unpack (expected 3, got 4)"
crash that left ``sensor.caffeine_tracker_amount_in_last_24h``,
``sensor.caffeine_tracker_avg_daily_caffeine_*`` and
``sensor.caffeine_tracker_daily_remaining`` Unavailable on any instance whose
master store had dose history (the crash fired inside ``async_added_to_hass``
only when ``dose_history`` was non-empty — empty test-instance stores masked
the bug).

Covers the three sensor classes that iterate the master ``dose_history``:

* :class:`DrinkMasterDailyAmountSensor` — 24h sum over 4-tuple + legacy
  3-tuple rows + empty history.
* :class:`DrinkMasterDailyRemainingSensor` — limit − amount (incl. overage).
* :class:`DrinkMasterAvgDosesSensor` — anchor + doses_in_window / window.

Also cross-checks each sensor's generated ``unique_id`` against the v17
entity-registry re-link target format (``drink_master_{substance}_{suffix}``)
so the migration mapping and the sensor code cannot drift apart silently.

Runs without a Home Assistant install: the HA modules and the heavy
``drink_coordinator`` module are stubbed (the sensors only use the
coordinator as a data holder).
"""

import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# --- Stub the homeassistant package (only light imports are needed) ---
ha_pkg = types.ModuleType("homeassistant")
sys.modules["homeassistant"] = ha_pkg

ha_ce = types.ModuleType("homeassistant.config_entries")
ha_ce.ConfigEntry = object
sys.modules["homeassistant.config_entries"] = ha_ce
ha_pkg.config_entries = ha_ce

ha_core = types.ModuleType("homeassistant.core")


def _callback(fn):
    return fn


ha_core.callback = _callback
ha_core.HomeAssistant = object
sys.modules["homeassistant.core"] = ha_core
ha_pkg.core = ha_core

ha_util = types.ModuleType("homeassistant.util")
sys.modules["homeassistant.util"] = ha_util
ha_pkg.util = ha_util

# dt_util.now() is pinned to a module-level _NOW for deterministic asserts.
_NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)
ha_dt = types.ModuleType("homeassistant.util.dt")
ha_dt.now = lambda: _NOW
ha_dt.parse_datetime = datetime.fromisoformat
sys.modules["homeassistant.util.dt"] = ha_dt
ha_util.dt = ha_dt

ha_sensor = types.ModuleType("homeassistant.components.sensor")


class _SensorStateClass:
    MEASUREMENT = "measurement"


class _RestoreSensor:
    """Minimal RestoreSensor stand-in (restore paths are not exercised)."""

    async def async_get_last_state(self):
        return None

    async def async_get_last_sensor_data(self):
        return None


ha_sensor.RestoreSensor = _RestoreSensor
ha_sensor.SensorStateClass = _SensorStateClass
sys.modules["homeassistant.components.sensor"] = ha_sensor
ha_pkg.components = types.SimpleNamespace(sensor=ha_sensor)

ha_helpers = types.ModuleType("homeassistant.helpers")
sys.modules["homeassistant.helpers"] = ha_helpers
ha_pkg.helpers = ha_helpers

ha_dr = types.ModuleType("homeassistant.helpers.device_registry")


class _DeviceInfo:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


ha_dr.DeviceInfo = _DeviceInfo
sys.modules["homeassistant.helpers.device_registry"] = ha_dr
ha_helpers.device_registry = ha_dr

# --- Stub the integration package path ---
pkg = types.ModuleType("custom_components")
pkg.__path__ = [str(Path(__file__).resolve().parent.parent / "custom_components")]
sys.modules["custom_components"] = pkg
pl = types.ModuleType("custom_components.ax_dose_logger")
pl.__path__ = [str(Path(__file__).resolve().parent.parent / "custom_components" / "ax_dose_logger")]
sys.modules["custom_components.ax_dose_logger"] = pl

# --- Stub drink_coordinator (heavy; sensors only need the data holder) ---
dc = types.ModuleType("custom_components.ax_dose_logger.drink_coordinator")


class DrinkMasterCoordinator:
    """Stand-in exposing only what the master sensors read."""

    def __init__(self, substance, dose_history=None, avg_reset_time=None):
        self.substance = substance
        self.data = DrinkMasterCoordinatorData(dose_history=dose_history or [], avg_reset_time=avg_reset_time)


class DrinkMasterCoordinatorData:
    def __init__(self, dose_history, avg_reset_time=None):
        self.dose_history = dose_history
        self.avg_reset_time = avg_reset_time


dc.DrinkMasterCoordinator = DrinkMasterCoordinator
dc.DrinkMasterCoordinatorData = DrinkMasterCoordinatorData
sys.modules["custom_components.ax_dose_logger.drink_coordinator"] = dc

# const + sliding_window + _tracker_info are pure — import the real modules.
from custom_components.ax_dose_logger.const import (
    ALCOHOL_DEFAULT_LIMIT_G,
    CAFFEINE_DEFAULT_LIMIT_MG,
    DEFAULT_PROFILE_ID,
    DRINK_TYPE_CAFFEINE,
    master_unique_id,
)
from custom_components.ax_dose_logger.sensors._tracker_info import tracker_device_info
from custom_components.ax_dose_logger.sensors.drink_master_avg import (
    DrinkMasterAvgDosesSensor,
)
from custom_components.ax_dose_logger.sensors.drink_master_daily_amount import (
    DrinkMasterDailyAmountSensor,
)
from custom_components.ax_dose_logger.sensors.drink_master_daily_remaining import (
    DrinkMasterDailyRemainingSensor,
)


def ts(hours_ago: float) -> datetime:
    return _NOW - timedelta(hours=hours_ago)


def check(name, expected, actual):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}: {name} expected={expected} actual={actual}")
    return ok


class _FakeSettingsEntry:
    """Minimal config-entry stand-in for the daily-limit reads."""

    def __init__(self, options=None, data=None):
        self.options = options or {}
        self.data = data or {}


def make_daily_amount(coordinator, limit_mg=CAFFEINE_DEFAULT_LIMIT_MG):
    return DrinkMasterDailyAmountSensor(
        _FakeSettingsEntry(options={"caffeine_daily_limit_mg": limit_mg}),
        coordinator,
        DEFAULT_PROFILE_ID,
        None,
    )


def make_daily_remaining(coordinator, limit_mg=CAFFEINE_DEFAULT_LIMIT_MG):
    return DrinkMasterDailyRemainingSensor(
        _FakeSettingsEntry(options={"caffeine_daily_limit_mg": limit_mg}),
        coordinator,
        DEFAULT_PROFILE_ID,
        None,
    )


def make_avg(coordinator, window):
    sensor = DrinkMasterAvgDosesSensor(_FakeSettingsEntry(), coordinator, window, DEFAULT_PROFILE_ID, None)
    # async_added_to_hass normally anchors this; pin it for determinism.
    sensor._history_start_date = ts(24 * 30)  # 30 days of history
    return sensor


def main() -> int:
    results = []

    # --- 1. Daily amount over a 4-tuple history (the crash scenario) ---
    coord = DrinkMasterCoordinator(
        DRINK_TYPE_CAFFEINE,
        dose_history=[
            (ts(1), 100.0, 0.25, "entry_a"),  # in window
            (ts(30), 50.0, 0.25, "entry_a"),  # outside 24h
        ],
    )
    sensor = make_daily_amount(coord)
    sensor._update_state()
    results.append(check("daily_amount 4-tuple sum", 100.0, sensor._attr_native_value))
    results.append(
        check("daily_amount 4-tuple doses_in_window", 1, sensor._attr_extra_state_attributes["doses_in_window"])
    )
    results.append(check("daily_amount remaining attr", 300.0, sensor._attr_extra_state_attributes["remaining"]))

    # --- 2. Daily amount over a legacy 3-tuple row (defensive) ---
    coord_legacy = DrinkMasterCoordinator(
        DRINK_TYPE_CAFFEINE,
        dose_history=[(ts(2), 75.0, 0.25)],
    )
    sensor = make_daily_amount(coord_legacy)
    sensor._update_state()
    results.append(check("daily_amount legacy 3-tuple sum", 75.0, sensor._attr_native_value))

    # --- 3. Empty history (the test-instance case) -> 0.0, not a crash ---
    sensor = make_daily_amount(DrinkMasterCoordinator(DRINK_TYPE_CAFFEINE))
    sensor._update_state()
    results.append(check("daily_amount empty history", 0.0, sensor._attr_native_value))

    # --- 4. Daily remaining: limit − amount, negative = overage ---
    coord = DrinkMasterCoordinator(
        DRINK_TYPE_CAFFEINE,
        dose_history=[(ts(1), 450.0, 0.25, "entry_a")],
    )
    sensor = make_daily_remaining(coord)
    sensor._update_state()
    results.append(check("daily_remaining overage", -50.0, sensor._attr_native_value))

    coord = DrinkMasterCoordinator(
        DRINK_TYPE_CAFFEINE,
        dose_history=[(ts(1), 100.0, 0.25, "entry_a")],
    )
    sensor = make_daily_remaining(coord)
    sensor._update_state()
    results.append(check("daily_remaining normal", 300.0, sensor._attr_native_value))

    # --- 5. Avg doses: 3 doses in the last 7 days over a 30-day anchor ---
    coord = DrinkMasterCoordinator(
        DRINK_TYPE_CAFFEINE,
        dose_history=[
            (ts(24), 100.0, 0.25, "entry_a"),
            (ts(24 * 3), 100.0, 0.25, "entry_a"),
            (ts(24 * 6), 100.0, 0.25, "entry_a"),
            (ts(24 * 20), 100.0, 0.25, "entry_a"),  # outside 7d window
        ],
    )
    sensor = make_avg(coord, 7)
    sensor._update_state()
    results.append(check("avg_7 doses_in_window", 3, sensor._attr_extra_state_attributes["doses_in_window"]))
    results.append(check("avg_7 value", 0.4, sensor._attr_native_value))  # 3 / 7 rounded to 1dp

    # --- 6. Avg doses: empty history -> 0.0 ---
    sensor = make_avg(DrinkMasterCoordinator(DRINK_TYPE_CAFFEINE), 7)
    sensor._update_state()
    results.append(check("avg_7 empty history", 0.0, sensor._attr_native_value))

    # --- 7. unique_id cross-check vs the v17 re-link target format ---
    # The v17 migration re-links registry rows to drink_master_{sub}_{suffix};
    # the sensors must generate exactly that shape or the migration target
    # and the live entities drift apart again.
    expected_suffixes = {
        DrinkMasterDailyAmountSensor: "daily_amount",
        DrinkMasterDailyRemainingSensor: "daily_remaining",
    }
    for cls, suffix in expected_suffixes.items():
        coord = DrinkMasterCoordinator(DRINK_TYPE_CAFFEINE)
        sensor = cls(_FakeSettingsEntry(), coord, DEFAULT_PROFILE_ID, None)
        expected_uid = f"{master_unique_id(DEFAULT_PROFILE_ID, DRINK_TYPE_CAFFEINE)}_{suffix}"
        results.append(check(f"{cls.__name__} unique_id", expected_uid, sensor._attr_unique_id))

    coord = DrinkMasterCoordinator(DRINK_TYPE_CAFFEINE)
    sensor = DrinkMasterAvgDosesSensor(_FakeSettingsEntry(), coord, 7, DEFAULT_PROFILE_ID, None)
    results.append(
        check(
            "DrinkMasterAvgDosesSensor unique_id",
            f"{master_unique_id(DEFAULT_PROFILE_ID, DRINK_TYPE_CAFFEINE)}_avg_7",
            sensor._attr_unique_id,
        )
    )

    # --- 8. Alcohol defaults still resolve (no caffeine-only assumptions) ---
    from custom_components.ax_dose_logger.const import DRINK_TYPE_ALCOHOL

    coord = DrinkMasterCoordinator(
        DRINK_TYPE_ALCOHOL,
        dose_history=[(ts(1), 20.0, 0.5, "entry_b")],
    )
    sensor = DrinkMasterDailyAmountSensor(
        _FakeSettingsEntry(options={"alcohol_daily_limit_g": ALCOHOL_DEFAULT_LIMIT_G}),
        coord,
        DEFAULT_PROFILE_ID,
        None,
    )
    sensor._update_state()
    results.append(check("alcohol daily_amount", 20.0, sensor._attr_native_value))
    results.append(check("alcohol remaining None (no limit)", None, sensor._attr_extra_state_attributes["remaining"]))

    # tracker_device_info must build for the default profile (device ids stable).
    info = tracker_device_info(DEFAULT_PROFILE_ID, DRINK_TYPE_CAFFEINE, profile_name=None)
    results.append(check("tracker_device_info builds", True, info is not None))  # noqa: FBT003

    failed = [r for r in results if not r]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
