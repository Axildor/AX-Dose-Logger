#!/usr/bin/env python3
"""Standalone regression test for the Regular Interval overdue re-anchor (2026-08-27).

Verifies the chained-deadline model (parity with the Time of Day slot model):

  1. Overdue does NOT stack: with an 8h interval and a dose at 13:00, the
     overdue counter resets to ~0 at each subsequent deadline (21:00, 05:00,
     13:00 next day) instead of accumulating 8h → 16h → 24h…  The value is
     always < one interval.
  2. next_dose advances to the next *future* chained deadline, so the
     reminder blueprint re-arms at each missed dose time.
  3. A late dose re-anchors everything (overdue clears, next_dose = dose +
     interval).
  4. Unsorted merged timestamps (adherence-override / undo edge case) are
     handled by the max() anchor.

Mirrors PillOverdueSensor._compute_overdue_regular_interval and the
Regular Interval branch of PillNextDoseSensor._update_state /
get_next_dose_time.

Run:  python3 scripts/verify_overdue_reanchor.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Make custom_components importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.ax_dose_logger.schedule import get_next_dose_time

INTERVAL_H = 8
BASE = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)  # dose taken at 13:00


def _overdue_anchor(timestamps: list[datetime], now: datetime) -> datetime | None:
    """Mirror PillOverdueSensor._compute_overdue_regular_interval."""
    if not timestamps or INTERVAL_H <= 0:
        return None
    interval = timedelta(hours=INTERVAL_H)
    anchor = max(timestamps)
    elapsed = now - anchor
    if elapsed <= timedelta(0):
        return None
    n = int(elapsed.total_seconds() // interval.total_seconds())
    if n <= 0:
        return None
    return anchor + n * interval


def _next_dose(timestamps: list[datetime], now: datetime) -> datetime | None:
    """Mirror the Regular Interval branch of PillNextDoseSensor._update_state."""
    if not timestamps:
        return now
    interval = timedelta(hours=INTERVAL_H)
    anchor = max(timestamps)
    elapsed = now - anchor
    if elapsed <= timedelta(0):
        return anchor + interval
    n = int(elapsed.total_seconds() // interval.total_seconds())
    return anchor + (n + 1) * interval


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%m-%d %H:%M") if dt else "None"


def check(name: str, got, want) -> bool:
    ok = got == want
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: got={_fmt(got)} want={_fmt(want)}")
    return ok


def main() -> int:
    all_ok = True

    # ── 1. Overdue does not stack ─────────────────────────────────────
    # Dose at 13:00, 8h interval. At 21:00 (1st deadline) overdue anchors
    # at 21:00 → 0s overdue, NOT 8h. At 05:00 next day (2nd deadline) the
    # anchor advances to 05:00 → 0s overdue, NOT 16h.
    doses = [BASE]
    all_ok &= check("overdue anchor at 21:00 (1st deadline)", _overdue_anchor(doses, BASE + timedelta(hours=8)), BASE + timedelta(hours=8))
    all_ok &= check("overdue anchor at 05:00 next day (2nd deadline)", _overdue_anchor(doses, BASE + timedelta(hours=16)), BASE + timedelta(hours=16))
    all_ok &= check("overdue anchor at 13:00 next day (3rd deadline)", _overdue_anchor(doses, BASE + timedelta(hours=24)), BASE + timedelta(hours=24))

    # Mid-interval: overdue counts from the most recent deadline only.
    # At 02:00 next day (5h past the 21:00 deadline) anchor = 21:00.
    all_ok &= check("overdue anchor mid-interval (5h past 2nd deadline)", _overdue_anchor(doses, BASE + timedelta(hours=13)), BASE + timedelta(hours=8))

    # Not yet due: no overdue.
    all_ok &= check("overdue None before first deadline", _overdue_anchor(doses, BASE + timedelta(hours=7)), None)

    # ── 2. next_dose advances to the next future deadline ─────────────
    all_ok &= check("next_dose at 21:00 → 05:00", _next_dose(doses, BASE + timedelta(hours=8)), BASE + timedelta(hours=16))
    all_ok &= check("next_dose at 05:00 → 13:00", _next_dose(doses, BASE + timedelta(hours=16)), BASE + timedelta(hours=24))
    all_ok &= check("next_dose before first deadline → 21:00", _next_dose(doses, BASE + timedelta(hours=7)), BASE + timedelta(hours=8))

    # Parity with the shared helper used by steady_state.
    class _Entry:  # minimal stub: options fall back to data
        data = {"hours_between_doses": INTERVAL_H}
        options = {}

    all_ok &= check(
        "get_next_dose_time parity at 05:00",
        get_next_dose_time(_Entry(), doses, BASE + timedelta(hours=16), "regular_interval"),
        BASE + timedelta(hours=24),
    )

    # ── 3. Late dose re-anchors everything ────────────────────────────
    # Dose missed at 21:00 and 05:00; user takes a dose at 09:00.
    late = BASE + timedelta(hours=20)
    doses_late = [BASE, late]
    all_ok &= check("overdue cleared after late dose", _overdue_anchor(doses_late, late + timedelta(hours=1)), None)
    all_ok &= check("next_dose = late dose + interval", _next_dose(doses_late, late + timedelta(hours=1)), late + timedelta(hours=8))

    # ── 4. Unsorted merged timestamps (override/undo edge case) ───────
    # The merged dose+skip list is not guaranteed sorted: an override
    # dose at 18:00 followed by an older entry appended at 14:00 puts
    # max() (18:00) at index 1, not [-1].  At 22:00 the correct anchor
    # (18:00) is only 4h elapsed → next_dose = 02:00 next day; a [-1]
    # anchor (14:00) would wrongly yield 22:00.
    unsorted = [BASE, BASE + timedelta(hours=5), BASE + timedelta(hours=1)]
    all_ok &= check("unsorted list: next_dose anchored at max()", _next_dose(unsorted, BASE + timedelta(hours=9)), BASE + timedelta(hours=13))
    all_ok &= check("unsorted list: overdue None before max()+interval", _overdue_anchor(unsorted, BASE + timedelta(hours=9)), None)
    all_ok &= check("unsorted list: overdue anchors at max()+interval", _overdue_anchor(unsorted, BASE + timedelta(hours=13)), BASE + timedelta(hours=13))

    print()
    print("ALL PASS" if all_ok else "FAILURES DETECTED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())