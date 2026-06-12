# 💊 Home Assistant Pill Logger

A fully local, advanced medication tracking and pharmacokinetics integration for Home Assistant.  
Pill Logger goes far beyond simple counters — it models drug amount in the body with a two-compartment PK engine, tracks rolling time windows, warns against accidental overdoses, calculates steady-state progress, lets you log subjective effectiveness, and powers actionable mobile reminders.

## ✨ Features

### 🕐 Scheduling
* **Regular Interval** — Track medications taken every N hours (e.g. every 8 hours).
* **Time of Day** — Schedule a daily dose at a fixed time (e.g. 08:30 every morning).
* **As Needed** — PRN tracking with a configurable rolling window (e.g. max 2 pills per 8 hours).
* **Cyclic/Calendar Pattern** — Define on/off cycles (e.g. 5 days on, 2 days off) anchored to a start date, with a per-day dose time.

### 📅 Calendar Entity
* **Dose Calendar** — Each medication can optionally create a `calendar.<medication_name>` entity that plots expected dose times on the Home Assistant calendar. Event generation varies by tracking type: **Time of Day** shows a daily event at the configured time; **Regular Interval** shows events every N hours anchored to midnight; **Cyclic** shows events on ON days at the dose time; **As Needed** shows no future events (unpredictable). Toggle this on or off at any time via the integration options.

### 🛡️ Safety
* **Safe Dose Tracking** — All tracking modes use a unified sliding-window algorithm: set how many doses are safe within a configurable time window. Each pill expires individually, so safe doses recover one at a time as each pill's window passes. On Cyclic OFF days, safe doses are forced to 0.
* **Next Dose `safe_to_take` Attribute** — The Next Dose sensor shows the next scheduled dose time and includes a `safe_to_take` attribute (number) showing how many safe doses remain right now based on the sliding window.
* **Smart Overdose Warning** — Dashboard UI dynamically swaps to a red warning button when safe doses reach 0, prompting an "Are you sure?" dialog before allowing an override.

### 🧪 Pharmacokinetics
* **Amount in Body Sensor** — Models drug amount in the body (mg) over time using a two-compartment model with configurable strength, half-life, and hours-to-peak. Absorption rate (kₐ) is solved dynamically from time-to-peak.
* **Steady State Sensor** — Calculates days remaining until 90% steady state, with attributes showing theoretical max amount and current percentage achieved.
* **Strength Sensor** — Displays the configured per-dose strength (mg) for quick reference.

### 📊 Effectiveness Tracking
* **Standard Metrics** — Optionally enable 1–10 sliders for Pain Level, Mood, Nausea Level, and Fatigue Level to log how well the medication is working for each dose.
* **Custom Metrics** — Add your own tracking metrics separated by commas (e.g. "brain fog, joint stiffness") and get a slider for each.

### 📈 Insights
* **Adherence Percentage** — Four rolling adherence sensors (7, 14, 30, and 365 days) calculate the percentage of scheduled doses actually taken on time. Uses a configurable **grace period** (default: 1 hour) — doses taken within ±grace of the expected time count as compliant. For Regular Interval mode, adherence is anchored to the patient's actual dosing schedule (not clock boundaries), with forward gap filling to correctly penalize missed doses after the last taken dose. Cyclic mode only counts ON days in the denominator. As Needed (PRN) medications report `None` since adherence is undefined without a schedule. Over-dosing is clamped at 100% with raw counts visible in attributes.
* **Rolling Averages** — Automatically tracks consumption patterns with rolling averages for 7 days, 14 days, 30 days, and yearly (365 days). For scheduled modes (Regular Interval, Time of Day, Cyclic), averages use **schedule-aligned counting with a grace period** — doses taken within ±grace of the expected time count as on-schedule, eliminating the 0.9/1.1 oscillation caused by sliding-window edge effects. As Needed mode uses the traditional sliding window. Sensors scale calculations from the moment the medication is added or reset.
* **Total Doses** — Cumulative lifetime dose counter.
* **Last Dose** — Timestamp of the most recent dose.

