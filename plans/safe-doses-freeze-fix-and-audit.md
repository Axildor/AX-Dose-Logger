# Safe Doses Freeze Fix & Full Integration Audit

## Primary Bug: Safe Doses Counter Freeze

### Root Cause

The `PillSafeDosesSensor` in [`safe_doses.py`](custom_components/pill_logger/sensors/safe_doses.py:1) **only recalculates its state at midnight** via `async_track_time_change` (line 33-36) and when a pill_taken/pill_reset dispatcher signal fires. For time-dependent tracking types, this means:

- **"As Needed"**: Timestamps expire from the rolling window continuously, but pruning only happens at midnight. A dose taken 7.5 hours ago in an 8-hour window still counts until midnight, making the counter appear frozen.
- **"Regular Interval"**: The counter should flip from `0` to `max_pills` when the interval expires, but this transition only fires at midnight.
- **"Time of Day"**: The release-time transition only evaluates at midnight.

**Compare with** [`PillNextDoseSensor`](custom_components/pill_logger/sensors/next_dose.py:33) which correctly uses `async_track_time_interval(timedelta(minutes=1))` for continuous recalculation.

### Secondary Freeze Factor

In [`async_added_to_hass`](custom_components/pill_logger/sensors/safe_doses.py:23), after restoring timestamps and calling `_update_state()`, **`async_write_ha_state()` is never called**. On HA restart, the entity shows as `unknown`/`None` until the next dispatcher event fires, making it appear dead.

### Fix

1. Replace `async_track_time_change` (midnight-only) with `async_track_time_interval(timedelta(minutes=1))` for continuous state recalculation.
2. Add `self.async_write_ha_state()` after `_update_state()` in `async_added_to_hass`.
3. Set `should_poll = False` since the entity is fully event-driven.

---

## Full Bug Catalog

### CRITICAL

| # | File | Issue |
|---|------|-------|
| C1 | [`safe_doses.py`](custom_components/pill_logger/sensors/safe_doses.py:33) | Midnight-only update causes freeze for all time-dependent tracking types |

### HIGH

| # | File | Issue |
|---|------|-------|
| H1 | [`safe_doses.py`](custom_components/pill_logger/sensors/safe_doses.py:45) | Missing `async_write_ha_state()` after state restoration — entity shows `None` on restart |
| H2 | [`avg_doses.py`](custom_components/pill_logger/sensors/avg_doses.py:60) | `pill_taken` callback skips `_update_state()` for "As Needed" tracking — avg doses not updated on pill taken |
| H3 | [`concentration.py`](custom_components/pill_logger/sensors/concentration.py:64) | `gut_mass` not restored when valid timestamp exists — only set in the `else` branch (line 70), lost on restart |
| H4 | [`number.py`](custom_components/pill_logger/number.py:94) | `await asyncio.sleep(0.5)` blocks the HA event loop for 500ms during stock refill |

### MEDIUM

| # | File | Issue |
|---|------|-------|
| M1 | All sensor files | Missing `should_poll = False` on entities that are purely event-driven via dispatchers/time callbacks |
| M2 | [`concentration.py`](custom_components/pill_logger/sensors/concentration.py:61) | State restoration uses single-compartment decay formula but runtime uses two-compartment model — inconsistent |
| M3 | [`avg_doses.py`](custom_components/pill_logger/sensors/avg_doses.py:129) | `_next_dose_timeout_unsub` not cleaned up on entity removal — potential callback leak |
| M4 | [`steady_state.py`](custom_components/pill_logger/sensors/steady_state.py:74) | `import math` inside `update_state()` method — should be a top-level import |
| M5 | [`concentration.py`](custom_components/pill_logger/sensors/concentration.py:78) | `from homeassistant.helpers.dispatcher import async_dispatcher_send` inside method body — should be top-level |

### LOW

| # | File | Issue |
|---|------|-------|
| L1 | [`entity.py`](custom_components/pill_logger/entity.py:1) | Dead code — imports non-existent `.coordinator` module |
| L2 | [`data.py`](custom_components/pill_logger/data.py:1) | Dead code — references non-existent `.api` and `.coordinator` modules |
| L3 | [`sensor.py`](custom_components/pill_logger/sensor.py:12) | `SCAN_INTERVAL` defined but never used by any entity |
| L4 | [`strength.py`](custom_components/pill_logger/sensors/strength.py:16) | Value set only in `__init__` from `entry.options` — never updates when options change |
| L5 | Multiple sensors | Redundant `native_value` property that just returns `_attr_native_value` |

---

## Detailed Diffs

### C1: safe_doses.py — Replace midnight-only with periodic interval

