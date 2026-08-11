[![GitHub Release](https://img.shields.io/github/v/release/Axildor/AX-Dose-Logger?style=flat-square)](https://github.com/Axildor/AX-Dose-Logger/releases)
[![HACS Status](https://img.shields.io/badge/HACS-Custom-orange.svg?style=flat-square)](https://github.com/hacs/integration)
[![Lint Status](https://img.shields.io/github/actions/workflow/status/Axildor/AX-Dose-Logger/lint.yml?branch=main&label=Lint&style=flat-square)](https://github.com/Axildor/AX-Dose-Logger/actions/workflows/lint.yml)
[![Validate Status](https://img.shields.io/github/actions/workflow/status/Axildor/AX-Dose-Logger/validate.yml?branch=main&label=Validate&style=flat-square)](https://github.com/Axildor/AX-Dose-Logger/actions/workflows/validate.yml)
[![Buy me a tea](https://img.shields.io/badge/Buy_me_a_tea-☕-FF5E5B?style=flat-square&logo=ko-fi&logoColor=white)](https://ko-fi.com/axildor)

# 💊 AX Dose Logger

A fully local Home Assistant integration for tracking **medications and drinks** — when you took them, when your next dose is, and whether it's safe to take another. It runs entirely on your instance with no cloud dependency.

For **medications**, it models how much drug is in your body over time using pharmacokinetic engines for both instant-release and sustained-release formulations, tracks how well your meds are working with custom sliders, and sends mobile reminders when it's time to take a dose.

For **drinks** (caffeine & alcohol), it tracks each drink granularly (one device per drink) and aggregates the doses into global Master Trackers that draw decay curves using integration-level metabolic constants.

> ⚠️ **Medical disclaimer:** This integration is for informational and home automation purposes only. It is not a certified medical device. Always follow your doctor's advice and the instructions on your prescription.


---

## 🃏 Companion Card

AX Dose Logger was built **in tandem** with the dedicated [**AX Dose Logger Card**](https://github.com/Axildor/AX-Dose-Logger-Card) — a Lovelace card that surfaces everything the integration produces with no template YAML and no Mushroom/Card-Mod dependencies. The two were programmed together and are designed to work as a pair.

That said, the integration is fully usable on its own. Every feature (sensors, buttons, services, events) is exposed through standard Home Assistant entities and is available to any dashboard, automation, or template you build yourself.

**Install the card:** `https://github.com/Axildor/AX-Dose-Logger-Card` → HACS → Custom Repositories → **Dashboard** category. See [Dashboard Card](#dashboard-card) below.

---

## Table of Contents

- [Companion Card](#-companion-card)
- [Key Features](#key-features)
- [Getting Started](#getting-started)
- [Medications](#medications)
  - [Tracking Modes](#tracking-modes)
  - [Staying Safe](#staying-safe)
  - [Pharmacokinetics Overview](#pharmacokinetics-overview)
  - [Tracking How Well It Works](#tracking-how-well-it-works)
  - [Adherence & Averages](#adherence--averages)
  - [Inventory & Undo](#inventory--undo)
- [Drinks (Caffeine & Alcohol)](#drinks-caffeine--alcohol)
  - [How Drinks Work](#how-drinks-work)
  - [Configuring a Drink](#configuring-a-drink)
  - [Drink Services & Events](#drink-services--events)
  - [Master Tracker Sensors](#master-tracker-sensors)
  - [Sleep Disruption Bands](#sleep-disruption-bands)
- [Dashboard Card](#dashboard-card)
- [Reminders](#reminders)
- [Building Automations](#building-automations)
- [☕ Support the Project](#-support-the-project)

### Dive deeper

The main README covers installation and everyday use. For deeper material, the docs split into three companion files:

- 🧮 [Pharmacokinetics.md](Pharmacokinetics.md) — full mathematical methodology (IR/ER models, worked examples, steady state, PK search guide, scientific references)
- 🔧 [Advanced-Users.md](Advanced-Users.md) — configuration reference, entity states & attributes, and full automation examples for power users hand-rolling custom templates
- 🛠️ [CONTRIBUTING.md](CONTRIBUTING.md) — project structure, architecture diagram, signal reference, and development setup for contributors

---

## Key Features

**Medications**
- Four tracking modes: fixed interval, time of day, as-needed (PRN), and cyclic on/off patterns
- Rolling pill-limit window that prevents accidental overdose (each pill expires individually)
- Pharmacokinetic modeling of drug amount in your body — instant-release (Bateman) and sustained-release (4-compartment hybrid) engines
- Steady-state tracking for scheduled medications
- 24-hour intake window with optional daily dose limit
- Adherence percentages and rolling dose averages (7/14/30/365 days)
- Daily-locked 0–10 symptom sliders (Pain, Mood, Nausea, Fatigue + custom)
- Smart inventory with refill dialog and estimated days-left sensor
- Undo last dose (reverts every sensor + the PK model)
- Calendar entity for expected dose times
- Ready-made reminder blueprint with Take / Skip / Snooze actions

**Drinks (Caffeine & Alcohol)**
- One granular device per drink (e.g. "Morning Espresso", "Evening Beer")
- Global Master Trackers draw the decay curve for each substance (caffeine first-order, alcohol zero-order)
- Sleep Disruption sensor (None / Low / Moderate / High) — how much the current load disrupts sleep
- Predictive "Low" timestamp + hours-until countdown sensors
- Per-drink cooldown window with override always available
- 24-hour intake window with optional daily limit (FDA caffeine default 400 mg)
- Stock counter, add-stock input, and days-left estimation

---

## Getting Started

1. **Install the integration** — In HACS, go to ⋮ → Custom Repositories, paste this repository URL, choose **Integration** as the category, then download and restart Home Assistant.
2. **Add a medication or drink** — Head to Settings → Devices & Services → Add Integration and search for **AX Dose Logger**. The config flow walks you through it in four steps (medications) or three steps (drinks).

<!-- SCREENSHOT: The 4-step AX Dose Logger config flow — capture step 1 (name + tracking type + release type) or a composite of all 4 steps -->
<img width="833" height="441" alt="Screenshot 2026-08-11 180542" src="https://github.com/user-attachments/assets/92be2888-4ecb-4ed0-9675-07ee1487083e" />


3. **Add the card to your dashboard** — Install the dedicated [AX Dose Logger Card](https://github.com/Axildor/AX-Dose-Logger-Card) and add it via the visual editor. No template YAML required. *(Optional — the integration works on its own.)*

---

## Medications

### Tracking Modes

AX Dose Logger supports four ways to track a medication, depending on how you take it:

| Mode | When to Use It | What Happens |
|------|---------------|--------------|
| **Regular Interval** | You take it every N hours (e.g. every 8 hours) | Schedules doses at fixed intervals from midnight. Shows a countdown to your next dose. |
| **Time of Day** | You take it at the same time(s) each day (e.g. 08:30 every morning, or 13:00 + 21:00 for twice daily) | One or more fixed clock times per day. A dose taken late counts as the dose for the slot it's closest to (lateness extends until the next scheduled slot), and an uncovered missed slot keeps the Overdue sensor counting across midnight until you take it. The calendar entity shows daily events. |
| **As Needed (PRN)** | You take it when you need it, but there's a limit (e.g. max 2 in 8 hours) | No fixed schedule — you log doses as you take them. The pill limit enforces a rolling window. |
| **Cyclic / Calendar Pattern** | You take it on a cycle — some days on, some days off (e.g. 5 days on, 2 days off) | Doses only happen on ON days at the time you set. The calendar entity only shows events on ON days. |

### Staying Safe

Accidentally taking too much is easy to do, especially with medications that have a wide dosing window. AX Dose Logger helps prevent that:

- **Pill Limit Tracking** — You set how many pills are safe within a rolling time window (e.g. max 3 pills in 24 hours). Each pill expires from the window individually, so the limit recovers one at a time. On Cyclic OFF days, the limit drops to 0 automatically.
- **Overdose Warning** — When the pill limit hits 0, the Take button on the dedicated AX Dose Logger Card turns red and asks you to confirm before logging.

<!-- SCREENSHOT: Daily pane with pill limit at 0 — Take button red with the confirmation dialog visible -->
<img width="726" height="321" alt="Screenshot 2026-08-11 181202" src="https://github.com/user-attachments/assets/ae0b572f-840d-40ef-97dd-77fe38120f85" />


- **Next Dose Countdown** — The Next Dose sensor tells you exactly when your next scheduled dose is, so you can show live countdowns like "in 2 hours" or "Available now". For scheduled medications (Time of Day, Cyclic), the next dose always reflects your prescribed clock time — taking a dose late does not drift the schedule. The separate **Pills Safe to Take** sensor tells you whether it's actually safe to take now.

### Pharmacokinetics Overview

If you want to understand what's happening in your body between doses, AX Dose Logger can optionally model the **amount of medication in your system over time**. When enabled, it creates sensors based on your tracking type:

- **Amount in Body** — Current drug amount (mg), updated every 2 minutes, accounting for absorption and elimination. Available for all tracking types.
- **Amount in Last 24h** — Sliding 24-hour window showing the total dose strength consumed in the last 24 hours. This is **intake** (how much you swallowed), not body load. Set an optional **24h Strength Limit** on the Pharmacokinetics screen and the `remaining` attribute exposes the headroom left — for automations and the card to warn before the next dose pushes you over a daily cap. Available for all tracking types.
- **Steady State** — Days remaining until you reach 90% steady state, anchored to the **trough** concentration (the clinically correct marker) and the **longest** dosing gap. Once reached, the sensor stays reached across every cycle phase. Includes the theoretical peak/trough, the 90% threshold, the projected next-trough, and your current percentage. **Scheduled medications only** (Regular Interval, Time of Day, Cyclic) — not available for As Needed, since steady state requires a fixed dosing interval.

You choose a **Release Type** when adding a medication:

- **Instant Release** — Three parameters: Dose Strength (mg), Elimination Half-Life (h), and Time to Peak Concentration (h; set to 0 for immediate-release). Uses a two-compartment (Bateman) model. An optional Lag Time (min) can model delayed-release formulations.
- **Sustained Release** — Adds Bioavailability (%), Initial Release (%), Sustained Release Duration (h), Release Half-Life (h), and Lag Time (min) to model hybrid extended-release formulations with both fast-acting and slow-release components.

Leave all PK values at 0 to disable concentration tracking. The Amount in Body sensor reports `unknown` (shown as N/A) when Elimination Half-Life is left at 0 — a concentration without elimination has no meaningful value, so the sensor no longer shows an infinitely accumulating number.

> **Note:** The sensor reports **drug amount in the body (mg)**, not blood concentration. Converting to concentration would require the volume of distribution, which varies from person to person.

➡️ **Dive deeper:** for formulas, worked examples, the steady-state derivation, and the PK search guide, see [Pharmacokinetics.md](Pharmacokinetics.md).

### Tracking How Well It Works

Not sure if your medication is actually helping? AX Dose Logger can add 0–10 daily-locked sliders so you can rate how you feel each day:

- **Standard symptoms**: Pain, Mood, Nausea, Fatigue
- **Custom symptoms**: Add your own (e.g. "brain fog", "joint stiffness") — each one gets its own slider
- **Daily-locked**: Each slider can only be set once per calendar day. If you try to change it, you'll get a warning with an option to override. Sliders reset to **unknown** at midnight — unset days are not imputed to 0 or any default, following FDA Patient-Reported Outcome (PRO) guidance that missing data must remain missing.

### Adherence & Averages

AX Dose Logger gives you several ways to look at your dosing history:

- **Adherence Percentage** — Four rolling sensors (7, 14, 30, 365 days) showing what percentage of scheduled doses you took on time. A dose counts as "on time" if it falls within ±grace period of the expected slot. Cyclic mode only counts ON days. As Needed medications report `Unavailable` since adherence doesn't apply without a schedule.
- **Rolling Averages** — Day-level dose coverage over 7, 14, 30, and 365 days (the fraction of scheduled days in the window on which at least one dose was taken, 0.0–1.0). Windows are anchored to your first recorded dose, so setting up a medication before you start taking it doesn't penalize the averages. A late-but-taken dose does not lower the average. Cyclic mode only counts ON days. Timing quality (on-time vs late) is reported separately by the Adherence Percentage sensors.
- **Total Doses** — Cumulative lifetime dose counter.
- **Last Dose** — Timestamp of your most recent dose.
- **Days Since First Dose** — Integer days elapsed since your first recorded dose.

### Inventory & Undo

- **Smart Inventory** — Tracks how many pills you have left. Double-tap the inventory tile on the AX Dose Logger Card to open the refill dialog, enter the new box amount, and it automatically adds to your total.

<!-- SCREENSHOT: Double-tap on the inventory tile showing the refill input dialog -->
<img width="790" height="315" alt="Screenshot 2026-08-11 181245" src="https://github.com/user-attachments/assets/9e4863ce-ebad-42f6-9081-75d1708e78f5" />


- **Days Left** — How many days your current inventory lasts. Scheduled medications divide Pills Left by the configured doses/day. As-Needed medications divide by the 7-day average doses/day (shows `unknown` until enough history exists).
- **Undo Last Dose** — Pressed Take by accident? The Undo button reverts the most recent dose across all sensors, counters, and the PK model — restoring inventory, removing the timestamp, and recalculating the concentration curve from dose history.

---

## Drinks (Caffeine & Alcohol)

In addition to medications, AX Dose Logger can track caffeinated and alcoholic drinks. The first config-flow step asks you to choose a **Device Category**:

- **Medication** — the legacy flow (scheduled pills, PK concentration, adherence, etc.)
- **Drink** — track a caffeine or alcohol drink with a granular device

A global **Drink Settings** entry holds the metabolic constants and the two Master Tracker devices. It is **auto-created the first time you add a drink** — never a manual choice. Edit its global constants later via the **Configure** button on the auto-created Drink Settings entry.

### How Drinks Work

Each configured drink becomes its own **Granular Drink Device** (e.g. "Morning Espresso", "Evening Beer") with a set of control and configuration entities:

| Entity | Type | Purpose |
|--------|------|---------|
| **Log Drink** | Button | Records a drink and forwards `dose_strength` + `drinking_duration` to the matching substance's Master Tracker. **Pressing this is what activates the Master Tracker PK engines.** |
| **Undo Drink** | Button | Reverts the last drink + its master contribution |
| **Reset History** | Button (config) | Clears the drink's local history + master contribution |
| **Inventory** | Number | Counts down by 1 per Log Drink press (using your configured unit, e.g. Cups/Cans/Bottles) |
| **Add Stock** | Number | Disposable input to refill the Inventory counter |
| **Total** | Sensor | Cumulative drink count |
| **Last Drink** | Sensor (timestamp) | Most recent drink timestamp |
| **Daily Average** | Sensors | 7/14/30/365-day daily-average sensors |
| **Drinks Available** | Sensor | Cooldown state (when a cooldown window is configured) |
| **Est. Days Left** | Sensor | Days inventory lasts at the current rate |

The Lovelace card's **Log Drink popup** shows a predictive **"Low: hh:mm"** line under each drink name — the wall-clock time the body-mass is expected to drop into the *Low* sleep band *if that drink were logged now*. "Low: —" means the drink would not lift body-mass above the Low band (a safe drink). The prediction is fetched live from the backend and never mutates real state.

#### Cooldown (Drinks Available Sensor)

When a drink has a `cooldown_window > 0`, a **Drinks Available** sensor (`sensor.<drink>_drinks_available`) is created on the granular drink device, mirroring the medicine **Pills Safe To Take** sensor's contract so the Lovelace card consumes it identically:

| State | Meaning |
| --- | --- |
| `1` | A drink is available (outside the cooldown window, or no history yet) |
| `0` | Cooldown active — limit reached for this window |

| Attribute | Type | Description |
| --- | --- | --- |
| `cooldown_ends_at` | datetime (ISO) \| null | When the current cooldown window expires. The card renders the "Next XXm" countdown from this. |
| `last_dose_time` | datetime (ISO) \| null | Timestamp of the most recent drink. The card renders the "Last XXm" display from this. |
| `cooldown_window_hours` | float | The configured cooldown window in hours. |
| `within_cooldown` | bool | Raw boolean mirror of the coordinator's lockout check, for templates that prefer a boolean. |

> **Override always available.** The cooldown is a *soft* warning, never a hard backend block. The **Log Drink** button and the `ax_dose_logger.log_drink` service always record the drink, so a user can override the lockout directly from the HA UI or an automation at any time. The card soft-disables the button and shows a "Last XXm * Next XXm" countdown when `native_value == 0`, with an explicit override affordance.

### Configuring a Drink

1. **Add a Device** → choose **Drink** as the category.
2. **Drink Setup** — name, drink type (Caffeine/Alcohol), unit of measurement (e.g. Cups, Cans, Bottles), and an initial stock count.
3. **Cooldown Timer** — optional lockout window in hours (0 = disabled; minimum is always 1). See the cooldown note above.
4. **Drink Details** —
   - Caffeine: `caffeine_mg` + `drinking_duration` (typical time to finish, minutes).
   - Alcohol: `volume_ml` + `abv_percent` + `drinking_duration`. The ethanol mass is calculated automatically: `grams = volume_ml × (abv_percent / 100) × 0.789` (Widmark formula). `bioavailability` is hardcoded to 100 for all drinks.

Edit the global metabolic constants via **Configure** on the Drink Settings entry:

| Constant | Default | Unit |
|----------|---------|------|
| Caffeine Half-Life | 5.0 | hours |
| Caffeine Time to Peak | 0.75 | hours |
| Alcohol Elimination Rate | 8.0 | g/h |

### Drink Services & Events

In addition to the **Log Drink** button, three services are available for automations:

- `ax_dose_logger.log_drink` — log a drink (entry_id + optional timestamp)
- `ax_dose_logger.undo_drink` — revert the last drink + its master contribution
- `ax_dose_logger.reset_drink` — clear a drink's local history + master contribution

The `ax_dose_logger_drink_taken` bus event fires on every log with `{entry_id, drink_type, dose_strength, drink_name}` for automations (e.g. "if caffeine in body > 200mg, dim lights").

> **Note:** Master Tracker devices expose a `drink_master: true` + `substance` attribute so the AX Dose Logger Card can identify them and render the dedicated Drinks card. Granular drink devices expose a `device_type: "drink"` + `substance` attribute so the card can group drinks by substance.

### Master Tracker Sensors

When you press **Log Drink**, the dose is forwarded to the matching **Master Tracker** virtual device, which draws the global decay curve. There are two Master Trackers:

| Master Tracker | Substance | PK Model | Amount in Body sensor |
|----------------|-----------|----------|-----------------------|
| **Caffeine Tracker** | Caffeine (mg) | Discretized uniform-absorption: each drink is split into mini-boluses spread across its `drinking_duration`, each absorbed via the IR Bateman equation with the global half-life and tmax. Linear PK → exact superposition across all caffeine drinks. | `sensor.total_caffeine_in_body` (displayed as **Amount in Body**) |
| **Alcohol Tracker** | Alcohol (g ethanol) | Zero-order (Michaelis-Menten saturated) elimination at a configurable grams-per-hour rate. Doses add instantly; elimination advances on every tick. | `sensor.total_alcohol_in_body` (displayed as **Amount in Body**) |

> Entity IDs `sensor.total_caffeine_in_body` / `sensor.total_alcohol_in_body` are preserved for backward compatibility.

Each Master Tracker hosts the following sensors:

| Sensor | Entity ID | What It Shows |
|--------|-----------|---------------|
| **Amount in Body** | `sensor.total_<substance>_in_body` | Current body-mass (mg caffeine / g alcohol), updated every 1-min decay tick. |
| **Sleep Disruption** | `sensor.sleep_disruption` | Categorical readout (`None` / `Low` / `Moderate` / `High`) — see [bands below](#sleep-disruption-bands). Recomputed on every coordinator push. |
| **Low - Timestamp** | `sensor.estimated_low_time` | Wall-clock time the body-mass will decay into the *Low* band. Displayed as `HH:MM` (24-hour) by the card; HA's more-info still shows the full datetime (keeps `TIMESTAMP` device class). `None` once already in the Low band or below. |
| **Low - Hours Until** | `sensor.low_hours_until` | Numeric `DURATION` (hours) countdown to the same Low-band milestone. `None` once already in Low or below. |
| **Amount in Last 24h** | `sensor.amount_in_last_24h` | Sliding 24-hour window — total strength of that substance consumed in the past 24 hours (mg caffeine / g alcohol). Aggregates **every** logged drink of that substance. |
| **Last Drink** | `sensor.<substance>_last_drink` | Timestamp of the most recent drink of that substance across all granular drink devices. |
| **Daily Average** | rolling avg sensors | 7/14/30/365-day daily-average sensors aggregating every drink of that substance. |

> **No "Days Left" sensor on the Master Tracker.** The aggregate device has no single inventory of its own, so a days-left reading would be misleading. Each granular drink device has its own **Est. Days Left** sensor (see the Drinks section), and the Inventory panel surfaces it per drink.

**Low - Timestamp** carries `estimated_none_time` (the sleep-safe moment when body-mass enters the None band) as an attribute. **Low - Hours Until** carries `estimated_none_hours` (the longer-horizon countdown to the sleep-safe None band) + `low_threshold` + `low_threshold_unit` as attributes. Both become `None` once the body-mass is in the None band.

**Decay formulas** (stated once, used by both Low sensors and the Sleep Disruption `minutes_until_next_band` attribute):
- **Caffeine** (first-order): `t = ln(M / threshold) ÷ ke`, where `ke = ln(2) / half_life`.
- **Alcohol** (zero-order linear): `t = (M − threshold) ÷ rate`.

The Sleep Disruption sensor's extra attributes expose the raw `body_mass` + unit, the `next_band` it will drop into, and `minutes_until_next_band` (estimated decay time to the next-lower band).

#### Amount in Last 24h — Daily Limits

Per-substance daily limits are configurable in **Drink Settings** (Configure):

| Substance | Unit | Default Limit | Source |
| --- | --- | --- | --- |
| **Caffeine** | mg | **400 mg** (FDA — healthy adults) | User-overridable. Lower for lighter body mass, pregnancy, or caffeine sensitivity. `0` = no limit. |
| **Alcohol** | g ethanol | **0 g** (no FDA limit) | User-overridable in grams ethanol. US Dietary Guidelines ≈ 14 g/day women, 28 g/day men (1 standard drink ≈ 14 g). `0` = no limit. |

When a limit is set (> 0), the sensor exposes a `remaining` attribute (`daily_limit - amount`) so automations and the card can warn "X mg of 400 mg — Y left" before the next drink pushes intake over the cap.

> **Intake vs. body load:** *Amount in Last 24h* tracks **how much you swallowed** in the rolling window — the correct value for comparing against FDA/Dietary Guidelines daily limits (which are stated as total daily *intake*, not plasma concentration). For the **current active amount in your body** (accounting for absorption and elimination), use the Master Tracker's *Amount in Body* sensor instead. These answer different questions: a dose taken 20 hours ago has mostly cleared from your body but still counts toward your 24h intake budget.

### Sleep Disruption Bands

The Sleep Disruption sensor classifies the current body-mass load into a categorical band indicating how much it is likely to disrupt sleep. The state is a bare label (no unit suffix); the threshold ranges are documented below. The band is recomputed on every coordinator push (dose event or 1-min decay tick) so it tracks clearance in real time.

#### Caffeine — Sleep Disruption Bands

| State | Threshold | Biological Impact at Bedtime |
| --- | --- | --- |
| **None** | `0 - 10 mg` | **Negligible impact.** The liver has effectively cleared the drug. Adenosine receptor binding is minimal. Normal sleep architecture and natural melatonin production occur. |
| **Low** | `11 - 30 mg` | **Minor architectural shift.** Roughly equivalent to the residual trace of a cup of green tea. Sleep latency is unaffected, and deep sleep metrics on wearables remain largely stable. Minor delays in the first REM cycle may occur. |
| **Moderate** | `31 - 60 mg` | **Hidden disruption.** Roughly equivalent to a residual espresso shot. Users will likely still fall asleep easily, but Slow-Wave Sleep is measurably suppressed. Expect reduced deep sleep duration and an elevated resting heart rate during the first half of the night. |
| **High** | `61+ mg` | **Severe disruption.** Heavy adenosine A2A receptor blockade. Increases sleep latency (tossing and turning), multiplies unconscious micro-arousals, and chemically delays the circadian melatonin trigger. |

> **Note on "Immunity":** If you drink coffee late in the day and feel "immune" to it because you still get 8 hours of sleep, your central nervous system is still being robbed of its primary phase for physical and cognitive regeneration. You are unconscious, but you are not getting deep sleep.

#### Alcohol — Sleep Disruption Bands

| State | Threshold | Biological Impact at Bedtime |
| --- | --- | --- |
| **None** | `0 g` | **Clean architecture.** The liver has cleared all ethanol. Normal sleep cycling, baseline resting heart rate, and natural REM duration occur. |
| **Low** | `1 - 10 g` | **Minor rebound.** (Less than 1 standard drink remaining). The ethanol will clear within the first 1-2 hours of sleep. Minor suppression of the first REM cycle, with a slight, brief elevation in resting heart rate. |
| **Moderate** | `11 - 30 g` | **Moderate architectural stress.** (Roughly 1 to 2.5 standard drinks remaining). Noticeable REM suppression. The glutamate rebound will occur during the middle of the night, causing restless sleep, potential temperature dysregulation (sweating), and a measurable drop in Heart Rate Variability (HRV) on fitness trackers. |
| **High** | `31+ g` | **Severe disruption.** Heavy REM suppression. The body is dedicating massive metabolic resources to clearance. Expect frequent mid-night awakenings, a spiked resting heart rate that stays elevated for hours, diuretic effects (waking up for the bathroom), and severe REM rebound (vivid, stressful dreams) if sleep is extended. |

> **Note on "The Nightcap":** Using alcohol to fall asleep faster is a biological trap. You are trading sleep latency (falling asleep quickly) for sleep architecture (restorative sleep). Wearable data will almost always show a destroyed Heart Rate Variability (HRV) and elevated resting heart rate for hours after a late-night drink, leaving you physically exhausted the next day regardless of hours spent in bed.

---

## Dashboard Card

AX Dose Logger has a dedicated Lovelace card that surfaces everything the integration produces — no template YAML, no Mushroom/Card-Mod dependencies. It's a separate repository, installed via HACS as a **Dashboard** card.

**Install:** `https://github.com/Axildor/AX-Dose-Logger-Card` (HACS → Custom Repositories → Dashboard category)

Once installed, add it to your dashboard via the visual editor and pick your medication or drink device from the dropdown. The card has four panes, selectable via tabs at the bottom:

| Pane | What It Shows |
|------|---------------|
| **📅 Daily** | Take Pill / Log Drink button with next-dose countdown, pills/drinks safe indicator, last dose timestamp, inventory count (double-tap to refill), custom chips for any related entities |
| **📊 Graphs** | Bar graph of daily doses with selectable timescales (14D, 30D, 60D) + amount-in-body line graph with selectable timeframes (12H, 48H, 7D, 14D, 30D) |
| **📈 Stats** | Rolling averages (7/14/30/365 days), adherence percentages (7/14/30/365 days), total doses, days since first dose |
| **🔧 Tools** | Reset adherence percentage, Mark last missed dose as taken, Reset dose history, Undo last dose |

**Daily pane** — medication name, Take Pill button with next-dose countdown, pills safe to take, last dose, inventory count, custom chips:

<!-- SCREENSHOT: Card showing the Daily pane — medication name, Take Pill button with next-dose countdown, pills safe to take, last dose, inventory count, custom chips -->
![Daily pane](screenshots/daily-pane.png)

**Graphs pane** — daily-dose bar graph with timescale selector + amount-in-body line graph with timeframe selector:

<!-- SCREENSHOT: Card showing the Graphs pane — daily-dose bar graph with timescale selector + amount-in-body line graph with timeframe selector -->
![Graphs pane](screenshots/graphs-pane.png)

**Stats pane** — rolling average boxes (7/14/30/365 days), adherence percentage boxes, total doses, days since first dose:

<!-- SCREENSHOT: Card showing the Stats pane — rolling average boxes (7/14/30/365 days), adherence percentage boxes, total doses, days since first dose -->
![Stats pane](screenshots/stats-pane.png)

**Tools pane** — Reset Adherence %, Mark Last Adherence Taken, Skip Dose, Reset History, Undo Last Dose buttons:

<!-- SCREENSHOT: Card showing the Tools pane — Reset Adherence %, Mark Last Adherence Taken, Skip Dose, Reset History, Undo Last Dose buttons -->
![Tools pane](screenshots/tools-pane.png)

> **Skip Dose** clears the overdue alarm and advances the next-dose schedule for a deliberately-skipped scheduled dose (e.g. prescriber-directed "skip if dizzy", a taper step, or a drug holiday) **without logging a dose** — Amount in Body, pill inventory, total doses, and last dose are all untouched, so the pharmacokinetic graph stays clean. Adherence stays penalized (you genuinely did not take it); for a prescriber-directed skip, press **Mark Last Adherence Taken** afterwards to credit the slot. Skip Dose is only available for scheduled medications (Time of Day, Regular Interval, Cyclic); As Needed meds have no schedule to skip.

For full card configuration options (color schemes, column layouts, chip customization, graph toggles), see the [AX Dose Logger Card repository](https://github.com/Axildor/AX-Dose-Logger-Card#readme).

---

## Reminders

There's a ready-made Blueprint you can import for push notifications with Take, Skip, and Snooze actions:

1. Go to Settings → Automations → Blueprints → Import Blueprint
2. Paste: `https://raw.githubusercontent.com/Axildor/AX-Dose-Logger/main/blueprints/reminder.yaml`
3. Create a new automation from the blueprint, pick your phone, and map your AX Dose Logger entities.

> **Safety guard**: The blueprint has an optional "Pills Safe to Take Sensor" input. When mapped, the notification's **Taken** action will not auto-log a dose if you're at the pill limit — instead it sends a warning telling you to open the AX Dose Logger card to override. This keeps the notification from bypassing the rolling-window overdose protection.

---

## Building Automations

Each medication and drink shows up as a **Device** in Home Assistant, exposing sensors, buttons, numbers, a calendar, and event-bus events you can use in any automation. The ready-made reminder blueprint above is the quickest starting point for push notifications.

For custom automations — the full sensor/entity reference table, button and number entities, the calendar entity, the event-bus reference, and copy-pasteable YAML automation examples — see [Advanced Users](Advanced-Users.md).

---

## ☕ Support the Project

I'm a solo developer on disability building Home Assistant integrations and UI components independently. Your support keeps servers online, API quotas funded, and the black tea brewing while I debug TypeScript.

If AX Dose Logger is useful to you, there's no obligation — but any support is highly appreciated.

[![Buy me a tea](https://img.shields.io/badge/Buy_me_a_tea-on_Ko--fi-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/axildor)

---

## Contributing

Contributions, bug reports, and feature requests are welcome. For the contribution workflow, project structure, architecture diagram, signal reference, and development setup, see [CONTRIBUTING.md](CONTRIBUTING.md).

---

*This integration is for informational and home automation purposes only. It is not a certified medical device. Always follow your doctor's advice and the instructions on your prescription.*