### 💊 Inventory
* **Smart Inventory** — Tracks remaining pills. To refill, double-tap the inventory card, type the new box amount, and it automatically adds to your total and resets the input to 0.
* **Undo Last Dose** — Accidentally pressed Take? The Undo button reverts the most recent dose across all sensors, counters, and the PK model — restoring inventory, removing the timestamp, and recalculating the concentration curve from dose history.
* **Native Countdowns** — Outputs the exact `datetime` of your next available dose, allowing Home Assistant to show live-ticking countdowns like "Wait: 2 hours" or "Available now".

### ⚙️ Configuration
* **Reconfigurable Settings** — Change schedule, intervals, safe dose limits, PK parameters, and effectiveness metrics at any time via the "Configure" button — no need to delete and recreate.
* **Built-in Reset** — Dedicated configuration button to wipe a medication's history and start fresh without losing inventory counts.

### 🔔 Reminders
* **Blueprint Included** — Pre-built Blueprint for actionable mobile notifications (Take, Skip, Snooze) with automatic loop and clear-on-take.

---

## 📊 Entity Reference

Each medication creates a **Device** with the following entities:

| Platform | Entity ID Pattern | Description |
|----------|-------------------|-------------|
| `sensor` | `{name}_total` | Cumulative lifetime dose count |
| `sensor` | `{name}_last_dose` | Timestamp of most recent dose |
| `sensor` | `{name}_safe_doses` | Remaining safe doses in the current time window |
| `sensor` | `{name}_concentration` | Current drug amount in body (mg) — requires PK fields |
| `sensor` | `{name}_next_dose` | Timestamp of next scheduled dose; `safe_to_take` attribute shows remaining safe doses |
| `sensor` | `{name}_avg_daily_doses_7_days` | 7-day rolling average of daily doses; `grace_hours` attribute shows active grace period |
| `sensor` | `{name}_avg_daily_doses_14_days` | 14-day rolling average of daily doses; `grace_hours` attribute shows active grace period |
| `sensor` | `{name}_avg_daily_doses_30_days` | 30-day rolling average of daily doses; `grace_hours` attribute shows active grace period |
| `sensor` | `{name}_avg_daily_doses_yearly` | 365-day rolling average of daily doses; `grace_hours` attribute shows active grace period |
| `sensor` | `{name}_adherence_7_days` | 7-day rolling adherence %; `actual_doses`, `expected_doses`, `grace_hours` attributes |
| `sensor` | `{name}_adherence_14_days` | 14-day rolling adherence %; `actual_doses`, `expected_doses`, `grace_hours` attributes |
| `sensor` | `{name}_adherence_30_days` | 30-day rolling adherence %; `actual_doses`, `expected_doses`, `grace_hours` attributes |
| `sensor` | `{name}_adherence_365_days` | 365-day rolling adherence %; `actual_doses`, `expected_doses`, `grace_hours` attributes |
| `sensor` | `{name}_steady_state` | Days remaining to 90% steady state — requires PK fields |
| `sensor` | `{name}_strength` | Configured per-dose strength (mg) |
| `button` | `take_{name}` | Log a dose |
| `button` | `reset_{name}_history` | Wipe dose history (keeps inventory) |
| `button` | `undo_{name}_dose` | Revert the most recent dose across all sensors and PK model |
| `number` | `{name}_pills_left` | Current inventory count |
| `number` | `add_{name}_refill` | Refill input (auto-resets to 0 after adding) |
| `number` | `{name}_{metric}_effectiveness` | 1–10 slider per enabled effectiveness metric |
| `calendar` | `{name}_calendar` | Expected dose times on the HA calendar (optional, enabled by default) |

> **PK fields note:** The Amount in Body and Steady State sensors only produce meaningful values when **Strength** and **Half-Life** are configured (non-zero). If left at 0, they will report `0` / `None`.

