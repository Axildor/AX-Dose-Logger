# Active Context: Pill Logger Sensor Fixes & PK Engine Upgrade

## Current Status
The pharmacokinetics engine has been upgraded to support dynamic $k_a$ solving based on "Hours to Peak", and a state-aware Steady State sensor has been implemented. Startup crashes related to state restoration have been resolved. A targeted refactor has been applied to align configuration flow, presentation precision, and state execution gates.

## Problem Description
- **Issue 1: Setup Loop Interruption.** `PillConcentrationSensor` was missing from registration. (Fixed)
- **Issue 2: Missing Callback.** `PillSafeDosesSensor` was missing `_on_midnight`. (Fixed)
- **Issue 3: Single Compartment Limitation.** The previous model didn't account for absorption delay. (Resolved by upgrade)
- **Issue 4: Config Flow Duplication.** `hours_to_peak` appeared in both initial and detailed setup steps. (Fixed)
- **Issue 5: Presentation/Polling misalignment.** Sensors lacked explicit scan intervals and precision metadata. (Fixed)
- **Issue 6: State Gate Bypass.** Pill strength was sometimes bypassing the gut compartment. (Fixed)

## Fixes Applied
- **Fix 1:** Added `PillConcentrationSensor(entry)` to the entities list in `async_setup_entry`.
- **Fix 2:** Implemented `_on_midnight` callback in `PillSafeDosesSensor`.
- **Fix 3: Two-Compartment PK Engine.** Redesigned concentration tracking in `sensor.py` to use the Iterative State Method, tracking `gut_mass` and `body_mass`.
- **Fix 4: Numerical $k_a$ Solver.** Replaced "Absorption Delay" with "Hours to Peak" ($T_{max}$) and implemented a binary search solver (`_solve_ka`) to dynamically calculate the absorption rate.
- **Fix 5: State Restoration Fix.** Resolved `AttributeError` in `async_added_to_hass` by correctly using `last_state.state` and casting numerical strings to floats.
- **Fix 6: Dynamic Steady State Tracking.** Refactored `PillSteadyStateSensor` to detect missed doses (>24h) and dynamically recalculate recovery time, ensuring 0.0 is not a flat baseline during active dosing.
- **Fix 7: Boot Loop Resolution.** Corrected a malformed `homeassistant.states` import in `sensor.py` that caused fatal startup errors.
- **Fix 8: Config Flow De-duplication.** Removed `hours_to_peak` from `STEP_USER_SCHEMA` in `config_flow.py`.
- **Fix 9: Precision & Polling.** Added `SCAN_INTERVAL = timedelta(minutes=2)` and set `_attr_suggested_display_precision = 1` and `_attr_native_unit_of_measurement = "mg"` in `PillConcentrationSensor`.
- **Fix 10: Execution Gate Routing.** Modified `handle_pill_taken` to always inject strength into `_gut_mass` and added immediate `async_write_ha_state()` trigger.

## Verification Results
- Verified corrected imports and fixed boot-loop crash (syntax check passed).
- Confirmed both sensors are properly registered.
- Verified two-compartment math logic and steady state calculation.
- Confirmed `sensor.py` compiles successfully after refactor.

## Next Steps
- Finalize documentation synchronization.
- Completed pharmacokinetic math stabilization in `sensor.py` (Fixed Concentration Decay Overlap and Steady State Boot Race Condition).