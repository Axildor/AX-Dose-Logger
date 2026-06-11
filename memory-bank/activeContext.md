# Active Context: Pill Logger — Cyclic/Calendar Pattern Feature

## Current Status
The Cyclic/Calendar Pattern tracking type has been implemented. This feature allows users to define a medication routine that runs on a fixed cycle: X days on, Y days off, repeated continuously, starting from a specific anchor date.

## Feature: Cyclic/Calendar Pattern

### What Was Added
- **New tracking type** `"Cyclic/Calendar Pattern"` added as a 4th option alongside Regular Interval, Time of Day, and As Needed
- **Config flow step** `async_step_cyclic` with fields: `days_on`, `days_off`, `cycle_anchor_date`, `dose_time`, plus standard fields
- **Options flow** support for editing cyclic-specific parameters (`days_on`, `days_off`, `cycle_anchor_date`, `dose_time`)
- **Safe Doses sensor** evaluates cycle position: returns `max_pills` during ON days, `0` during OFF days
- **Next Dose sensor** calculates next dose timestamp based on cycle position and dose_time
- **Localized strings** for all cyclic fields in `translations/en.json`

### Files Modified
1. **`config_flow.py`** — Added `"Cyclic/Calendar Pattern"` to dropdown, `async_step_cyclic` method, and cyclic branch in options flow; imported `date` from `datetime`
2. **`sensors/safe_doses.py`** — Added `elif self._tracking_type == "Cyclic/Calendar Pattern":` branch in `_update_state()`; imported `date` from `datetime`
3. **`sensors/next_dose.py`** — Added `elif self._tracking_type == "Cyclic/Calendar Pattern":` branch in `_update_state()`; imported `date` and `datetime` from `datetime`
4. **`translations/en.json`** — Added `cyclic` step section with labels for all cyclic-specific fields; added cyclic field labels to `options.step.init.data`

### Cycle Calculation Logic
- `position_in_cycle = (today - anchor_date).days % (days_on + days_off)`
- If `position_in_cycle < days_on` → ON window (safe_doses = max_pills)
- If `position_in_cycle >= days_on` → OFF window (safe_doses = 0)

### Next Dose Logic (Cyclic)
- **OFF window**: next dose = start of next ON period at `dose_time`
- **ON window, before dose_time**: next dose = today at `dose_time`
- **ON window, after dose_time, dose already taken**: next dose = next ON day at `dose_time`
- **ON window, after dose_time, no dose taken**: next dose = today at `dose_time` (still available)

### Key Design Decisions
- `dose_time` field (HH:MM) gives users control over when during ON days the dose is scheduled
- `cycle_anchor_date` stored as ISO date string, parsed with `date.fromisoformat()`, fallback to today
- No timestamp filtering for cyclic safe_doses — cycle position alone determines availability
- All changes are additive `elif` branches; existing tracking types are completely undisturbed
- Config flow VERSION remains at 2 (no breaking changes to existing entries)

## Previous Context (Archived)
- Effectiveness Mapping feature implemented (4 standard metrics + custom metrics)
- PK engine upgraded to two-compartment model with dynamic $k_a$ solving
- Steady state sensor implemented with event-driven updates
- Sensor package modularized into `sensors/` directory
- Various runtime bug fixes