# 💊 Home Assistant Pill Logger

A fully local, advanced medication tracking and pharmacokinetics integration for Home Assistant.  
Pill Logger goes far beyond simple counters — it models drug concentration with a two-compartment PK engine, tracks rolling time windows, warns against accidental overdoses, calculates steady-state progress, lets you log subjective effectiveness, and powers actionable mobile reminders.

## ✨ Features

### 🕐 Scheduling
* **Regular Interval** — Track medications taken every N hours (e.g. every 8 hours).
* **Time of Day** — Schedule a daily dose at a fixed time (e.g. 08:30 every morning).
* **As Needed** — PRN tracking with a configurable rolling window (e.g. max 2 pills per 8 hours).
* **Cyclic/Calendar Pattern** — Define on/off cycles (e.g. 5 days on, 2 days off) anchored to a start date, with a per-day dose time.

### 🛡️ Safety
* **Safe Dose Tracking** — Set limits per interval. The integration calculates your rolling window and tells you exactly how many safe doses remain.
* **Smart Overdose Warning** — Dashboard UI dynamically swaps to a red warning button when safe doses reach 0, prompting an "Are you sure?" dialog before allowing an override.

### 🧪 Pharmacokinetics
* **Concentration Sensor** — Models drug concentration (mg) over time using a two-compartment model with configurable strength, half-life, and hours-to-peak. Absorption rate (kₐ) is solved dynamically from time-to-peak.
* **Steady State Sensor** — Calculates days remaining until 90% steady state, with attributes showing theoretical max concentration and current percentage achieved.
* **Strength Sensor** — Displays the configured per-dose strength (mg) for quick reference.

### 📊 Effectiveness Tracking
* **Standard Metrics** — Optionally enable 1–10 sliders for Pain Level, Mood, Nausea Level, and Fatigue Level to log how well the medication is working for each dose.
* **Custom Metrics** — Add your own tracking metrics separated by commas (e.g. "brain fog, joint stiffness") and get a slider for each.

### 📈 Insights
* **Rolling Averages** — Automatically tracks consumption patterns with rolling averages for 7 days, 30 days, and yearly (365 days). Sensors scale calculations from the moment the medication is added or reset.
* **Total Doses** — Cumulative lifetime dose counter.
* **Last Dose** — Timestamp of the most recent dose.

### 💊 Inventory
* **Smart Inventory** — Tracks remaining pills. To refill, double-tap the inventory card, type the new box amount, and it automatically adds to your total and resets the input to 0.
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
| `sensor` | `{name}_safe_doses` | Remaining safe doses in the current window |
| `sensor` | `{name}_concentration` | Current drug concentration (mg) — requires PK fields |
| `sensor` | `{name}_next_dose` | Timestamp of next available dose |
| `sensor` | `{name}_avg_daily_doses_7_days` | 7-day rolling average of daily doses |
| `sensor` | `{name}_avg_daily_doses_30_days` | 30-day rolling average of daily doses |
| `sensor` | `{name}_avg_daily_doses_yearly` | 365-day rolling average of daily doses |
| `sensor` | `{name}_steady_state` | Days remaining to 90% steady state — requires PK fields |
| `sensor` | `{name}_strength` | Configured per-dose strength (mg) |
| `button` | `take_{name}` | Log a dose |
| `button` | `reset_{name}_history` | Wipe dose history (keeps inventory) |
| `number` | `{name}_pills_left` | Current inventory count |
| `number` | `add_{name}_refill` | Refill input (auto-resets to 0 after adding) |
| `number` | `{name}_{metric}_effectiveness` | 1–10 slider per enabled effectiveness metric |

> **PK fields note:** The Concentration and Steady State sensors only produce meaningful values when **Strength** and **Half-Life** are configured (non-zero). If left at 0, they will report `0` / `None`.

---

## ⚙️ Configuration Flows

### Regular Interval
| Field | Description | Default |
|-------|-------------|---------|
| Medication Name | Display name for the device | My Medication |
| Initial Stock | Pills currently in inventory | 30 |
| Hours Between Doses | Minimum interval between doses | 8 |
| Safe Doses | Max doses allowed per interval | 1 |
| Strength (mg) | Per-dose strength for PK calculations | 0 |
| Half-Life (h) | Elimination half-life for PK calculations | 0 |
| Hours to Peak (h) | Time to peak concentration for absorption modeling | 0 |
| Effectiveness Metrics | Toggle Pain, Mood, Nausea, Fatigue + custom | — |

### Time of Day
| Field | Description | Default |
|-------|-------------|---------|
| Medication Name | Display name for the device | My Medication |
| Initial Stock | Pills currently in inventory | 30 |
| Time of Day | Daily dose time (time picker) | 08:00 |
| Safe Doses | Max doses per 24 hours | 1 |
| Strength / Half-Life / Hours to Peak | PK fields (same as above) | 0 |
| Effectiveness Metrics | Same as above | — |

### As Needed (PRN)
| Field | Description | Default |
|-------|-------------|---------|
| Medication Name | Display name for the device | My Medication |
| Initial Stock | Pills currently in inventory | 30 |
| Safe Doses | Max doses in the time window | 2 |
| Time Window (hours) | Rolling window for safe dose calculation | 8 |
| Strength / Half-Life / Hours to Peak | PK fields (same as above) | 0 |
| Effectiveness Metrics | Same as above | — |

### Cyclic/Calendar Pattern
| Field | Description | Default |
|-------|-------------|---------|
| Medication Name | Display name for the device | My Medication |
| Initial Stock | Pills currently in inventory | 30 |
| Days On | Number of active days in the cycle | 5 |
| Days Off | Number of rest days in the cycle | 2 |
| Cycle Anchor Date | Start date of the cycle (calendar picker) | Today |
| Dose Time | Time of day to take on active days (time picker) | 08:00 |
| Safe Doses | Max doses per on-day | 1 |
| Strength / Half-Life / Hours to Peak | PK fields (same as above) | 0 |
| Effectiveness Metrics | Same as above | — |

---

## 🔧 Reconfiguring After Setup

Click **Configure** on the integration entry to change any of the following without recreating the medication:

| Tracking Type | Editable Fields |
|---------------|-----------------|
| Regular Interval | Hours Between Doses, Safe Doses, Strength, Half-Life, Hours to Peak, Effectiveness Metrics |
| Time of Day | Time of Day, Safe Doses, Strength, Half-Life, Hours to Peak, Effectiveness Metrics |
| As Needed | Time Window (hours), Safe Doses, Strength, Half-Life, Hours to Peak, Effectiveness Metrics |
| Cyclic/Calendar Pattern | Days On, Days Off, Cycle Anchor Date, Dose Time, Safe Doses, Strength, Half-Life, Hours to Peak, Effectiveness Metrics |

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
3. Follow the multi-step setup to define your medication (tracking type, dosages, PK parameters, effectiveness metrics, and current stock).
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
      primary: Concentration
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
        content: "30d Avg: {{ states('sensor.YOUR_MEDICATION_avg_daily_doses_30_days') }}"
        icon: mdi:chart-line
      - type: template
        content: "Year Avg: {{ states('sensor.YOUR_MEDICATION_avg_daily_doses_yearly') }}"
        icon: mdi:chart-line
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