---

## ⚙️ Configuration Flows

Setup is a 4-step process: **Step 1** — name your medication and choose a tracking mode; **Step 2** — configure the schedule and dosing; **Step 3** — set optional pharmacokinetic parameters; **Step 4** — choose which effectiveness metrics to track and configure adherence.

All numeric fields use **NumberSelector (box mode)** with unit labels, min/max validation, and increment/decrement buttons for precise input.

### Step 1: Add a Medication
| Field | Type | Description | Default |
|-------|------|-------------|---------|
| Medication Name | Text | Display name for the device | My Medication |
| Tracking Type | Dropdown | Each option includes a brief description of the mode | Regular Interval |

> Dropdown options include inline descriptions: **Regular Interval — fixed hours between doses**, **Time of Day — once daily at a set time**, **As Needed — on-demand with a safety limit**, **Cyclic — on/off day patterns**.

### Step 2: Schedule & Dosing

#### Regular Interval
| Field | Range | Description | Default |
|-------|-------|-------------|---------|
| Inventory | 0–9999 pills | Number of pills currently available | 30 |
| Dose Interval | 1–48 h | Minimum hours between consecutive doses | 8 |
| Safe Doses | 1–20 doses | Maximum doses allowed within the time window | 1 |
| Time Window | 0.5–168 h | Rolling window for safe dose calculation, e.g. max 3 pills in 24 hours | 8 |
| Calendar Entity | Toggle | Show expected dose times on the HA calendar | On |

#### Time of Day
| Field | Range | Description | Default |
|-------|-------|-------------|---------|
| Inventory | 0–9999 pills | Number of pills currently available | 30 |
| Dose Time | Time picker | Time of day to take the medication | 08:00 |
| Safe Doses | 1–20 doses | Maximum doses allowed within the time window | 1 |
| Time Window | 0.5–168 h | Rolling window for safe dose calculation, e.g. max 2 pills in 24 hours | 24 |
| Calendar Entity | Toggle | Show expected dose times on the HA calendar | On |

#### As Needed (PRN)
| Field | Range | Description | Default |
|-------|-------|-------------|---------|
| Inventory | 0–9999 pills | Number of pills currently available | 30 |
| Safe Doses | 1–20 doses | Maximum doses allowed within the time window | 2 |
| Time Window | 0.5–168 h | Rolling window for safe dose calculation, e.g. max 2 pills in 8 hours | 8 |
| Calendar Entity | Toggle | Show expected dose times on the HA calendar | On |

#### Cyclic/Calendar Pattern
| Field | Range | Description | Default |
|-------|-------|-------------|---------|
| Inventory | 0–9999 pills | Number of pills currently available | 30 |
| Days On | 1–30 days | Number of active days in the cycle | 5 |
| Days Off | 1–30 days | Number of rest days in the cycle | 2 |
| Cycle Start Date | Date picker | Start date of the on/off cycle | Today |
| Dose Time | Time picker | Time of day to take on active days | 08:00 |
| Safe Doses | 1–20 doses | Maximum doses allowed within the time window | 1 |
| Time Window | 0.5–168 h | Rolling window for safe dose calculation, e.g. max 1 pill in 24 hours | 24 |
| Calendar Entity | Toggle | Show expected dose times on the HA calendar | On |

### Step 3: Pharmacokinetics

| Field | Range | Description | Default |
|-------|-------|-------------|---------|
| Dose Strength | 0–9999 mg | Amount of medication per dose. Set to 0 if not tracking concentration. | 0 |
| Elimination Half-Life | 0–168 h | Time for the body to eliminate half the drug. Set to 0 if not tracking concentration. | 0 |
| Time to Peak Concentration | 0–72 h | Hours after taking until concentration peaks. Set to 0 for immediate-release medications. | 0 |

> Leave all values at 0 if you don't need concentration or steady-state tracking.

