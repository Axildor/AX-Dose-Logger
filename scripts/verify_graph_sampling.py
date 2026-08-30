#!/usr/bin/env python3
"""Sanity-check the graph sampling helpers (pure math, no HA stubbing)."""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath("custom_components"))
from ax_dose_logger.pk_model import PKModel, PKParams

# Mirror AxDoseLoggerCoordinator.sample_amount_curve's core loop.
PARAMS = PKParams(
    release_type="Instant Release",
    strength=100.0,
    half_life=4.0,
    hours_to_peak=1.0,
    bioavailability=100.0,
    ir_fraction=100.0,
    zero_order_duration=0.0,
    release_half_life=0.0,
    lag_time=0.0,
    ir_hours_to_peak=1.0,
)

DOSE = datetime(2026, 8, 29, 18, 0, 0)
END = datetime(2026, 8, 30, 12, 0, 0)
START = END - timedelta(hours=12)
POINTS = 240

# Dose 6h before the window start (peak at 19:00, well before START) —
# inside the 10-half-life relevance horizon, so the whole window is decay.
history = [(DOSE, 100.0)]

samples = []
span = (END - START).total_seconds()
n = max(2, min(int(POINTS), 400))
for i in range(n):
    t = START + timedelta(seconds=span * i / (n - 1))
    samples.append((t, PKModel.compute(PARAMS, history, t).body))

# 1. Correct sample count.
assert len(samples) == 240, f"expected 240 samples, got {len(samples)}"

# 2. Monotonic decay after the peak (dose at 06:00, t_max 1h -> peak 07:00;
#    window starts 00:00, so the whole window is post-peak decay).
values = [v for _, v in samples]
assert all(values[i] >= values[i + 1] - 1e-9 for i in range(len(values) - 1)), (
    "curve not monotonically decaying post-peak"
)

# 3. Endpoint parity: last sample == direct compute at END.
direct = PKModel.compute(PARAMS, history, END).body
assert abs(values[-1] - direct) < 1e-9, "last sample != direct compute"

# 4. Even spacing (float tolerance — timedelta microsecond rounding can
#    produce sub-microsecond jitter between consecutive gaps).
gaps = [(samples[i + 1][0] - samples[i][0]).total_seconds() for i in range(len(samples) - 1)]
assert max(gaps) - min(gaps) < 1e-6, f"uneven spacing: {max(gaps) - min(gaps)}"

# 5. Relevance pruning: a dose 100 half-lives old is dropped -> empty series.
old_history = [(DOSE - timedelta(hours=400), 100.0)]
horizon = START - timedelta(hours=PARAMS.half_life * 10)
relevant = [(ts, s) for ts, s in old_history if ts >= horizon]
assert relevant == [], "old dose not pruned"

# 6. Alcohol segment-wise forward simulation (mirrors
#    DrinkMasterCoordinator.sample_body_mass_curve's alcohol branch).
# Seeding at 0 at the oldest dose is exact (pre-retention mass is PK-dead),
# so the simulated curve IS the truth — no drift re-anchor.
rate = 1.0  # g/h
doses = [(END - timedelta(hours=1), 2.0, 0.0)]  # 1h ago
sorted_doses = sorted((ts, s) for ts, s, _ in doses)
t_sim = min(sorted_doses[0][0], START)
body = 0.0
di = 0
for i in range(n):
    t = START + timedelta(seconds=span * i / (n - 1))
    while di < len(sorted_doses) and sorted_doses[di][0] <= t:
        t_d, s_d = sorted_doses[di]
        body = max(0.0, body - rate * (t_d - t_sim).total_seconds() / 3600.0)
        body += s_d
        t_sim = t_d
        di += 1
    body = max(0.0, body - rate * (t - t_sim).total_seconds() / 3600.0)
    t_sim = t
    if t >= doses[0][0]:
        # Forward truth: body(t_dose+) = 2.0, decaying at 1 g/h.
        expected = max(0.0, 2.0 - rate * (t - doses[0][0]).total_seconds() / 3600.0)
        assert abs(body - expected) < 1e-9, f"alcohol mismatch at {t}: {body} vs {expected}"
    else:
        # Before the dose: body = 0 (seeded at the dose time, which is
        # after START here), clamped at 0.
        expected = 0.0
        assert abs(body - expected) < 1e-9, f"alcohol mismatch at {t}: {body} vs {expected}"
# Endpoint parity: the simulated END value equals the live incremental model
# (dose 2.0 at t_dose, 1h of decay to END -> 1.0).
assert abs(body - 1.0) < 1e-9, f"END parity failed: {body}"

print("ALL GRAPH SAMPLING CHECKS PASS")
