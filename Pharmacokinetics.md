# 💊 Pharmacokinetics Reference

[![Buy me a tea](https://img.shields.io/badge/Buy_me_a_tea-☕-FF5E5B?style=flat-square&logo=ko-fi&logoColor=white)](https://ko-fi.com/axildor)

> [← Back to main README](README.md)

This document covers the complete mathematical methodology behind AX Dose Logger's pharmacokinetic models. All calculations are transparent and evidence-based, using standard compartmental frameworks from clinical pharmacokinetics.

For the user-facing overview (which sensors you get, when to enable it), see the [Pharmacokinetics Overview](README.md#pharmacokinetics-overview) in the main README.

---

## Pharmacokinetics Reference

### Instant Release: The Two-Compartment Model

When you take a standard (instant-release) pill, the drug doesn't instantly appear in your bloodstream. It must first be absorbed from the gastrointestinal tract. AX Dose Logger models this as two compartments:

```
┌─────────┐    absorption (kₐ)    ┌─────────┐    elimination (kₑ)    ┌─────┐
│   Gut   │ ───────────────────▶ │  Body   │ ──────────────────────▶ │ Out │
│  (mg)   │                       │  (mg)   │                         │     │
└─────────┘                       └─────────┘                         └─────┘
```

- **Gut compartment**: Drug waiting to be absorbed. Decays exponentially as drug moves into the body.
- **Body compartment**: Drug currently in your system. Increases from absorption, decreases from elimination.

#### IR Parameters

| Parameter | What It Means | Example |
|-----------|--------------|---------|
| **Dose Strength (D)** | Milligrams per pill | 200 mg |
| **Elimination Half-Life (t½)** | Time for the body to eliminate half the drug | 2 hours |
| **Time to Peak Concentration (t_max)** | Hours after taking until the drug amount in the body is highest | 1.5 hours |
| **Bioavailability (F)** | Fraction of the dose that reaches systemic circulation | 87% |
| **Lag Time** | Minutes before the medication begins releasing. During the lag time, the entire dose sits inert (no absorption, no release). After the lag time elapses, normal release kinetics apply. Set to 0 for immediate onset. | 0 min (most drugs) |

#### How the Absorption Rate Is Calculated

The elimination rate constant is derived directly from the half-life:

> **kₑ = ln(2) / t½**

The absorption rate constant **kₐ** cannot be solved in closed form from t_max. Instead, it's found numerically using the standard pharmacokinetic relationship (Rowland & Tozer, 2011):

> **t_max = ln(kₐ / kₑ) / (kₐ − kₑ)**

AX Dose Logger solves this equation using a binary search over kₐ ∈ [0.0001, 20.0] with 50 iterations, which converges to within 0.001% accuracy.

#### The Bateman Equation

For a single dose of strength **D** at time t = 0, the amount of drug in the body at time **t** is given by the **Bateman equation**:

**General case (kₐ ≠ kₑ):**

> C(t) = F × D × kₐ / (kₐ − kₑ) × (e^(−kₑ·t) − e^(−kₐ·t))

**Limiting case (kₐ ≈ kₑ):**

> C(t) = F × D × kₐ × t × e^(−kₐ·t)

The gut compartment decays independently:

> G(t) = D × e^(−kₐ·t)

When a dose is taken while drug from a previous dose is still in the gut, the body compartment receives an additional contribution from the remaining gut mass:

> Body contribution from gut = F × G₀ × kₐ / (kₐ − kₑ) × (e^(−kₑ·t) − e^(−kₐ·t))

#### Immediate Release Mode

When **t_max = 0**, the dose enters the body directly with no absorption phase. This is appropriate for sublingual, IV, or fast-dissolving formulations. The formula simplifies to:

> C(t) = F × D × e^(−kₑ·t)

The gut compartment is bypassed entirely (G = 0 at all times).

### Sustained Release: The Four-Compartment Hybrid Model

For extended-release medications (e.g., Paracetamol (Panadol/Tylenol) ER 665 mg), the drug is released in two phases: an initial burst for quick onset, followed by a sustained release that maintains therapeutic levels. AX Dose Logger models this with four compartments:

```
                    ┌──────────────┐
                    │  IR Gut      │  Immediate-release fraction (F × D × IR%)
                    │  absorbs via kₐ│
                    └──────┬───────┘
                           │  kₐ absorption
                           ▼
┌──────────────┐    ┌──────────────┐    elimination (kₑ)    ┌─────┐
│  SR Matrix   │───▶│  SR Gut      │──────────────────────▶ │ Out │
│  (mg)        │    │  (mg)        │                         │     │
└──────────────┘    └──────┬───────┘                         └─────┘
  zero-order R₀             │  kₐ absorption
  then first-order kᵣ       ▼
                    ┌──────────────┐
                    │  Body        │
                    │  (mg)        │
                    └──────────────┘
```

- **IR Gut**: The immediate-release fraction of the dose, absorbed with rate constant kₐ (same as instant release).
- **SR Matrix**: The sustained-release fraction, released at a constant rate R₀ during the zero-order phase, then exponentially with rate constant kᵣ = ln(2) / release_half_life.
- **SR Gut**: Drug released from the SR matrix, waiting to be absorbed into the body with rate constant kₐ.
- **Body**: Drug currently in your system. Receives contributions from both IR and SR gut compartments, and is eliminated with rate constant kₑ.

#### SR Parameters

| Parameter | What It Means | Example (Paracetamol ER) |
|-----------|--------------|--------------------------|
| **Dose Strength (D)** | Milligrams per pill | 665 mg |
| **Elimination Half-Life (t½)** | Time for the liver to clear half the drug | 2.0 h |
| **Time to Peak Concentration (t_max)** | Hours until the overall formulation peaks | 2.8 h |
| **Immediate Release Time to Peak (IR t_max)** | Hours until the fast instant-release layer peaks | 1.0 h |
| **Bioavailability (F)** | Fraction reaching systemic circulation | 85% |
| **Initial Release (IR%)** | Percentage of the dose released immediately | 31% |
| **Sustained Release Duration (T_dur)** | Duration of the constant-rate (zero-order) release phase — 0 for matrix tablets | 0 h |
| **Release Half-Life** | Time for the polymer matrix sponge to physically dissolve | 3.0 h |

#### Piecewise Analytical Solution

The ER model uses exact analytical solutions for recalculation (on pill taken/undo/reset) and Euler integration for real-time decay updates.

**Phase 1: During zero-order release (0 ≤ t ≤ T)**

The SR matrix releases drug at a constant rate R₀ = (1 − IR%) × D × F / (T + release_half_life × (1 − e^(−kᵣ·T)) / (kᵣ × T)), ensuring the total SR fraction is fully released over the combined zero-order and first-order phases.

During this phase:
- IR gut: G_IR(t) = D_IR × e^(−kₐ·t)
- SR matrix: M(t) = M₀ − R₀ × t
- SR gut: G_SR(t) = R₀ / kₐ × (1 − e^(−kₐ·t)) + contributions from initial conditions
- Body: B(t) = sum of contributions from IR gut, SR gut, and elimination

**Phase 2: After zero-order release ends (t > T)**

The remaining SR matrix mass decays exponentially:
- M(t) = M_T × e^(−kᵣ·(t−T))

where M_T is the matrix mass at the end of Phase 1, and kᵣ = ln(2) / release_half_life.

#### Multi-Dose Superposition

Both the IR and ER models are **linear**, so the total drug amount at any time equals the sum of each individual dose's contribution:

> C_total(t) = Σᵢ Cᵢ(t − tᵢ)

This is **mathematically exact** — AX Dose Logger stores the complete dose history and recalculates from scratch on every update (including the periodic 2-minute decay updates), eliminating floating-point drift entirely. When you undo a dose, the last entry is removed and the entire model is recalculated from the remaining history.

#### Lag Time

For medications with a delayed onset (enteric-coated, colon-targeted), the **Lag Time** parameter specifies how many minutes pass before any drug release begins. During the lag period, the entire dose sits inert — no absorption, no release. After the lag time elapses, normal IR or SR kinetics apply as if the dose had just been taken at `t = dose_time + lag_time`.

Mathematically, for each dose with elapsed time `t` and lag time `L`:

> t_effective = t − L

If `t_effective < 0`, the dose contributes nothing to any compartment. If `t_effective ≥ 0`, all PK calculations use `t_effective` in place of `t`.

### Worked Example: Ibuprofen 200 mg (Instant Release)

**Configuration:** D = 200 mg, t½ = 2 h, t_max = 1.5 h, F = 100%, dosing interval τ = 6 h

**Step 1 — Elimination rate:**
> kₑ = ln(2) / 2 = 0.347 h⁻¹

**Step 2 — Absorption rate (solved numerically):**
> kₐ ≈ 1.15 h⁻¹ (satisfies t_max = ln(1.15/0.347) / (1.15 − 0.347) ≈ 1.5 h)

**Step 3 — Single dose at t = 0:**

At peak (t = 1.5 h):
> C(1.5) = 200 × 1.15/(1.15 − 0.347) × (e^(−0.347×1.5) − e^(−1.15×1.5))
> = 200 × 1.432 × (0.595 − 0.178)
> = 200 × 1.432 × 0.417
> ≈ **119 mg** in the body

Just before the second dose (t = 6 h):
> C(6) = 200 × 1.432 × (e^(−2.08) − e^(−6.9))
> = 200 × 1.432 × (0.125 − 0.001)
> ≈ **35.5 mg** remaining from the first dose

**Step 4 — Second dose at t = 6 h (superposition):**

At the moment of the second dose, the body still holds ~35.5 mg from the first dose. The new 200 mg enters the gut and begins absorbing. The total body amount is the sum of both contributions at every future time point.

**Step 5 — Steady state accumulation factor:**
> R = 1 / (1 − e^(−0.347 × 6)) = 1 / (1 − 0.125) ≈ **1.14**
> C_max_ss = 200 × 1.14 ≈ **228 mg**

This means at steady state, the peak amount in the body reaches approximately 228 mg — only 14% more than a single dose, because ibuprofen's 2-hour half-life allows significant elimination between doses.

### Worked Example: Paracetamol (Panadol/Tylenol) ER 665 mg (Sustained Release)

**Configuration:** D = 665 mg, t½ = 2.0 h, t_max = 2.8 h, IR t_max = 1.0 h, F = 85%, IR% = 31%, T_dur = 0 h, release_half_life = 3.0 h

**Step 1 — Rate constants:**
> kₑ = ln(2) / 2.0 = 0.347 h⁻¹
> kₐ ≈ 0.51 h⁻¹ (solved from t_max = 2.8 h — the overall formulation peak)
> kₐ_IR ≈ 1.15 h⁻¹ (solved from IR t_max = 1.0 h — the fast instant-release layer)
> kᵣ = ln(2) / 3.0 = 0.231 h⁻¹ (the polymer matrix dissolution rate)

**Step 2 — Dose fractions:**
> D_IR = 665 × 0.85 × 0.31 = 175.2 mg (immediate release, bioavailability-adjusted)
> D_SR = 665 × 0.85 × 0.69 = 389.9 mg (sustained release, bioavailability-adjusted)

**Step 3 — Release profile (matrix tablet, no zero-order pump):**

Because T_dur = 0, there is no constant-rate zero-order phase — Panadol is a matrix tablet, not an osmotic pump. The entire SR fraction is released by first-order dissolution of the polymer sponge at rate kᵣ = 0.231 h⁻¹ (half-life 3.0 h). The SR matrix mass decays as:

> M(t) = D_SR × e^(−kᵣ·t)

The released drug enters the SR gut compartment and is absorbed into the body at the slow overall rate kₐ = 0.51 h⁻¹.

**Step 4 — Resulting profile:**

The IR fraction (31% of the dose) peaks quickly at ~1.0 h via the fast kₐ_IR, providing rapid onset. The SR fraction (69%) is gradually liberated as the polymer matrix dissolves over ~3 h half-lives, then absorbed at the slower overall kₐ, producing a broad second peak around t_max = 2.8 h that maintains therapeutic levels over 6–8 hours. The total body amount at any time is the superposition of the IR and SR contributions plus any residual from previous doses.

### Steady State Tracking

> **Availability:** The Steady State sensor is only created for **scheduled medications** (Regular Interval, Time of Day, Cyclic). It is not available for As Needed medications because steady state requires a fixed dosing interval (τ), which PRN medications do not have.

The Steady State sensor calculates how many days remain until you reach 90% of pharmacokinetic steady state, anchored to the **trough** concentration (the clinically correct marker). For sustained-release medications, the effective dose is scaled by bioavailability (F).

**Dosing interval (τ):** the sensor uses the **longest nominal inter-dose gap**, not the average. This ensures the steady-state band covers the lowest trough the schedule can produce:
- **Regular Interval** — `hours_between_doses` (uniform gap).
- **Time of Day** — the largest circular gap between consecutive slots (e.g. 13:00 + 21:00 → 16 h, not the 12 h average).
- **Cyclic** — `(days_off + 1) × 24` (the span from the last ON-day dose to the first ON-day dose of the next cycle; an every-second-day pill → 48 h).

**Accumulation factor:**
> R = 1 / (1 − e^(−kₑ × τ))

**Theoretical peak and trough at steady state:**
> C_max_ss = F × D × R (peak, just after a dose)
> C_min_ss = C_max_ss × e^(−kₑ × τ) (trough, just before the next dose)

**Reached threshold:** the sensor is "at steady state" when the **projected trough** (the concentration just before the next dose, evaluated from the full PK superposition at the next-dose time) is ≥ **90% of C_min_ss**. Anchoring to the trough — and evaluating the real PK model at the next-dose time rather than the instantaneous mass — keeps the sensor stable across the intra-cycle oscillation: once reached, it *stays* reached instead of flipping back near every trough.

The sensor reports one of three cases:

| Current State | Calculation | Result |
|---------------|-------------|--------|
| **Above 110% of C_max_ss** (e.g. after a dosage reduction) | t = ln(C_current / threshold) / kₑ | Days until drug drops to the new trough threshold |
| **Projected trough ≥ 90% of C_min_ss** | — | `0.0` — steady state reached ✓ |
| **Projected trough below 90% of C_min_ss** | remaining = (t₉₀ − t_current) / 24, where t₉₀ = −ln(0.1)/kₑ and t_current = −ln(1−p)/kₑ (p = trough / C_min_ss) | Days until 90% of the trough asymptote is achieved |

**Lateness buffer:** because the threshold is a percentage (90% of the trough asymptote) rather than a flat time, the implied lateness tolerance scales correctly with half-life: `−ln(0.9)/kₑ` ≈ 18 min for a 2 h half-life and ≈ 3.7 h for a 24 h half-life. Short half-life drugs drop fast (brief lateness matters); long half-life drugs are forgiving. A flat 60-minute buffer would scale the wrong way.

**Attributes exposed:** `theoretical_max_mg`, `steady_state_trough_mg`, `threshold_mg`, `current_mass`, `projected_trough_mg`, `current_percentage`, `dosing_interval_hours`, `last_dose_timestamp`. `current_percentage` measures progress toward the *trough* asymptote (the clinically relevant marker), not the peak.

> **Note:** The 90% threshold is the standard clinical convention — steady state is considered achieved after 4–5 half-lives, which corresponds to 93.75%–96.88% accumulation. The sensor uses 90% as a conservative milestone.

### Worked Example: Steady State Calculation

Using the same ibuprofen configuration (t½ = 2 h, τ = 6 h):

**After 1 dose (at peak, t = 1.5 h):**
- Current body amount ≈ 119 mg
- The single-dose projected trough (just before the 2nd dose) is far below the trough threshold → the sensor reports days remaining (not yet reached)

**Time to reach 90% steady state from zero:**
> t₉₀ = −ln(0.1) / kₑ = 2.303 / 0.347 ≈ 6.6 hours ≈ **0.3 days**

In practice, with repeated dosing every 6 hours, steady state is reached within **approximately 8–10 hours** (4–5 half-lives × 2 h = 8–10 h), which the sensor calculates dynamically based on your actual dosing history. Once reached, the projected trough stays ≥ 90% of C_min_ss across every cycle phase, so the sensor remains at `0.0`.

### PK Search Guide

The PK configuration panel asks for several clinical parameters. Search the web for your medication's **Clinical Pharmacology** or **Product Information** sheet to find these values:

**Core parameters:**

* Elimination Half-Life [t1/2]
* Time to Peak Concentration [Tmax]
* Immediate Release Time to Peak [IR Tmax] (Search for the Tmax of the standard/instant version of the drug)
* Bioavailability [%]
* Initial Release [%] *(Note: If your label shows a milligram split, divide the instant-release mg by the total pill mg)*

**Advanced Pharmacokinetics (if applicable):**

* Lag Time (Enteric-Coated/Delayed Release only) [Tlag]
* Sustained Release Duration (Osmotic/OROS pumps only) [Dissolution Time]
* Release Half-Life (Post-Zero-Order Dissolution only)

### Scientific References

- Rowland, M., & Tozer, T.N. (2011). *Clinical Pharmacokinetics and Pharmacodynamics: Concepts and Applications*. Lippincott Williams & Wilkins.
- Gabrielsson, J., & Weiner, D. (2016). *Pharmacokinetic and Pharmacodynamic Data Analysis: Concepts and Applications*. Apotekarsocieteten.

---

*This integration is for informational and home automation purposes only. It is not a certified medical device. Always follow your doctor's advice and the instructions on your prescription.*