### Step 4: Metrics Tracker & Adherence

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| Pain | Toggle | Enable a 1–10 slider for pain | Off |
| Mood | Toggle | Enable a 1–10 slider for mood | Off |
| Nausea | Toggle | Enable a 1–10 slider for nausea | Off |
| Fatigue | Toggle | Enable a 1–10 slider for fatigue | Off |
| Custom Metrics | Text | Separate multiple with commas, e.g. brain fog, joint stiffness. A 1–10 slider is created for each. | — |

**Adherence Tracking** section:

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| Track Dose Adherence | Toggle | Show how consistently you take doses on time. Creates 7, 14, 30, and 365-day adherence sensors. | On (Off for As Needed) |
| On-Time Window | 0.5–24 h | How early or late a dose can be and still count as on-time. For example, 1 hour means ±1 hour around the scheduled time. Only applies when adherence tracking is on. | 1 |

---

## 🔧 Reconfiguring After Setup

Click **Configure** on the integration entry to change settings without recreating the medication. Reconfiguration is a 3-step process:

### Step 1: Schedule & Dosing

| Tracking Type | Editable Fields |
|---------------|-----------------|
| Regular Interval | Dose Interval, Time Window, Safe Doses, Calendar Entity |
| Time of Day | Dose Time, Time Window, Safe Doses, Calendar Entity |
| As Needed | Time Window, Safe Doses, Calendar Entity |
| Cyclic/Calendar Pattern | Days On, Days Off, Cycle Start Date, Dose Time, Time Window, Safe Doses, Calendar Entity |

### Step 2: Pharmacokinetics

| Field | Range | Description | Default |
|-------|-------|-------------|---------|
| Dose Strength | 0–9999 mg | Amount of medication per dose. Set to 0 if not tracking concentration. | 0 |
| Elimination Half-Life | 0–168 h | Time for the body to eliminate half the drug. Set to 0 if not tracking concentration. | 0 |
| Time to Peak Concentration | 0–72 h | Hours after taking until concentration peaks. Set to 0 for immediate-release medications. | 0 |

### Step 3: Metrics Tracker & Adherence

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| Pain | Toggle | Enable a 1–10 slider for pain | Off |
| Mood | Toggle | Enable a 1–10 slider for mood | Off |
| Nausea | Toggle | Enable a 1–10 slider for nausea | Off |
| Fatigue | Toggle | Enable a 1–10 slider for fatigue | Off |
| Custom Metrics | Text | Separate multiple with commas, e.g. brain fog, joint stiffness. A 1–10 slider is created for each. | — |

**Adherence Tracking** section:

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| Track Dose Adherence | Toggle | Show how consistently you take doses on time. Creates 7, 14, 30, and 365-day adherence sensors. | On (Off for As Needed) |
| On-Time Window | 0.5–24 h | How early or late a dose can be and still count as on-time. For example, 1 hour means ±1 hour around the scheduled time. Only applies when adherence tracking is on. | 1 |

> **Note:** The medication name and tracking type cannot be changed after creation. To switch, remove the entry and create a new one.

---

## 🛠️ Installation

### 1. Install via HACS (Recommended)
1. Open HACS in your Home Assistant instance.
2. Click the 3 dots in the top right → **Custom repositories**.
3. Paste the URL of this repository and select **Integration** as the category.
4. Click **Download** and restart Home Assistant.

### 2. Add your Medications
1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Pill Logger**.
3. Follow the 3-step setup: choose a medication name and tracking type → configure schedule and dosing → select effectiveness metrics to track.
4. Repeat for as many medications as you need. All entities are grouped into a single Device per medication.

---

## 📱 The Dashboard (UI)

