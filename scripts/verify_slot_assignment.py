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
    dt(2026, 8, 6, 13, 0), dt(2026, 8, 6, 21, 0),
    dt(2026, 8, 7, 13, 0), dt(2026, 8, 7, 21, 0),
]


def covered_map(assignments):
    return [(a.slot_time.strftime("%m-%d %H:%M"), a.covered) for a in assignments]


def case(name, now, doses, expected_overdue_slot):
    early_grace = timedelta(minutes=240)  # max(30, 480//2)
    assignments = compute_slot_assignments(
        PARSED, doses, now,
        lookback_days=2, future_days=0,
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
        PARSED, doses, now,
        lookback_days=1, future_days=1,
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
        PARSED, doses, now,
        lookback_days=2, future_days=0,
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
    print(f"[{'PASS' if ok else 'FAIL'}] adherence {name}: actual={actual}/{expected} (want {expected_actual}/{expected_expected})")
    if not ok:
        print("    assignments:", covered_map(assignments))
    return ok


results = []

# 1) Reported bug: 17:30 dose should cover 13:00, leave 21:00 uncovered.
#    Prior history covers 08-06/08-07. Only 08-08 13:00 was the missed slot.
results.append(case(
    "17:30 dose covers 13:00 not 21:00",
    now=dt(2026, 8, 8, 17, 30),
    doses=[*PRIOR_HISTORY, dt(2026, 8, 8, 17, 30)],
    expected_overdue_slot=None,  # 13:00 covered by 17:30 -> no overdue
))

# 2) next_dose should report 21:00 (not "covered early" by the 17:30 dose).
results.append(next_dose_check(
    "17:30 dose leaves 21:00 as next",
    now=dt(2026, 8, 8, 17, 30),
    doses=[*PRIOR_HISTORY, dt(2026, 8, 8, 17, 30)],
    expected_next=dt(2026, 8, 8, 21, 0),
))

# 3) Just before the 17:30 dose, 13:00 is overdue (prior covered).
results.append(case(
    "before dose, 13:00 overdue",
    now=dt(2026, 8, 8, 17, 29),
    doses=PRIOR_HISTORY,
    expected_overdue_slot=dt(2026, 8, 8, 13, 0),
))

# 4) Midnight reset: at 00:30 next day, 21:00 missed -> overdue anchors
#    yesterday's 21:00 (not reset to 0). 13:00 was taken.
results.append(case(
    "midnight: yesterday 21:00 still overdue",
    now=dt(2026, 8, 9, 0, 30),
    doses=[*PRIOR_HISTORY, dt(2026, 8, 8, 13, 5)],  # 13:00 taken, 21:00 missed
    expected_overdue_slot=dt(2026, 8, 8, 21, 0),
))

# 5) Midnight with both 08-08 doses taken -> no overdue.
results.append(case(
    "midnight: both taken -> no overdue",
    now=dt(2026, 8, 9, 0, 30),
    doses=[*PRIOR_HISTORY, dt(2026, 8, 8, 13, 5), dt(2026, 8, 8, 21, 10)],
    expected_overdue_slot=None,
))

# 6) Adherence with capped grace: a 4h-late dose is NOT on-time (1h grace).
#    08-06 and 08-07 fully on time (4 slots), 08-08 both missed (17:30 is
#    4.5h late for 13:00, 3.5h early for 21:00 -> neither within 1h grace).
#    Window: lookback 2 days -> 08-06, 08-07, 08-08 = 6 slots, all past grace.
results.append(adherence_check(
    "17:30 dose is late for adherence (1h grace)",
    now=dt(2026, 8, 8, 23, 0),
    doses=[*PRIOR_HISTORY, dt(2026, 8, 8, 17, 30)],
    grace_hours=1,
    expected_actual=4,  # only the 4 prior on-time slots
    expected_expected=6,
))

# 7) Adherence: 08-08 13:00 on time counts (5 covered / 6 expected).
results.append(adherence_check(
    "on-time 13:00 dose counts",
    now=dt(2026, 8, 8, 23, 0),
    doses=[*PRIOR_HISTORY, dt(2026, 8, 8, 13, 5)],
    grace_hours=1,
    expected_actual=5,  # 4 prior + 08-08 13:00
    expected_expected=6,
))

print()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
