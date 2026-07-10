#!/usr/bin/env python3
"""
Verify the FIXED concentration.py produces a continuous, mass-conserving curve.

Extracts the _recalculate_er math by stubbing the HA dependencies and calling
the real method with the user's parameters.
"""

import os
import sys

# Stub HA modules so we can import the sensor
import types
from datetime import datetime, timedelta

# homeassistant.util.dt must be importable as `import homeassistant.util.dt as dt_util`
ha = types.ModuleType("homeassistant")
ha_util = types.ModuleType("homeassistant.util")
ha_util_dt = types.ModuleType("homeassistant.util.dt")
ha_util_dt.now = lambda: datetime.utcnow()
ha_util_dt.parse_datetime = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
ha_util.dt = ha_util_dt
ha.util = ha_util
sys.modules["homeassistant"] = ha
sys.modules["homeassistant.util"] = ha_util
sys.modules["homeassistant.util.dt"] = ha_util_dt

ha_comp = types.ModuleType("homeassistant.components")
ha_comp_sensor = types.ModuleType("homeassistant.components.sensor")


class _RestoreSensor:
    pass


class _SensorStateClass:
    MEASUREMENT = "measurement"


ha_comp_sensor.RestoreSensor = _RestoreSensor
ha_comp_sensor.SensorStateClass = _SensorStateClass
ha.components = ha_comp_sensor
sys.modules["homeassistant.components"] = ha_comp
sys.modules["homeassistant.components.sensor"] = ha_comp_sensor

ha_helpers = types.ModuleType("homeassistant.helpers")
ha_disp = types.ModuleType("homeassistant.helpers.dispatcher")
ha_disp.async_dispatcher_connect = lambda *a, **k: lambda: None
ha_disp.async_dispatcher_send = lambda *a, **k: None
ha_helpers.dispatcher = ha_disp
ha_dev = types.ModuleType("homeassistant.helpers.device_registry")


class _DeviceInfo(dict):
    pass


ha_dev.DeviceInfo = _DeviceInfo
ha_helpers.device_registry = ha_dev
ha_ev = types.ModuleType("homeassistant.helpers.event")
ha_ev.async_track_time_interval = lambda *a, **k: lambda: None
ha_helpers.event = ha_ev
ha_core = types.ModuleType("homeassistant.core")
ha_core.callback = lambda f: f
sys.modules["homeassistant.helpers"] = ha_helpers
sys.modules["homeassistant.helpers.dispatcher"] = ha_disp
sys.modules["homeassistant.helpers.device_registry"] = ha_dev
sys.modules["homeassistant.helpers.event"] = ha_ev
sys.modules["homeassistant.core"] = ha_core

ha_const = types.ModuleType("homeassistant.const")
ha_const.STATE_UNKNOWN = "unknown"
ha_const.STATE_UNAVAILABLE = "unavailable"
sys.modules["homeassistant.const"] = ha_const

# Stub the package hierarchy, pointing __path__ at the real directories
pkg = types.ModuleType("custom_components")
pkg.__path__ = [os.path.abspath("custom_components")]
sys.modules["custom_components"] = pkg
pl = types.ModuleType("custom_components.ax_dose_logger")
pl.__path__ = [os.path.abspath("custom_components/ax_dose_logger")]
sys.modules["custom_components.ax_dose_logger"] = pl
const_mod = types.ModuleType("custom_components.ax_dose_logger.const")
const_mod.DOMAIN = "ax_dose_logger"
const_mod.PK_DEFAULTS = {
    "bioavailability": 100,
    "ir_fraction": 100,
    "zero_order_duration": 0,
    "release_half_life": 0,
    "lag_time": 0,
    "ir_hours_to_peak": 1.0,
}
sys.modules["custom_components.ax_dose_logger.const"] = const_mod
sensors_pkg = types.ModuleType("custom_components.ax_dose_logger.sensors")
sensors_pkg.__path__ = [os.path.abspath("custom_components/ax_dose_logger/sensors")]
sys.modules["custom_components.ax_dose_logger.sensors"] = sensors_pkg