To get a beautiful, app-like experience on your dashboard, you will need three popular frontend plugins installed via HACS:
* [Mushroom Cards](https://github.com/piitaya/lovelace-mushroom)
* [Vertical Stack In Card](https://github.com/ofekashery/vertical-stack-in-card)
* [Card-Mod](https://github.com/thomasloven/lovelace-card-mod)

Once those are installed, add a "Manual" card to your dashboard and paste this code. *(Replace `YOUR_MEDICATION` with your medication's entity name!)*

**💡 How to refill:** Double-click the "Inventory Left" box to open the refill dialog, enter the new box amount, and close it to instantly add to your inventory.

```yaml
type: custom:vertical-stack-in-card
cards:
  - type: custom:mushroom-template-card
    entity: sensor.YOUR_MEDICATION_next_dose
    primary: YOUR_MEDICATION
    secondary: >-
      {% set next = states('sensor.YOUR_MEDICATION_next_dose') | as_datetime(None) %}
      {% if next == None or next <= now() %}
        Available now
      {% else %}
        {% set total_seconds = (next - now()).total_seconds() %}
        {% set hours = (total_seconds // 3600) | int %}
        {% set minutes = ((total_seconds % 3600) // 60) | int %}
        Wait: {% if hours > 0 %}{{ hours }} hours {% endif %}{{ minutes }} minutes
      {% endif %}
    card_mod:
      style: |
        ha-card {
          zoom: 1.2;
        }
  - type: horizontal-stack
    cards:
      - type: vertical-stack
        cards:
          - type: conditional
            conditions:
              - condition: numeric_state
                entity: sensor.YOUR_MEDICATION_safe_doses
                above: 0
            card:
              type: custom:mushroom-template-card
              primary: Take Pill
              secondary: |-
                {% set last_dose = states('sensor.YOUR_MEDICATION_last_dose') %}
                {% if last_dose not in ['unknown', 'unavailable', 'None', ''] %}
                  {% set total_seconds = (now() - as_datetime(last_dose)).total_seconds() %}
                  {% set hours = (total_seconds // 3600) | int %}
                  {% set minutes = ((total_seconds % 3600) // 60) | int %}
                  {% if hours > 0 %}{{ hours }} hours {% endif %}{{ minutes }} minutes ago
                {% else %}
                  Never ago
                {% endif %}
              icon: mdi:pill
              icon_color: blue
              layout: vertical
              tap_action:
                action: call-service
                service: button.press
                target:
                  entity_id: button.YOUR_MEDICATION_take_YOUR_MEDICATION
              card_mod:
                style: |
                  ha-card {
                    height: 120px !important;
                    display: flex;
                  }
                  ha-card:hover {
                    background: rgba(var(--rgb-blue), 0.1);
                    transition: background 0.2s ease;
                  }
                  ha-card:active {
                    transform: scale(0.95);
                    animation: pulse 0.3s ease;
                  }
                  @keyframes pulse {
                    0% { box-shadow: 0 0 0 0 rgba(var(--rgb-blue), 0.7); }
                    70% { box-shadow: 0 0 0 10px rgba(var(--rgb-blue), 0); }
                    100% { box-shadow: 0 0 0 0 rgba(var(--rgb-blue), 0); }
                  }
          - type: conditional
            conditions:
              - condition: state
                entity: sensor.YOUR_MEDICATION_safe_doses
                state: unknown
            card:
              type: custom:mushroom-template-card
              primary: Take Pill
              secondary: |-
                {% set last_dose = states('sensor.YOUR_MEDICATION_last_dose') %}
                {% if last_dose not in ['unknown', 'unavailable', 'None', ''] %}
                  {% set total_seconds = (now() - as_datetime(last_dose)).total_seconds() %}
                  {% set hours = (total_seconds // 3600) | int %}
                  {% set minutes = ((total_seconds % 3600) // 60) | int %}
                  {% if hours > 0 %}{{ hours }} hours {% endif %}{{ minutes }} minutes ago
                {% else %}
                  Never ago
                {% endif %}
              icon: mdi:pill
              icon_color: blue
              layout: vertical
              tap_action:
                action: call-service
                service: button.press
                target:
                  entity_id: button.YOUR_MEDICATION_take_YOUR_MEDICATION
              card_mod:
                style: |
                  ha-card {
                    height: 120px !important;
                    display: flex;
                  }
                  ha-card:hover {
                    background: rgba(var(--rgb-blue), 0.1);
                    transition: background 0.2s ease;
                  }
                  ha-card:active {
                    transform: scale(0.95);
                    animation: pulse 0.3s ease;
                  }
                  mushroom-shape-icon {
                    --icon-main-color: var(--rgb-blue) !important;
                    --icon-size: 40px !important;
                  }
                  @keyframes pulse {
                    0% { box-shadow: 0 0 0 0 rgba(var(--rgb-blue), 0.7); }
                    70% { box-shadow: 0 0 0 10px rgba(var(--rgb-blue), 0); }
                    100% { box-shadow: 0 0 0 0 rgba(var(--rgb-blue), 0); }
                  }
          - type: conditional
            conditions:
              - condition: numeric_state
                entity: sensor.YOUR_MEDICATION_safe_doses
                below: 1
            card:
              type: custom:mushroom-template-card
              primary: LIMIT REACHED
              secondary: |-
                {% set last_dose = states('sensor.YOUR_MEDICATION_last_dose') %}
                {% if last_dose not in ['unknown', 'unavailable', 'None', ''] %}
                  {% set total_seconds = (now() - as_datetime(last_dose)).total_seconds() %}
                  {% set hours = (total_seconds // 3600) | int %}
                  {% set minutes = ((total_seconds % 3600) // 60) | int %}
                  {% if hours > 0 %}{{ hours }} hours {% endif %}{{ minutes }} minutes ago
                {% else %}
                  Never ago
                {% endif %}
              icon: mdi:alert
              icon_color: red
              layout: vertical
              tap_action:
                action: call-service
                service: button.press
                target:
                  entity_id: button.YOUR_MEDICATION_take_YOUR_MEDICATION
                confirmation:
                  text: "WARNING: 0 safe doses available. Override?"
              card_mod:
                style: >
                  ha-card {
                    height: 120px !important;
                    display: flex;
                  }
                  /* 1. Hide the native cropped icon entirely */
                  ha-state-icon {
                    display: none !important;
                  }
                  /* 2. Draw a massive replacement icon AND circle using an un-croppable SVG overlay */
                  ha-card::after {
                    content: "";
                    position: absolute;
                    top: 0px;
                    left: 50%;
                    transform: translateX(-50%);
                    width: 70px;
                    height: 70px;
                    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23F44336' d='M13 14H11V9H13M13 18H11V16H13M1 21H23L12 2L1 21Z'/%3E%3C/svg%3E");
                    background-repeat: no-repeat;
                    background-position: center;
                    background-size: 50px 50px;
                    pointer-events: none;
                  }
                  ha-card:hover {
                    background: rgba(var(--rgb-red), 0.1);
                  }
                  ha-card:active {
                    transform: scale(0.95);
                    animation: pulse-red 0.3s ease;
                  }
                  mushroom-shape-icon {
                    --icon-size: 60px !important;
                    --shape-color: rgba(var(--rgb-red), 0.2) !important;
                    --shape-outline-color: rgba(var(--rgb-red), 0.5) !important;
                  }
                  @keyframes pulse-red {
                    0% { box-shadow: 0 0 0 0 rgba(var(--rgb-red), 0.7); }
                    70% { box-shadow: 0 0 0 10px rgba(var(--rgb-red), 0); }
                    100% { box-shadow: 0 0 0 0 rgba(var(--rgb-red), 0); }
                  }
      - type: vertical-stack
        cards:
          - type: custom:mushroom-template-card
            entity: sensor.YOUR_MEDICATION_safe_doses
            primary: Can take
            secondary: "{{ states('sensor.YOUR_MEDICATION_safe_doses') }}"
            icon: mdi:pill
            icon_color: blue
            tap_action:
              action: none
          - type: custom:mushroom-template-card
            entity: number.YOUR_MEDICATION_pills_left
            primary: Left
            secondary: "{{ states('number.YOUR_MEDICATION_pills_left') }}"
            icon: mdi:pill
            icon_color: blue
            tap_action:
              action: none
            double_tap_action:
              action: more-info
              entity: number.YOUR_MEDICATION_add_YOUR_MEDICATION_refill
            card_mod:
              style: |
                ha-card:hover {
                  cursor: pointer;
                  background: rgba(var(--rgb-blue), 0.05);
                }
  - type: conditional
    conditions:
      - condition: numeric_state
        entity: sensor.YOUR_MEDICATION_concentration
        above: 0
    card:
      type: custom:mushroom-template-card
      entity: sensor.YOUR_MEDICATION_concentration
      primary: Amount in body
      secondary: "{{ states('sensor.YOUR_MEDICATION_concentration') }} mg"
      icon: mdi:chart-bell-curve
      icon_color: purple
      tap_action:
        action: more-info
  - type: conditional
    conditions:
      - condition: state
        entity: sensor.YOUR_MEDICATION_steady_state
        state_not: unknown
    card:
      type: custom:mushroom-template-card
      entity: sensor.YOUR_MEDICATION_steady_state
      primary: Steady State
      secondary: >-
        {% set ss = states('sensor.YOUR_MEDICATION_steady_state') %}
        {% if ss == '0.0' or ss == '0' %}
          Reached ✓
        {% else %}
          {{ ss }} days remaining
        {% endif %}
      icon: mdi:chart-timeline-variant
      icon_color: teal
      tap_action:
        action: more-info
  - type: custom:mushroom-chips-card
    alignment: center
    chips:
      - type: template
        content: "7d Avg: {{ states('sensor.YOUR_MEDICATION_avg_daily_doses_7_days') }}"
        icon: mdi:chart-line
      - type: template
        content: "14d Avg: {{ states('sensor.YOUR_MEDICATION_avg_daily_doses_14_days') }}"
        icon: mdi:chart-line
      - type: template
        content: "30d Avg: {{ states('sensor.YOUR_MEDICATION_avg_daily_doses_30_days') }}"
        icon: mdi:chart-line
      - type: template
        content: "Year Avg: {{ states('sensor.YOUR_MEDICATION_avg_daily_doses_yearly') }}"
        icon: mdi:chart-line
      - type: template
        content: "7d: {{ states('sensor.YOUR_MEDICATION_adherence_7_days') }}%"
        icon: mdi:check-decagram
      - type: template
        content: "14d: {{ states('sensor.YOUR_MEDICATION_adherence_14_days') }}%"
        icon: mdi:check-decagram
      - type: template
        content: "30d: {{ states('sensor.YOUR_MEDICATION_adherence_30_days') }}%"
        icon: mdi:check-decagram
      - type: template
        content: "Total: {{ states('sensor.YOUR_MEDICATION_total') }}"
        icon: mdi:counter
```

---

## ⏰ Smart Reminders (Blueprint)

This repository includes a Blueprint that handles complex reminder loops. It sends an actionable notification to your phone. If you click **Take Now**, it logs the pill natively. If you ignore it, it snoozes and loops.

**To install the Blueprint:**
1. Go to **Settings → Automations → Blueprints**.
2. Click **Import Blueprint** in the bottom right.
3. Paste the URL to the blueprint file in this repository:
   `https://raw.githubusercontent.com/adix992/Home-Assistant-Pill-Logger/main/blueprints/reminder.yaml`
4. Create a new automation using the blueprint, select your phone, and map your Pill Logger entities.

---

*Disclaimer: This integration is for informational and home automation purposes only. It is not a certified medical device. Always follow the advice of your doctor and the instructions on your prescription.*
