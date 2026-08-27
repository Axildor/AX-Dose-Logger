# 🔧 Advanced Users

[![Buy me a tea](https://img.shields.io/badge/Buy_me_a_tea-☕-FF5E5B?style=flat-square&logo=ko-fi&logoColor=white)](https://ko-fi.com/axildor)

> [← Back to main README](README.md)

This document is for power users building custom Lovelace templates or automations beyond the dedicated AX Dose Logger Card. The card handles all of this automatically — you only need these details if you're hand-rolling your own dashboard or automation.

- [Configuration Reference](#configuration-reference)
- [Entity States & Attributes](#entity-states--attributes)
- [Building Automations](#building-automations)

For the pharmacokinetic model math (formulas, worked examples, steady-state derivation), see [Pharmacokinetics.md](Pharmacokinetics.md).

---

## Configuration Reference

### Step 1: Add a Medication

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| Medication Name | Text | Display name for the device | My Medication |
| Tracking Type | Dropdown | Choose a tracking mode (descriptions shown inline) | Regular Interval |
| Release Type | Dropdown | **Instant Release** for standard pills, **Sustained Release** for extended-release formulations | Instant Release |

> The medication name, tracking type, and release type can't be changed after creation. To switch, remove the entry and create a new one. *(The tracking type can be changed later via Configure — see [Reconfiguring](#reconfiguring-after-setup).)*

### Step 2: Schedule & Dosing

#### Regular Interval

| Field | Range | Description | Default |
|-------|-------|-------------|---------|
| Inventory | 0–9999 pills | Number of pills currently available | 30 |
| Dose Interval | 1–48 h | Minimum hours between consecutive doses | 8 |
| Pill Limit | 1–20 pills | Maximum pills you can take within the time window | 1 |
| Time Window | 0.5–168 h | Rolling window for the pill limit | 8 |

#### Time of Day

| Field | Range | Description | Default |
|-------|-------|-------------|---------|
| Inventory | 0–9999 pills | Number of pills currently available | 30 |
| Dose Time | Time picker | Time of day to take the medication | 08:00 |
| Pill Limit | 1–20 pills | Maximum pills you can take within the window | 1 |
| Time Window | 0.5–168 h | Rolling window for the pill limit | 24 |

#### As Needed (PRN)

| Field | Range | Description | Default |
|-------|-------|-------------|---------|
| Inventory | 0–9999 pills | Number of pills currently available | 30 |
| Pill Limit | 1–20 pills | Maximum pills you can take within the time window | 2 |
| Time Window | 0.5–168 h | Rolling window for the pill limit | 8 |

#### Cyclic / Calendar Pattern

| Field | Range | Description | Default |
|-------|-------|-------------|---------|
| Inventory | 0–9999 pills | Number of pills currently available | 30 |
| Days On | 1–30 days | Number of active days in the cycle | 5 |
| Days Off | 1–30 days | Number of rest days in the cycle | 2 |
| Cycle Start Date | Date picker | Start date of the on/off cycle | Today |
| Dose Time | Time picker | Time of day to take on active days | 08:00 |
| Pill Limit | 1–20 pills | Maximum pills you can take within the time window | 1 |
| Time Window | 0.5–168 h | Rolling window for the pill limit | 24 |

### Step 3: Pharmacokinetics

> ⚠️ **Important:** PK parameters should be sourced from official pharmacokinetic data (e.g., FDA labels, EMA assessments, peer-reviewed literature). Do not guess — incorrect values will produce misleading results. See the [PK Search Guide](Pharmacokinetics.md#pk-search-guide) for help.

**Common fields (all release types):**

| Field | Range | Description | Default |
|-------|------|-------------|---------|
| Dose Strength | 0–9999 mg | Amount of medication per dose. Set to 0 if not tracking concentration. | 0 |
| Elimination Half-Life | 0–168 h | Time for the body to eliminate half the drug. Set to 0 if not tracking concentration. | 0 |
| Time to Peak Concentration | 0–72 h | Hours after taking until concentration peaks. Set to 0 for immediate-release medications. | 0 |
| Bioavailability | 0–100 % | Fraction of the dose that reaches systemic circulation. For example, ibuprofen ≈ 87%, while some drugs are closer to 50%. | 100 |
| Lag Time | 0–1440 min | Minutes before the medication begins releasing. Leave at 0 if unsure — most drugs start releasing immediately. Typical values: 15–30 min for enteric-coated tablets, 60+ min for colon-targeted delivery. | 0 |
| 24h Strength Limit | 0–∞ (medication unit) | Optional daily intake cap. `0` = no limit. When set, the Amount in Last 24h sensor exposes a `remaining` attribute, and a **24h Limit Exceeded** binary sensor is created that turns on when the limit is already exceeded or the next dose would push you over it. | 0 |

**Sustained Release fields** (only shown when Release Type is Sustained Release):

| Field | Range | Description | Default |
|-------|------|-------------|---------|
| Initial Release | 0–100 % | Percentage of the dose released immediately (IR fraction). For Paracetamol (Panadol/Tylenol) ER 665 mg, this is 31%. | 100 |
| Sustained Release Duration | 0–72 h | Duration of the zero-order (constant-rate) release phase. Leave at 0 for matrix tablets (e.g. Paracetamol ER) — they are polymer sponges, not mechanical pumps. | 0 |
| Release Half-Life | 0–168 h | Half-life of the first-order release from the SR matrix (the polymer sponge's physical dissolution time). For Paracetamol (Panadol/Tylenol) ER 665 mg, this is 3.0 h. | 0 |

> Leave Dose Strength and Elimination Half-Life at 0 to disable concentration tracking. The Amount in Body sensor reports `unknown` (shown as N/A) when Elimination Half-Life is left at 0 — a concentration without elimination has no meaningful value, so the sensor no longer shows an infinitely accumulating number. The Steady State sensor is only created for scheduled medications.

### Step 4: Symptom, Adherence and Tracking

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| Tracked Symptoms | Multi-select | Check which symptoms to track (Pain, Mood, Nausea, Fatigue). Each gets a daily-locked 0–10 slider. | None |
| Custom Symptoms | Text | Separate multiple with commas (e.g. brain fog, joint stiffness). A daily-locked 0–10 slider is created for each. | — |
| Calendar Entity | Toggle | Show expected dose times on the HA calendar. Not available for As Needed. | Off |
| Track Dose Adherence | Toggle | Show how consistently you take doses on time. Creates 7, 14, 30, and 365-day adherence sensors. | On (Off for As Needed) |
| On-Time Window | 1–1440 min | How early or late a dose can be and still count as on-time. For example, 60 minutes means ±60 minutes around the scheduled time. Also controls when the card transitions to the overdue warning state — at half this value, the card begins showing the overdue indicator. Applies to all scheduled medications (whether or not adherence tracking is on). | 60 |

### Reconfiguring After Setup

Click **Configure** on the integration entry to change settings without recreating the medication. The reconfiguration flow has 3 steps:

**Step 1: Schedule & Dosing** — A **Tracking Type** dropdown at the top lets you change how the medication is scheduled (e.g. from Regular Interval to Cyclic, or to As Needed). If you change it, an extra **New Schedule** step appears to collect the new type's schedule fields. If you keep the same type, the current schedule fields are shown inline. Dose history and effectiveness logs are preserved across the change.

**Step 2: Pharmacokinetics** (same as Step 3 above)

**Step 3: Symptom, Adherence and Tracking** (same as Step 4 above)

> **Note:** The medication name and release type can't be changed after creation. The tracking type *can* be changed from the Configure dialog.

> **Changes apply automatically.** After saving, schedule, dosing, and PK changes propagate to all sensors within about a minute, or instantly when you log your next dose. No device reload is needed. The exceptions are: enabling/disabling the Calendar, Adherence, or tracked symptoms (which add or remove entities), **setting or clearing the 24h Strength Limit** (which adds or removes the 24h Limit Exceeded binary sensor), and **changing the Tracking Type** (which reloads the device to recreate its sensors). In all cases your dose history and effectiveness logs are preserved.

---

### Changing the Home Assistant Timezone

AX Dose Logger reads the current time from Home Assistant's configured timezone (`time_zone` in `configuration.yaml` or the UI General settings). Two scenarios affect how your medication schedule is displayed:

**Daylight Saving Time transitions (spring forward / fall back)**
- **Time of Day** and **Cyclic** schedules are wall-clock-anchored — a slot set at 08:00 stays at 08:00 across both DST transitions. Your medication times do not shift.
- **Regular Interval** schedules are elapsed-time-anchored (the next dose is always `hours_between` real hours after the last dose). On a spring-forward day the wall-clock gap between two doses is one hour shorter; on a fall-back day it is one hour longer. The *actual* dosing interval is always preserved (pharmacokinetically correct and keeps the minimum-spacing safety floor). The Calendar entity uses the same anchor as the Next Dose sensor, so the two always agree.

**Changing the HA timezone setting (relocation / travelling)**
- Dose timestamps are stored as absolute UTC instants (ISO 8601 with offset), so your dose history and all PK calculations (concentration, steady state) are preserved exactly — only their wall-clock display changes.
- **Regular Interval** is inherently zone-safe — the next-dose instant is computed from the last dose's absolute instant, so it carries over correctly to the new timezone.
- **Time of Day** and **Cyclic** rebuild their slot grid in the new timezone. Historical doses taken in the old zone may not match the new zone's slot grid for the transition day, which can cause a one-day adherence dip. This self-heals as soon as you take your next dose in the new timezone — no action is needed. The schedule resumes correct tracking from that point.

### Multi-User Households (Profiles)

The drinks subsystem supports **fully isolated per-person tracking** for multi-user households via a Many-to-Many topology between **Profiles** (the biological PK layer) and **Drinks** (the global inventory layer). Single-user setups are unaffected — an implicit "Default" profile is used automatically.

#### Profiles (biological layer)

A **profile** is a Drink Settings config entry representing one person. Each profile owns:
- Its own PK constants (caffeine half-life, caffeine t-max, alcohol elimination rate) — editable via **Configure** on that profile's Drink Settings entry.
- Its own per-substance daily limits (caffeine mg, alcohol g).
- Its own two Master Tracker devices ("Alice Caffeine Tracker", "Alice Alcohol Tracker") with fully independent decay curves.
- Its own `.storage` files for the aggregated dose history + body mass.

Profiles are identified by an **immutable UUID** (the config entry's own `entry_id`) — the display name ("Alice") is mutable and can be renamed via the Drink Settings options flow without affecting routing, storage, or device identity. The legacy single-user "Default" profile uses a reserved literal id (`default`) and keeps the original un-profiled device names ("Caffeine Tracker") + store files — no migration, no broken entities.

#### Drinks (global inventory layer)

A **drink** (e.g. "Coca-Cola 33cl") is a global household asset, **not** owned by any profile. At setup you pick which profiles may route PK payloads from it via a multi-select **Allowed Profiles** field. Examples:
- A 12-pack shared by two partners → Allowed Profiles = `[Alice, Bob]`.
- Alice's personal morning coffee → Allowed Profiles = `[Alice]`.
- A pure-inventory tracker (no PK) → Allowed Profiles = `[]` (empty — valid; tracks stock only).

Reassign a drink's allowed profiles any time via the drink's **Configure** (options) flow.

#### Logging a shared drink (split-routing)

When a drink has more than one allowed profile, pressing **Log Drink** requires choosing **whose** body the payload routes to. The dashboard card shows a **"Who is logging this?"** popup and calls the `ax_dose_logger.log_drink` service with a `target_profile` argument:

```yaml
service: ax_dose_logger.log_drink
data:
  entry_id: <the shared drink's config entry id>
  target_profile: <the profile UUID whose Master Tracker should receive the dose>
```

The local inventory always decrements (the 12-pack loses one unit regardless of who drank it); only the PK payload is routed to the chosen profile. Automations reading the `ax_dose_logger_drink_taken` event can disambiguate via the `device_owner_id` (the drink's native profile) and `target_profile_id` (whose PK curve changed) event fields.

- A drink with **one** allowed profile: `target_profile` is optional (defaults to that profile) — single-user behavior, no popup.
- A drink with **zero** allowed profiles: logs to inventory only (no PK routing).
- The raw **Log Drink button entity** (stateless HA trigger) cannot carry a per-press target, so it uses the single-profile convenience default and raises if the drink is shared — use the card for shared drinks.

#### Undo / Reset with split-routing

Undo and Reset are **per-dose aware**: each logged dose records which profile received it, so Undo removes the dose from *that* profile's Master Tracker (not the drink's native profile), and Reset surgically removes only this drink's contributions from each affected profile (rather than wiping one profile's entire curve). This prevents the corruption that would occur if a shared drink's reset wiped an unrelated person's caffeine curve.

#### Deleting a profile (non-destructive)

Removing a profile's Drink Settings entry **does not** delete any drink devices. The integration scrubs the deleted profile's UUID from every drink's Allowed Profiles array — the drinks survive for the remaining users. A drink whose Allowed Profiles becomes empty after scrubbing degrades to a pure-inventory tracker (no PK routing); it is never deleted.

#### Per-profile retention

Each Drink Settings entry has its own **History Retention** slider, so Alice and Bob can have different retention windows if desired. Granular drinks inherit retention from whichever profile's Drink Settings entry is found first (the universal drinks retention model is preserved).

---

## Entity States & Attributes

> This section is for advanced users who want to build custom templates beyond the dedicated AX Dose Logger Card. The card handles all of this automatically — you only need these details if you're hand-rolling your own Lovelace templates.

Key entities and their attributes for template references:

**Pills Safe to Take** (`sensor.ibuprofen_pills_safe_to_take`)
- State: number of pills safe to take remaining (integer)
- `timestamps`: list of recent dose timestamps within the window
- `time_window_hours`: configured rolling window size
- `in_on_window`: (Cyclic only) whether currently in an ON period
- `window_expires_at`: when the oldest in-window dose expires and the limit will increment (ISO datetime); `null` when not at the limit. This is the true "when can I safely take another" time, distinct from the Next Dose schedule.

**Next Dose** (`sensor.ibuprofen_next_dose`)
- State: datetime of next scheduled dose. For scheduled medications (Time of Day, Cyclic), this is always the next prescribed clock slot — taking a dose late does not drift the schedule. For multi-dose Time of Day schedules, a dose taken late is assigned to the slot it covers (lateness extends until the next scheduled slot), so a 17:30 dose on a 13:00 + 21:00 schedule counts as the late 13:00 dose and leaves 21:00 as the next due slot. The safety gate (whether it's actually safe to take now) is the separate Pills Safe to Take sensor.
- `safe_to_take`: number of pills safe to take right now

**Amount in Body** (`sensor.ibuprofen_amount_in_body`)
- State: current drug amount in mg (float, 1 decimal)
- *Instant Release attributes:*
  - `gut_mass`: drug remaining in gut compartment (mg)
  - `ka`: absorption rate constant (h⁻¹)
  - `lag_time`: configured lag time (min)
  - `dose_history`: list of `[timestamp, strength]` pairs
- *Sustained Release attributes:*
  - `gut_ir_mass`: drug in IR gut compartment (mg)
  - `matrix_sr_mass`: drug remaining in SR matrix (mg)
  - `gut_sr_mass`: drug in SR gut compartment (mg)
  - `ka`: absorption rate constant (h⁻¹)
  - `kr`: SR release rate constant (h⁻¹)
  - `lag_time`: configured lag time (min)
  - `dose_history`: list of `[timestamp, strength]` pairs

**Amount in Last 24h** (`sensor.ibuprofen_amount_in_last_24h`)
- State: total dose strength (in this medication's unit — mg/μg/g) consumed in the last 24 hours (float, 1 decimal). This is **intake** (how much you swallowed), not body load. For the current active amount in your body after absorption/elimination, see the *Amount in Body* sensor above.
- `window_hours`: `24` (fixed rolling window)
- `doses_in_window`: count of doses logged in the window
- `daily_limit`: configured 24h limit (in the medication's unit), or `null` when set to `0` (no limit)
- `remaining`: `daily_limit - amount`, or `null` when no limit is configured
- `unit_of_measurement`: the medication's strength unit (mg/μg/g)

**Dose Status** (`sensor.ibuprofen_dose_status`)
- State: one of `not_due` / `due` / `overdue` / `limit_reached` / `limit_24h` / `ok`. A single enum sensor that answers "can/should I take it right now?" — the same state machine the card's button uses, so automations and the card can never disagree. Created for all tracking types (As-Needed meds report `ok` / `limit_reached` / `limit_24h`).
  - `not_due` — scheduled medication, next slot still in the future
  - `due` — the scheduled slot has arrived (within the first half of the grace window)
  - `overdue` — past half the grace window (latency warning)
  - `limit_reached` — pill-count rolling window is full (or Cyclic OFF day)
  - `limit_24h` — 24h strength limit already exceeded, or the next dose would push over it
  - `ok` — As-Needed medication, available to take
- `next_dose_at`: next scheduled slot (ISO datetime; `null` for As-Needed)
- `overdue_since`: when the missed slot began (ISO datetime; `null` when not overdue)
- `grace_minutes`: configured on-time window
- `safe_count`: pills safe to take right now (mirrors the Pills Safe to Take sensor)
- `amount_24h` / `daily_limit`: 24h strength sum and configured cap
- `tracking_type`: the medication's tracking type
- The sensor flips states at the exact transition instants (slot arrival, half-grace boundary, window expiry) via point-in-time timers — no waiting for the next minute tick. Use it in automations with a State trigger, e.g. `trigger: state → entity_id: sensor.ibuprofen_dose_status → to: due`.

**24h Limit Exceeded** (`binary_sensor.ibuprofen_24h_limit_exceeded`)
- State: `on` / `off`. Turns on when the current 24h strength sum has already exceeded the configured `daily_limit`, OR when the next configured dose would push the total over the limit (pre-warning). Only created when `daily_limit > 0`.
- `current_amount`: total strength consumed in the last 24 hours
- `daily_limit`: the configured 24h strength cap
- `next_dose_strength`: the per-dose strength that would be added
- `remaining`: `daily_limit - current_amount`
- `already_exceeded`: `True` when `current_amount > daily_limit`
- `would_exceed`: `True` when `current_amount + next_dose_strength > daily_limit` but not already exceeded
- `unit_of_measurement`: the medication's strength unit (mg/μg/g)

**Steady State** (`sensor.ibuprofen_days_to_steady_state`)
- State: days remaining to 90% steady state (float, 1 decimal), `0.0` if reached, or `unknown` when elimination is disabled
- `theoretical_max_mg`: predicted peak at steady state (C_max_ss)
- `steady_state_trough_mg`: predicted trough at steady state (C_min_ss)
- `threshold_mg`: the 90% trough threshold the state is tested against
- `current_mass`: instantaneous body mass
- `projected_trough_mg`: the body mass projected to the next dose (the trough the sensor tests)
- `current_percentage`: progress toward the trough asymptote as a percentage
- `dosing_interval_hours`: the effective longest-gap interval (τ) used
- `last_dose_timestamp`: the most recent dose time

**Adherence** (`sensor.ibuprofen_adherence_7_days`, etc.)
- State: adherence percentage (integer, clamped at 100%)
- `actual_doses`: number of on-time doses in the window
- `expected_doses`: number of expected doses in the window
- `grace_hours`: configured grace period

**Sleep Disruption** (Master Tracker — `sensor.sleep_disruption`)
- State: categorical label (`None` / `Low` / `Moderate` / `High`)
- `body_mass`: raw current body-mass (mg caffeine / g alcohol)
- `body_mass_unit`: unit string
- `current_band`: the current band label
- `next_band`: the next-lower band label
- `minutes_until_next_band`: estimated decay time to the next-lower band

---

## Building Automations

Each medication and drink shows up as a **Device** in Home Assistant. Replace `ibuprofen` with your entity name in the examples below.

### Sensors

| Sensor | Entity ID | What It Shows | Key Attributes |
|--------|-----------|---------------|----------------|
| Total Doses | `sensor.ibuprofen_total_doses` | Cumulative lifetime dose count | — |
| Days Since First Dose | `sensor.ibuprofen_days_since_first_dose` | Integer days elapsed since the first recorded dose | `first_dose_timestamp`, `history_start_date` |
| Last Dose | `sensor.ibuprofen_last_dose` | Timestamp of most recent dose | — |
| Pills Safe to Take | `sensor.ibuprofen_pills_safe_to_take` | Remaining pills safe to take in the current window | `timestamps`, `time_window_hours`, `in_on_window` (Cyclic only), `window_expires_at` (when the limit resets; `null` if not at the limit) |
| Amount in Body | `sensor.ibuprofen_amount_in_body` | Current drug amount in body (mg) — requires PK fields | `last_updated`, `gut_mass`, `ka`, `lag_time`, `dose_history` (IR); `gut_ir_mass`, `matrix_sr_mass`, `gut_sr_mass`, `ka`, `kr`, `lag_time`, `dose_history` (ER) |
| Amount in Last 24h | `sensor.ibuprofen_amount_in_last_24h` | Total dose strength consumed in the last 24 hours (mg/μg/g) | `window_hours`, `doses_in_window`, `daily_limit` (`null` when 0), `remaining` (`null` when no limit), `unit_of_measurement` |
| 24h Limit Exceeded | `binary_sensor.ibuprofen_24h_limit_exceeded` | On when 24h strength limit is/would be exceeded (only when `daily_limit > 0`) | `current_amount`, `daily_limit`, `next_dose_strength`, `remaining`, `already_exceeded`, `would_exceed`, `unit_of_measurement` |
| Next Dose | `sensor.ibuprofen_next_dose` | Timestamp of next scheduled dose | `safe_to_take` (number of pills safe to take remaining now) |
| 7-Day Average | `sensor.ibuprofen_avg_daily_doses_7_days` | Day-level dose coverage over 7 days (0.0–1.0) | `covered_days`, `scheduled_days`, `effective_window_days` |
| 14-Day Average | `sensor.ibuprofen_avg_daily_doses_14_days` | Day-level dose coverage over 14 days (0.0–1.0) | `covered_days`, `scheduled_days`, `effective_window_days` |
| 30-Day Average | `sensor.ibuprofen_avg_daily_doses_30_days` | Day-level dose coverage over 30 days (0.0–1.0) | `covered_days`, `scheduled_days`, `effective_window_days` |
| Yearly Average | `sensor.ibuprofen_avg_daily_doses_yearly` | Day-level dose coverage over 365 days (0.0–1.0) | `covered_days`, `scheduled_days`, `effective_window_days` |
| 7-Day Adherence | `sensor.ibuprofen_adherence_7_days` | Adherence % over 7 days | `actual_doses`, `expected_doses`, `grace_hours` |
| 14-Day Adherence | `sensor.ibuprofen_adherence_14_days` | Adherence % over 14 days | `actual_doses`, `expected_doses`, `grace_hours` |
| 30-Day Adherence | `sensor.ibuprofen_adherence_30_days` | Adherence % over 30 days | `actual_doses`, `expected_doses`, `grace_hours` |
| 365-Day Adherence | `sensor.ibuprofen_adherence_365_days` | Adherence % over 365 days | `actual_doses`, `expected_doses`, `grace_hours` |
| Steady State | `sensor.ibuprofen_days_to_steady_state` | Days remaining to 90% steady state (trough-anchored) — scheduled medications only, requires PK fields | `theoretical_max_mg`, `steady_state_trough_mg`, `threshold_mg`, `current_mass`, `projected_trough_mg`, `current_percentage`, `dosing_interval_hours`, `last_dose_timestamp` |
| Strength | `sensor.ibuprofen_strength` | Configured per-dose strength (mg) | — |
| Days Left | `sensor.ibuprofen_days_left` (scheduled) or `sensor.ibuprofen_days_left_est` (As Needed) | How many days the current inventory lasts | `stock`, `doses_per_day`, `estimation`, `window_days` |

> **PK fields note:** The Amount in Body sensor only produces meaningful values when **Dose Strength** and **Elimination Half-Life** are configured (non-zero). The Steady State sensor additionally requires a fixed dosing interval — it is only created for scheduled medications (Regular Interval, Time of Day, Cyclic), not As Needed.

### Buttons

| Button | Entity ID | What It Does |
|--------|-----------|-------------|
| Take | `button.ibuprofen_take` | Log a dose |
| Reset History | `button.ibuprofen_reset_history` | Wipe dose history (keeps inventory) |
| Undo Dose | `button.ibuprofen_undo_dose` | Revert the most recent dose across all sensors and PK model |
| Reset Adherence % | `button.ibuprofen_reset_adherence` | Clear adherence percentage history only — does NOT affect Amount in Body, dose count, or any other sensor |
| Mark Last Adherence Taken | `button.ibuprofen_mark_last_adherence_taken` | Mark the most recent missed dose slot as taken for adherence calculation only — does NOT add a dose to the PK model or dose count |
| Skip Dose | `button.ibuprofen_skip_dose` | Skip the current missed scheduled dose slot — clears overdue + advances next-dose without logging a dose. PK, inventory, totals, and last dose untouched; adherence stays penalized |

### Numbers

| Number | Entity ID | Range | What It Does |
|--------|-----------|-------|-------------|
| Pills Left | `number.ibuprofen_pills_left` | 0–9999 | Current inventory count |
| Add Refill | `number.ibuprofen_add_refill` | 0–9999 | Refill input (auto-resets to 0 after adding) |
| Effectiveness | `number.ibuprofen_{metric}_effectiveness` | 0–10 | Daily-locked per-metric rating slider (unknown until set, resets at midnight) |

### Calendar

| Calendar | Entity ID | What It Shows |
|----------|-----------|---------------|
| Dose Calendar | `calendar.ibuprofen_calendar` | Expected dose times on the HA calendar (optional, enabled by default) |

### Events

AX Dose Logger fires events on the Home Assistant event bus that you can use in automations:

| Event | When It Fires | Event Data |
|-------|--------------|------------|
| `ax_dose_logger_dose_taken` | Any Take button is pressed | `medication_name`, `timestamp` |
| `ax_dose_logger_dose_undone` | Any Undo button is pressed | `medication_name` |
| `ax_dose_logger_adherence_override` | Mark Last Adherence Taken button is pressed | `entity_id` |
| `ax_dose_logger_dose_skipped` | Skip Dose button is pressed | `entry_id`, `timestamp` |
| `ax_dose_logger_drink_taken` | Any Log Drink button is pressed | `entry_id`, `drink_type`, `dose_strength`, `drink_name` |

### Automation Examples

**Trigger when a dose is taken:**
```yaml
automation:
  - trigger:
      - platform: event
        event_type: ax_dose_logger_dose_taken
        event_data:
          medication_name: Ibuprofen
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: "Ibuprofen dose logged at {{ trigger.event.data.timestamp }}"
```

**Alert when pill limit reaches 0:**
```yaml
automation:
  - trigger:
      - platform: numeric_state
        entity_id: sensor.ibuprofen_pills_safe_to_take
        below: 1
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: "⚠️ No pills safe to take for Ibuprofen"
```

**Notify when a dose becomes due (Dose Status sensor):**
```yaml
automation:
  - trigger:
      - platform: state
        entity_id: sensor.ibuprofen_dose_status
        to: "due"
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: "💊 Ibuprofen dose is due now"
```

**Escalate when a dose becomes overdue:**
```yaml
automation:
  - trigger:
      - platform: state
        entity_id: sensor.ibuprofen_dose_status
        to: "overdue"
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: "🔴 Ibuprofen dose is overdue — the on-time window is closing"
```

**Notify when steady state is reached** (scheduled medications only):
```yaml
automation:
  - trigger:
      - platform: numeric-state
        entity_id: sensor.ibuprofen_days_to_steady_state
        below: 0.1
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: "✅ Ibuprofen has reached steady state"
```

---

*This integration is for informational and home automation purposes only. It is not a certified medical device. Always follow your doctor's advice and the instructions on your prescription.*