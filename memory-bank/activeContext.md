# Active Context: Pill Logger Sensor Fixes & PK Engine Upgrade

## Current Status
The systemic structural bug in `custom_components/pill_logger/sensor.py` that caused safe dose entities to be unavailable has been fixed. Additionally, the pharmacokinetics engine has been upgraded to a two-compartment model and a new Steady State sensor has been added.

## Problem Description
- **Issue 1: Setup Loop Interruption.** `PillConcentrationSensor` was missing from registration. (Fixed)
- **Issue 2: Missing Callback.** `PillSafeDosesSensor` was missing `_on_midnight`. (Fixed)
- **Issue 3: Single Compartment Limitation.** The previous model didn't account for absorption delay. (Resolved by upgrade)

## Fixes Applied
- **Fix 1:** Added `PillConcentrationSensor(entry)` to the entities list in `async_setup_entry`.
- **Fix 2:** Implemented `_on_midnight` callback in `PillSafeDosesSensor`.
- **Fix 3: Two-Compartment PK Engine.** Redesigned concentration tracking in `sensor.py` to use the Iterative State Method, tracking `gut_mass` and `body_mass`.
- **Fix 4: Absorption Delay Support.** Added "Absorption Delay" configuration variable.
- **Fix 5: Steady State Sensor.** Added `PillSteadyStateSensor` to calculate days to reach 90% accumulation.

## Verification Results
- Verified module imports (syntax check passed in local environment).
- Confirmed both sensors are properly registered.
- Verified two-compartment math logic and steady state calculation.

## Next Steps
- Finalize documentation synchronization.