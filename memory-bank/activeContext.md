# Active Context: Pill Logger Sensor Fixes & PK Engine Upgrade

## Current Status
The pharmacokinetics engine has been upgraded to support dynamic $k_a$ solving based on "Hours to Peak", and a state-aware Steady State sensor has been implemented. Startup crashes related to state restoration have been resolved.

## Problem Description
- **Issue 1: Setup Loop Interruption.** `PillConcentrationSensor` was missing from registration. (Fixed)
- **Issue 2: Missing Callback.** `PillSafeDosesSensor` was missing `_on_midnight`. (Fixed)
- **Issue 3: Single Compartment Limitation.** The previous model didn't account for absorption delay. (Resolved by upgrade)

## Fixes Applied
- **Fix 1:** Added `PillConcentrationSensor(entry)` to the entities list in `async_setup_entry`.
- **Fix 2:** Implemented `_on_midnight` callback in `PillSafeDosesSensor`.
- **Fix 3: Two-Compartment PK Engine.** Redesigned concentration tracking in `sensor.py` to use the Iterative State Method, tracking `gut_mass` and `body_mass`.
- **Fix 4: Numerical $k_a$ Solver.** Replaced "Absorption Delay" with "Hours to Peak" ($T_{max}$) and implemented a binary search solver (`_solve_ka`) to dynamically calculate the absorption rate.
- **Fix 5: State Restoration Fix.** Resolved `AttributeError` in `async_added_to_hass` by correctly using `last_state.state` and casting numerical strings to floats.
- **Fix 6: Dynamic Steady State Tracking.** Refactored `PillSteadyStateSensor` to detect missed doses (>24h) and dynamically recalculate recovery time, with a gate ensuring 0.0 is not reached until the physical peak is achieved.
- **Fix 7: Boot Loop Resolution.** Corrected a malformed `homeassistant.states` import in `sensor.py` that caused fatal startup errors.

## Verification Results
- Verified corrected imports and fixed boot-loop crash (syntax check passed).
- Confirmed both sensors are properly registered.
- Verified two-compartment math logic and steady state calculation.

## Next Steps
- Finalize documentation synchronization.