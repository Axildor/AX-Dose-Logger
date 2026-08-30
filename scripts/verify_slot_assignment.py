"""Targeted unit check for the dose-stealing + midnight-reset fix.

Simulates the reported scenario: a 13:00 + 21:00 schedule where a pill
taken at 17:30 (4.5h late for 13:00, 3.5h early for 21:00) must clear the
13:00 overdue (not be stolen by 21:00), and overdue must persist across
midnight if a slot stays uncovered.

All cases include realistic prior history (every prior slot taken on
time) so only the slot under test is the variable.
"""

import os
import sys
from datetime import UTC, datetime, timedelta

# Make the custom_components package importable with its HA deps.
sys.path.insert(0, os.path.abspath("."))

from custom_components.ax_dose_logger.schedule import (
    LATENESS_CAPPED,
    LATENESS_UNTIL_NEXT_SLOT,
    compute_slot_assignments,
)

PARSED = [(13, 0), (21, 0)]
TZ = UTC


def dt(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


# Prior history: every slot on 08-06 and 08-07 taken on time.
PRIOR_HISTORY = [
    dt(2026, 8, 6, 13, 0),
    dt(2026, 8, 6, 21, 0),
    dt(2026, 8, 7, 13, 0),
    dt(2026, 8, 7, 21, 0),
]

# Prior history at 2 pills per slot: every prior slot fully covered
# (used by the pills_per_slot regression cases below).
PRIOR_HISTORY_PPS2 = [
    dt(2026, 8, 6, 13, 0),
    dt(2026, 8, 6, 13, 1),
    dt(2026, 8, 6, 21, 0),
    dt(2026, 8, 6, 21, 5),
    dt(2026, 8, 7, 13, 0),
    dt(2026, 8, 7, 13, 5),
    dt(2026, 8, 7, 21, 0),
    dt(2026, 8, 7, 21, 5),
]


def covered_map(assignments):
    return [(a.slot_time.strftime("%m-%d %H:%M"), a.covered) for a in assignments]


def case(name, now, doses, expected_overdue_slot):
    early_grace = timedelta(minutes=240)  # max(30, 480//2)
    assignments = compute_slot_assignments(
        PARSED,
        doses,
        now,
        lookback_days=2,
        future_days=0,
        early_grace=early_grace,
        lateness_mode=LATENESS_UNTIL_NEXT_SLOT,
    )
    overdue = None
    for a in assignments:
        if a.slot_time > now:
            break
        if not a.covered:
            overdue = a.slot_time
    overdue_str = overdue.strftime("%m-%d %H:%M") if overdue else "None"
    expected_str = expected_overdue_slot.strftime("%m-%d %H:%M") if expected_overdue_slot else "None"
    ok = overdue_str == expected_str
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: overdue={overdue_str} (want {expected_str})")
    if not ok:
        print("    assignments:", covered_map(assignments))
    return ok


def next_dose_check(name, now, doses, expected_next):
    early_grace = timedelta(minutes=240)
    assignments = compute_slot_assignments(
        PARSED,
        doses,
        now,
        lookback_days=1,
        future_days=1,
        early_grace=early_grace,
        lateness_mode=LATENESS_UNTIL_NEXT_SLOT,
    )
    nxt = None
    for a in assignments:
        if not a.covered and a.slot_time > now:
            nxt = a.slot_time
            break
    nxt_str = nxt.strftime("%m-%d %H:%M") if nxt else "None"
    expected_str = expected_next.strftime("%m-%d %H:%M") if expected_next else "None"
    ok = nxt_str == expected_str
    print(f"[{'PASS' if ok else 'FAIL'}] next_dose {name}: next={nxt_str} (want {expected_str})")
    if not ok:
        print("    assignments:", covered_map(assignments))
    return ok


def adherence_check(name, now, doses, grace_hours, expected_actual, expected_expected):
    grace = timedelta(hours=grace_hours)
    assignments = compute_slot_assignments(
        PARSED,
        doses,
        now,
        lookback_days=2,
        future_days=0,
        early_grace=grace,
        lateness_mode=LATENESS_CAPPED,
        lateness_cap=grace,
    )
    cutoff = now - timedelta(days=7)
    actual = expected = 0
    for a in assignments:
        if a.slot_time < cutoff:
            continue
        if now < a.slot_time + grace:
            continue
        expected += 1
        if a.covered:
            actual += 1
    ok = actual == expected_actual and expected == expected_expected
    print(
        f"[{'PASS' if ok else 'FAIL'}] adherence {name}: actual={actual}/{expected} (want {expected_actual}/{expected_expected})"
    )
    if not ok:
        print("    assignments:", covered_map(assignments))
    return ok


results = []

# 1) Reported bug: 17:30 dose should cover 13:00, leave 21:00 uncovered.
#    Prior history covers 08-06/08-07. Only 08-08 13:00 was the missed slot.
results.append(
    case(
        "17:30 dose covers 13:00 not 21:00",
        now=dt(2026, 8, 8, 17, 30),
        doses=[*PRIOR_HISTORY, dt(2026, 8, 8, 17, 30)],
        expected_overdue_slot=None,  # 13:00 covered by 17:30 -> no overdue
    )
)

# 2) next_dose should report 21:00 (not "covered early" by the 17:30 dose).
results.append(
    next_dose_check(
        "17:30 dose leaves 21:00 as next",
        now=dt(2026, 8, 8, 17, 30),
        doses=[*PRIOR_HISTORY, dt(2026, 8, 8, 17, 30)],
        expected_next=dt(2026, 8, 8, 21, 0),
    )
)

# 3) Just before the 17:30 dose, 13:00 is overdue (prior covered).
results.append(
    case(
        "before dose, 13:00 overdue",
        now=dt(2026, 8, 8, 17, 29),
        doses=PRIOR_HISTORY,
        expected_overdue_slot=dt(2026, 8, 8, 13, 0),
    )
)

# 4) Midnight reset: at 00:30 next day, 21:00 missed -> overdue anchors
#    yesterday's 21:00 (not reset to 0). 13:00 was taken.
results.append(
    case(
        "midnight: yesterday 21:00 still overdue",
        now=dt(2026, 8, 9, 0, 30),
        doses=[*PRIOR_HISTORY, dt(2026, 8, 8, 13, 5)],  # 13:00 taken, 21:00 missed
        expected_overdue_slot=dt(2026, 8, 8, 21, 0),
    )
)

# 5) Midnight with both 08-08 doses taken -> no overdue.
results.append(
    case(
        "midnight: both taken -> no overdue",
        now=dt(2026, 8, 9, 0, 30),
        doses=[*PRIOR_HISTORY, dt(2026, 8, 8, 13, 5), dt(2026, 8, 8, 21, 10)],
        expected_overdue_slot=None,
    )
)

# 6) Adherence with capped grace: a 4h-late dose is NOT on-time (1h grace).
#    08-06 and 08-07 fully on time (4 slots), 08-08 both missed (17:30 is
#    4.5h late for 13:00, 3.5h early for 21:00 -> neither within 1h grace).
#    Window: lookback 2 days -> 08-06, 08-07, 08-08 = 6 slots, all past grace.
results.append(
    adherence_check(
        "17:30 dose is late for adherence (1h grace)",
        now=dt(2026, 8, 8, 23, 0),
        doses=[*PRIOR_HISTORY, dt(2026, 8, 8, 17, 30)],
        grace_hours=1,
        expected_actual=4,  # only the 4 prior on-time slots
        expected_expected=6,
    )
)

# 7) Adherence: 08-08 13:00 on time counts (5 covered / 6 expected).
results.append(
    adherence_check(
        "on-time 13:00 dose counts",
        now=dt(2026, 8, 8, 23, 0),
        doses=[*PRIOR_HISTORY, dt(2026, 8, 8, 13, 5)],
        grace_hours=1,
        expected_actual=5,  # 4 prior + 08-08 13:00
        expected_expected=6,
    )
)

# ─────────────────────────────────────────────────────────────────────
# pills_per_slot regression (audit edge case: 4 pills/day, 2 slots).
# With pills_per_slot=2 a slot is only covered after TWO doses inside
# its window; a single dose leaves it uncovered (due/overdue persists).
# ──────────────────────────────────────────────────────────────────────


def pps_case(name, now, doses, pills_per_slot, expected_overdue_slot, expected_remaining=None):
    """Overdue check with pills_per_slot + slot_remaining verification."""
    early_grace = timedelta(minutes=240)
    assignments = compute_slot_assignments(
        PARSED,
        doses,
        now,
        lookback_days=2,
        future_days=0,
        early_grace=early_grace,
        lateness_mode=LATENESS_UNTIL_NEXT_SLOT,
        pills_per_slot=pills_per_slot,
    )
    overdue = None
    current = None
    for a in assignments:
        if a.slot_time > now:
            break
        current = a
        if not a.covered:
            overdue = a.slot_time
    overdue_str = overdue.strftime("%m-%d %H:%M") if overdue else "None"
    expected_str = expected_overdue_slot.strftime("%m-%d %H:%M") if expected_overdue_slot else "None"
    remaining = max(0, pills_per_slot - current.assigned_count) if current else None
    ok = overdue_str == expected_str and remaining == expected_remaining
    print(
        f"[{'PASS' if ok else 'FAIL'}] {name}: overdue={overdue_str} (want {expected_str}), "
        f"slot_remaining={remaining} (want {expected_remaining})"
    )
    if not ok:
        print(
            "    assignments:",
            [(a.slot_time.strftime("%m-%d %H:%M"), a.covered, a.assigned_count) for a in assignments],
        )
    return ok


# Trace 1 (compliant user, 2 pills/slot): 08:00 slot has 1 of 2 pills ->
# still uncovered (overdue anchors it) with 1 pill remaining. The old
# binary model wrongly marked the slot covered after ONE pill.
results.append(
    pps_case(
        "pps=2: 1 pill in 13:00 slot -> uncovered, 1 remaining",
        now=dt(2026, 8, 8, 14, 0),
        doses=[*PRIOR_HISTORY_PPS2, dt(2026, 8, 8, 13, 0)],
        pills_per_slot=2,
        expected_overdue_slot=dt(2026, 8, 8, 13, 0),
        expected_remaining=1,
    )
)

# Trace 1 continued: both pills taken -> slot covered, 0 remaining.
results.append(
    pps_case(
        "pps=2: 2 pills in 13:00 slot -> covered, 0 remaining",
        now=dt(2026, 8, 8, 14, 0),
        doses=[*PRIOR_HISTORY_PPS2, dt(2026, 8, 8, 13, 0), dt(2026, 8, 8, 13, 5)],
        pills_per_slot=2,
        expected_overdue_slot=None,
        expected_remaining=0,
    )
)

# Trace 2 (prompt-following user, 1 pill/slot): default pps=1 keeps the
# legacy behavior — one dose covers the slot (no regression).
results.append(
    pps_case(
        "pps=1 (default): 1 pill covers the slot",
        now=dt(2026, 8, 8, 14, 0),
        doses=[*PRIOR_HISTORY, dt(2026, 8, 8, 13, 0)],
        pills_per_slot=1,
        expected_overdue_slot=None,
        expected_remaining=0,
    )
)

# Dose-stealing guard with pps=2: a single dose between slots belongs to
# the EARLIER slot (greedy chronological), leaving 21:00 fully uncovered.
results.append(
    pps_case(
        "pps=2: 17:30 dose -> late 13:00 (1/2), 21:00 uncovered",
        now=dt(2026, 8, 8, 22, 0),
        doses=[*PRIOR_HISTORY_PPS2, dt(2026, 8, 8, 17, 30)],
        pills_per_slot=2,
        expected_overdue_slot=dt(2026, 8, 8, 21, 0),
        expected_remaining=2,
    )
)


# Adherence with pps=2: expected = slots x 2; 1 pill in one slot = 1 actual.
def adherence_pps_check(name, now, doses, pills_per_slot, grace_hours, expected_actual, expected_expected):
    early_grace = timedelta(hours=grace_hours)
    assignments = compute_slot_assignments(
        PARSED,
        doses,
        now,
        lookback_days=2,
        future_days=0,
        early_grace=early_grace,
        lateness_mode=LATENESS_CAPPED,
        lateness_cap=early_grace,
        pills_per_slot=pills_per_slot,
    )
    actual = 0
    expected = 0
    for a in assignments:
        if a.slot_time < dt(2026, 8, 6, 0, 0):
            continue
        if now < a.slot_time + early_grace:
            continue
        expected += pills_per_slot
        actual += min(a.assigned_count, pills_per_slot)
    ok = actual == expected_actual and expected == expected_expected
    print(
        f"[{'PASS' if ok else 'FAIL'}] {name}: actual={actual}/{expected} (want {expected_actual}/{expected_expected})"
    )
    return ok


# 4 prior slots fully covered (8 pills) + 1 pill in 08-08 13:00 ->
# actual=9, expected=12 (6 slots x 2). The old model would show 5/6 = 83%
# while the user is 75% pill-compliant.
results.append(
    adherence_pps_check(
        "pps=2 adherence: 9/12 pills",
        now=dt(2026, 8, 8, 23, 0),
        doses=[*PRIOR_HISTORY_PPS2, dt(2026, 8, 8, 13, 5)],
        pills_per_slot=2,
        grace_hours=1,
        expected_actual=9,
        expected_expected=12,
    )
)

print()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