```diff
--- a/custom_components/pill_logger/sensors/safe_doses.py
+++ b/custom_components/pill_logger/sensors/safe_doses.py
@@ -1,6 +1,6 @@
 from datetime import timedelta
 from homeassistant.components.sensor import RestoreSensor
 from homeassistant.helpers.dispatcher import async_dispatcher_connect
 from homeassistant.helpers.device_registry import DeviceInfo
-from homeassistant.helpers.event import async_track_time_change
+from homeassistant.helpers.event import async_track_time_interval
 from homeassistant.core import callback
 import homeassistant.util.dt as dt_util
@@ -10,6 +10,7 @@
 class PillSafeDosesSensor(RestoreSensor):
+    should_poll = False
+
     def __init__(self, entry):
         med_name = entry.data["medication_name"]
         self._med_name = med_name
@@ -30,9 +31,9 @@
         self.async_on_remove(
-            async_track_time_change(
-                self.hass, self._on_midnight, hour=0, minute=0, second=0
+            async_track_time_interval(
+                self.hass, self._on_interval, timedelta(minutes=1)
             )
         )
 
@@ -44,6 +45,7 @@
                     if dt:
                         self._timestamps.append(dt)
             self._update_state()
+            self.async_write_ha_state()
 
-    @callback
-    def _on_midnight(self, now):
+    @callback
+    def _on_interval(self, now):
         self._update_state()
```

### H2: avg_doses.py — Fix pill_taken for As Needed tracking

```diff
--- a/custom_components/pill_logger/sensors/avg_doses.py
+++ b/custom_components/pill_logger/sensors/avg_doses.py
@@ -59,8 +59,8 @@
     @callback
     def pill_taken(self, *args, **kwargs):
         self._timestamps.append(dt_util.now())
-        if self._tracking_type in ("Time of Day", "Regular Interval"):
-            self._update_state()
-            self.async_write_ha_state()
+        self._update_state()
+        self.async_write_ha_state()
 
     @callback
```

### H3: concentration.py — Restore gut_mass in all branches

```diff
--- a/custom_components/pill_logger/sensors/concentration.py
+++ b/custom_components/pill_logger/sensors/concentration.py
@@ -55,13 +55,14 @@
             if last_ts_str:
                 try:
                     last_ts = dt_util.parse_datetime(last_ts_str)
                     now = dt_util.now()
                     elapsed_hours = (now - last_ts).total_seconds() / 3600.0
+                    self._gut_mass = gut_mass
                     if self._half_life > 0:
                         self._current_mass = old_mass * (0.5 ** (elapsed_hours / self._half_life))
                     else:
                         self._current_mass = old_mass
                     self._last_updated = last_ts
                 except (ValueError, TypeError):
+                    self._gut_mass = gut_mass
                     self._current_mass = old_mass
                     self._last_updated = dt_util.now()
```

### H4: number.py — Replace blocking sleep with async callback

```diff
--- a/custom_components/pill_logger/number.py
+++ b/custom_components/pill_logger/number.py
@@ -1,5 +1,5 @@
-import asyncio
 from homeassistant.components.number import RestoreNumber, NumberEntity, NumberMode
 from homeassistant.core import HomeAssistant, callback
 from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
 from homeassistant.helpers.device_registry import DeviceInfo
+from homeassistant.helpers.event import async_call_later
@@ -83,15 +84,19 @@
     async def async_set_native_value(self, value: float):
         if value > 0:
             self._attr_native_value = value
             self.async_write_ha_state()
             async_dispatcher_send(self.hass, f"pill_add_stock_{self._entry_id}", value)
-            await asyncio.sleep(0.5)
-            self._attr_native_value = 0.0
-            self.async_write_ha_state()
+            self.async_on_remove(
+                async_call_later(
+                    self.hass,
+                    0.5,
+                    self._reset_add_stock,
+                )
+            )
+
+    @callback
+    def _reset_add_stock(self, _now):
+        self._attr_native_value = 0.0
+        self.async_write_ha_state()
```

### M1: Add should_poll = False to all event-driven sensors

**total.py:**
```diff
 class PillTotalSensor(RestoreSensor):
+    should_poll = False
+
     def __init__(self, name, entry_id):
```

**last_dose.py:**
```diff
 class PillLastDoseSensor(RestoreSensor):
+    should_poll = False
+
     def __init__(self, name, entry_id):
```

**next_dose.py:**
```diff
 class PillNextDoseSensor(RestoreSensor):
+    should_poll = False
+
     def __init__(self, entry):
```

**concentration.py:**
```diff
 class PillConcentrationSensor(RestoreSensor):
+    should_poll = False
+
     def __init__(self, entry):
```

**avg_doses.py:**
```diff
 class PillAvgDosesSensor(RestoreSensor):
+    should_poll = False
+
     def __init__(self, entry, window_days, sensor_name):
```

**steady_state.py:**
```diff
 class PillSteadyStateSensor(RestoreSensor):
+    should_poll = False
+
     def __init__(self, entry):
```

**strength.py:**
```diff
 class PillStrengthSensor(RestoreSensor):
+    should_poll = False
+
     def __init__(self, entry):
```

### M2: concentration.py — Use two-compartment decay in state restoration