# Now import the real sensor
sys.path.insert(0, ".")
from custom_components.ax_dose_logger.sensors.concentration import PillConcentrationSensor


# Build a fake entry
class FakeEntry:
    entry_id = "test"
    data = {
        "medication_name": "test",
        "release_type": "Sustained Release",
        "strength": 665,
        "half_life": 2,
        "hours_to_peak": 2.8,
    }
    options = {
        "strength": 665,
        "half_life": 2,
        "hours_to_peak": 2.8,
        "bioavailability": 100,
        "ir_fraction": 31,
        "zero_order_duration": 8,
        "release_half_life": 0,
        "lag_time": 0,
        "ir_hours_to_peak": 1.0,
        "strength_unit": "mg",
    }


sensor = PillConcentrationSensor(FakeEntry())
sensor.hass = None  # not used in _recalculate_er
sensor.async_write_ha_state = lambda: None

# Single dose at t=0
dose_time = datetime(2026, 6, 13, 18, 28, 0)
sensor._dose_history = [(dose_time, 665.0)]

print("=== Fixed concentration.py: ER curve (user params) ===")
print("t_hours, body, gut_ir, matrix_sr, gut_sr, total_in_system")
prev = None
max_jump = 0.0
for i in range(721):  # 0 to 12h in 1-min steps
    t_h = i / 60.0
    now = dose_time + timedelta(hours=t_h)
    sensor._recalculate_er(now=now)
    body = sensor._current_mass
    gi = sensor._gut_ir_mass
    mx = sensor._matrix_sr_mass
    gs = sensor._gut_sr_mass
    total = body + gi + mx + gs
    if prev is not None:
        max_jump = max(max_jump, abs(body - prev))
    prev = body
    if i % 30 == 0 or abs(t_h - 8.0) < 0.02:  # every 30 min + around T_dur
        print(f"{t_h:6.3f}, {body:7.2f}, {gi:6.2f}, {mx:6.2f}, {gs:6.2f}, {total:7.2f}")

print()
print(f"Max step-to-step body jump (1-min sampling): {max_jump:.4f} mg")
# Under the dual-absorption model the IR fraction absorbs via a fast k_a_ir
# (default 1.0h time-to-peak), producing a steep but CONTINUOUS initial rise
# (~8-9 mg/min at t=0). This is legitimate pharmacokinetic behavior, not a
# cliff drop. The threshold accommodates the fast IR rise while still
# catching genuine discontinuities (e.g. at t = T_dur).
assert max_jump < 15.0, f"FAIL: cliff drop detected ({max_jump:.2f} mg)"
print("PASS: no cliff drop (continuity verified)")

# Mass balance at t=0: all dose in gut_ir + matrix
sensor._recalculate_er(now=dose_time)
# Under the dual-absorption ER model, D_IR absorbs through a fast k_a_ir
# (default 1.0h time-to-peak) and sits in the gut_ir compartment at t=0
# (no teleport into the body). The t=0 mass-balance identity is therefore
# gut_ir + matrix == dose.
assert abs(sensor._gut_ir_mass + sensor._matrix_sr_mass - 665.0) < 0.01, "FAIL: mass not conserved at t=0"
print("PASS: mass conserved at t=0 (gut_ir + matrix = 665)")

# Continuity right at T_dur
eps = timedelta(seconds=1e-3)
sensor._recalculate_er(now=dose_time + timedelta(hours=8.0) - eps)
b_before = sensor._current_mass
sensor._recalculate_er(now=dose_time + timedelta(hours=8.0) + eps)
b_after = sensor._current_mass
print(f"Continuity at T_dur: body(8h-eps)={b_before:.4f}, body(8h+eps)={b_after:.4f}, jump={b_after - b_before:.6f}")
assert abs(b_after - b_before) < 0.01, "FAIL: discontinuity at T_dur"
print("PASS: continuous at t = T_dur")
