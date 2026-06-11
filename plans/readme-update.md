# Plan: Update README.md to Reflect Current Integration State

## Gap Analysis

The current README is significantly outdated. It was written before several major features were added:

### Missing Features in README
1. **Cyclic/Calendar Pattern** — 4th tracking type (days on/off cycle with anchor date)
2. **Pharmacokinetics Engine** — Two-compartment PK model with concentration tracking, absorption rate solving
3. **Steady State Sensor** — Days until 90% steady state with current percentage
4. **Strength Sensor** — Shows configured medication strength
5. **Effectiveness Tracking** — Subjective 1-10 sliders for Pain, Mood, Nausea, Fatigue + custom metrics
6. **Total Doses Sensor** — Cumulative lifetime dose counter

### Outdated Dashboard YAML
- Does not reference any new sensors (concentration, steady state, strength, effectiveness)
- Needs a refreshed layout showcasing PK and effectiveness capabilities

---

## Planned README Structure

### 1. Title and Tagline
Update description to mention PK modeling, effectiveness tracking, and cyclic scheduling.

### 2. Features Section (expanded)
Organize into categories:
- **Scheduling** — 4 tracking types including Cyclic/Calendar Pattern
- **Safety** — Safe dose tracking, overdose warning
- **Pharmacokinetics** — Concentration, steady state, strength, half-life, absorption modeling
- **Effectiveness Tracking** — Standard + custom metric sliders
- **Insights** — Rolling averages, total doses, last dose
- **Inventory** — Smart inventory with refill
- **Reminders** — Blueprint with Take/Skip/Snooze

### 3. Entity Reference (NEW section)
Table listing every entity created per medication device:
- 10 sensors, 2 buttons, 2+ numbers

### 4. Configuration Details
Show all 4 setup flows with their fields, including PK fields and effectiveness metrics.

### 5. Configuration Options (NEW section)
Document what can be changed via the Options flow after initial setup, per tracking type:
- **Regular Interval**: hours_between_doses, safe_doses, strength, half_life, hours_to_peak, effectiveness metrics
- **Time of Day**: time_of_day, safe_doses, strength, half_life, hours_to_peak, effectiveness metrics
- **As Needed**: time_window_hours, safe_doses, strength, half_life, hours_to_peak, effectiveness metrics
- **Cyclic/Calendar Pattern**: days_on, days_off, cycle_anchor_date, dose_time, safe_doses, strength, half_life, hours_to_peak, effectiveness metrics
- Note: medication_name and tracking_type cannot be changed after creation (require re-creation)

### 6. Installation
Keep existing HACS instructions.

### 7. Dashboard YAML
Updated card layout that includes:
- Concentration display
- Steady state progress
- Effectiveness sliders
- Existing take/safe doses/inventory/averages

### 8. Blueprint
Keep existing instructions, minor polish.

### 9. Disclaimer
Keep as-is.

---

## Entity Inventory (for reference)

| Platform | Entity ID Pattern | Description |
|----------|-------------------|-------------|
| sensor | `{name}_total` | Cumulative lifetime dose count |
| sensor | `{name}_last_dose` | Timestamp of most recent dose |
| sensor | `{name}_safe_doses` | Remaining safe doses in window |
| sensor | `{name}_concentration` | Current drug concentration (mg) |
| sensor | `{name}_next_dose` | Timestamp of next available dose |
| sensor | `{name}_avg_daily_doses_7_days` | 7-day rolling average |
| sensor | `{name}_avg_daily_doses_30_days` | 30-day rolling average |
| sensor | `{name}_avg_daily_doses_yearly` | 365-day rolling average |
| sensor | `{name}_steady_state` | Days to 90% steady state |
| sensor | `{name}_strength` | Configured medication strength (mg) |
| button | `take_{name}` | Log a dose |
| button | `reset_{name}_history` | Wipe history, keep inventory |
| number | `{name}_pills_left` | Current inventory count |
| number | `add_{name}_refill` | Refill input (auto-resets) |
| number | `{name}_{metric}_effectiveness` | 1-10 slider per enabled metric |