```diff
--- a/custom_components/pill_logger/sensors/concentration.py
+++ b/custom_components/pill_logger/sensors/concentration.py
@@ -55,12 +55,24 @@
             if last_ts_str:
                 try:
                     last_ts = dt_util.parse_datetime(last_ts_str)
                     now = dt_util.now()
                     elapsed_hours = (now - last_ts).total_seconds() / 3600.0
+                    self._gut_mass = gut_mass
-                    if self._half_life > 0:
-                        self._current_mass = old_mass * (0.5 ** (elapsed_hours / self._half_life))
+                    k_e = math.log(2) / self._half_life if self._half_life > 0 else 0
+                    k_a = self._ka if self._ka > 0 else (self._solve_ka(self._hours_to_peak, k_e) if self._hours_to_peak > 0 else 0)
+                    if k_e > 0 and k_a > 0 and abs(k_a - k_e) > 0.0001:
+                        self._current_mass = (old_mass * math.exp(-k_e * elapsed_hours) +
+                                              (gut_mass * k_a / (k_a - k_e)) *
+                                              (math.exp(-k_e * elapsed_hours) - math.exp(-k_a * elapsed_hours)))
+                        self._gut_mass = gut_mass * math.exp(-k_a * elapsed_hours)
+                    elif k_e > 0:
+                        self._current_mass = old_mass * math.exp(-k_e * elapsed_hours)
+                        self._gut_mass = 0
                     else:
                         self._current_mass = old_mass
                     self._last_updated = last_ts
                 except (ValueError, TypeError):
+                    self._gut_mass = gut_mass
                     self._current_mass = old_mass
                     self._last_updated = dt_util.now()
```

### M4/M5: Move imports to top level

**concentration.py** — move `async_dispatcher_send` import to top:
```diff
--- a/custom_components/pill_logger/sensors/concentration.py
+++ b/custom_components/pill_logger/sensors/concentration.py
@@ -7,6 +7,7 @@
 from homeassistant.core import callback
 import homeassistant.util.dt as dt_util
 from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
+from homeassistant.helpers.dispatcher import async_dispatcher_send
 import math
 from ..const import DOMAIN
```

Then remove the two inline imports:
- Line 78: remove `from homeassistant.helpers.dispatcher import async_dispatcher_send`
- Line 150: remove `from homeassistant.helpers.dispatcher import async_dispatcher_send`

**steady_state.py** — move `import math` to top:
```diff
--- a/custom_components/pill_logger/sensors/steady_state.py
+++ b/custom_components/pill_logger/sensors/steady_state.py
@@ -5,6 +5,7 @@
 from homeassistant.helpers.device_registry import DeviceInfo
 from homeassistant.helpers.event import async_track_time_interval
 from homeassistant.core import callback
+import math
 import homeassistant.util.dt as dt_util
 from ..const import DOMAIN
```

Then remove `import math` from inside `update_state()` (line 74).

### M3: avg_doses.py — Clean up timeout subscription on removal

Add cleanup in `async_added_to_hass`:
```diff
     async def async_added_to_hass(self):
         await super().async_added_to_hass()
+        self._next_dose_timeout_unsub = None
         self.async_on_remove(
```

And add a `async_will_remove_from_hass` method:
```diff
+    async def async_will_remove_from_hass(self):
+        if self._next_dose_timeout_unsub:
+            self._next_dose_timeout_unsub()
+            self._next_dose_timeout_unsub = None
```

### L1/L2: Remove dead code from entity.py and data.py

**entity.py** — Remove entirely or replace with minimal stub since no sensor uses it.

**data.py** — Remove references to non-existent `.api` and `.coordinator` modules.

### L3: Remove unused SCAN_INTERVAL from sensor.py

```diff
--- a/custom_components/pill_logger/sensor.py
+++ b/custom_components/pill_logger/sensor.py
@@ -1,5 +1,3 @@
-from datetime import timedelta
 from homeassistant.core import HomeAssistant
 from .sensors.total import PillTotalSensor
```

Remove the `SCAN_INTERVAL = timedelta(minutes=2)` line.

---

## Validation Summary

After applying all fixes:

1. **Safe Doses counter will update every minute** via `async_track_time_interval`, ensuring time-dependent state transitions (window expiry, interval expiry, time-of-day release) are reflected in real-time rather than waiting for midnight.

2. **Entity state persists across restarts** because `async_write_ha_state()` is now called after restoring state in `async_added_to_hass`.

3. **No event loop blocking** — the `asyncio.sleep(0.5)` in `PillAddStockNumber` is replaced with `async_call_later`, preserving HA responsiveness.

4. **Gut mass is properly restored** on restart, maintaining two-compartment model continuity.

5. **All event-driven sensors declare `should_poll = False`**, preventing unnecessary polling overhead and making HA's entity platform aware that these entities manage their own state updates.

6. **Avg Doses sensor updates on every pill_taken event** regardless of tracking type.

7. **Concentration restoration uses the same two-compartment math** as runtime decay, ensuring state continuity accuracy.