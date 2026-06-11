# UX Improvements Plan for Pill Logger Integration

## Issues Found

### 1. Tracking metric labels show as raw keys
The config flow fields `metric_pain`, `metric_mood`, `metric_nausea`, `metric_fatigue`, and `custom_metrics` display as raw key names instead of user-friendly labels. The translations in `translations/en.json` exist but may not be loading because there's no `strings.json` at the integration root — which is the canonical source HA uses for config flow translations.

### 2. `custom_metrics` label needs better guidance
Current label: `"Custom metrics (comma-separated, e.g. brain fog, joint stiffness)"` — this is okay but should more clearly explain that you can add **multiple** custom tracking metrics by separating them with commas.

### 3. `cycle_anchor_date` is a plain text field
Currently a raw string input requiring `YYYY-MM-DD` format. This is not user-friendly, especially for US vs EU date format users. Should be a **date picker** selector.

### 4. `time_of_day` and `dose_time` are plain text fields
Currently raw string inputs requiring `HH:MM` 24-hour format. Should be **time picker** selectors for better UX.

### 5. Missing `strings.json`
The integration lacks a `strings.json` file at the root level. This is the canonical file HA loads for config flow translations. Without it, translations may not apply correctly, causing raw key names to display.

### 6. No step descriptions in config flow
The config flow steps lack `description` text that would help users understand what each step is for.

---

## Changes Required

### A. Create `custom_components/pill_logger/strings.json`
This file is the primary source for HA config flow translations. It should mirror and improve upon the existing `translations/en.json` content with:

- **User-friendly metric labels**: Change from `metric_pain` → `Pain Level`, `metric_mood` → `Mood`, `metric_nausea` → `Nausea Level`, `metric_fatigue` → `Fatigue Level`
- **Improved `custom_metrics` label**: Something like `"Add custom tracking metrics — separate multiple with commas, e.g. brain fog, joint stiffness"`
- **Step descriptions**: Add helpful description text for each config step
- **Date/time field labels**: Update `cycle_anchor_date` label to `"Cycle start date"`, `time_of_day` to `"Time of day to take"`, `dose_time` to `"Dose time"`

### B. Update `custom_components/pill_logger/translations/en.json`
Sync with the new `strings.json` content to keep them consistent.

### C. Update `custom_components/pill_logger/config_flow.py`
1. **Import selectors** from `homeassistant.helpers.selector`
2. **Change `cycle_anchor_date`** from `str` type to `selector({"date": {}})` — renders a calendar picker, returns `YYYY-MM-DD` string
3. **Change `time_of_day`** from `str` type to `selector({"time": {}})` — renders a time picker, returns `HH:MM` string
4. **Change `dose_time`** from `str` type to `selector({"time": {}})` — renders a time picker, returns `HH:MM` string
5. **Apply same selector changes in the options flow** (`PillLoggerOptionsFlowHandler`)

### D. No sensor code changes needed
The date and time selectors return the same string formats (`YYYY-MM-DD` and `HH:MM`) that the sensor code already parses, so no changes are needed in `sensors/next_dose.py`, `sensors/safe_doses.py`, or `sensors/avg_doses.py`.

---

## Detailed Label Changes

| Field Key | Current Label | New Label |
|---|---|---|
| `metric_pain` | Track Pain effectiveness | Pain Level |
| `metric_mood` | Track Mood effectiveness | Mood |
| `metric_nausea` | Track Nausea effectiveness | Nausea Level |
| `metric_fatigue` | Track Fatigue effectiveness | Fatigue Level |
| `custom_metrics` | Custom metrics - comma-separated, e.g. brain fog, joint stiffness | Add custom tracking metrics — separate multiple with commas, e.g. brain fog, joint stiffness |
| `cycle_anchor_date` | Cycle start date - YYYY-MM-DD | Cycle start date |
| `time_of_day` | Time of day to take - HH:MM format, 24-hour | Time of day to take |
| `dose_time` | Time of day to take - HH:MM format, 24-hour | Dose time |

---

## Config Flow Step Descriptions

| Step | Description |
|---|---|
| `user` | Choose a medication name and how you want to track it. |
| `regular_interval` | Set up dosing at fixed intervals — e.g. every 8 hours. |
| `time_of_day` | Set up dosing at a specific time each day. |
| `as_needed` | Set up on-demand dosing with a safety limit. |
| `cyclic` | Set up a cycling schedule — e.g. 5 days on, 2 days off. |

---

## Files to Modify

1. **`custom_components/pill_logger/strings.json`** — CREATE new file
2. **`custom_components/pill_logger/translations/en.json`** — UPDATE labels and add descriptions
3. **`custom_components/pill_logger/config_flow.py`** — ADD date/time selectors, import selector module

## Files NOT Modified

- Sensor files — no changes needed, selectors return same string formats
- `const.py` — no changes needed
- `number.py` — entity names are already user-friendly
- `entity.py` — no changes needed