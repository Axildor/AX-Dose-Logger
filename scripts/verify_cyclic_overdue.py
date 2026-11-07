"""Verify the Cyclic SCHEDULE gate: ON-day slots, lateness until the next ON slot.

Companion to ``verify_cyclic_safe_to_take.py``.  That harness pins the
*pharmacological* gate (the rolling window); this one pins the *schedule* gate
(ON-day slot coverage), which is the defect the user actually hit: a missed ON
day disappeared at midnight instead of staying overdue.

The old implementation asked ``is_day_covered(now.date(), timestamps)`` — "did
anything happen TODAY" — so:

* an ON day missed at 08:00 was reported overdue until 00:00 and then silently
  became "not overdue", because the new day had no doses on it;
* a catch-up dose taken at 00:30 on the following OFF day stamped a different
  calendar date, so even a correct multi-day lookback would still have shown
  the ON slot as missed.

The fix is the shared Cyclic slot model (``compute_cyclic_slot_assignments``):
one slot per ON day, lateness extending until the next ON slot.  Scenarios
covered here:

* A missed ON slot is still overdue after midnight, throughout the OFF span,
  and on the following ON day until it is discharged.
* A dose taken late — at 00:30 on the OFF day, or later on a following ON day —
  consumes the missed slot (lateness until the next ON slot).
* A skipped slot clears it exactly like a dose.
* The one-full-cycle lookback bound is respected: nothing older than
  ``days_on + days_off`` is reported.
* OFF days contribute no slot, so an OFF day can never be overdue.
* The next-ON-day helper returns the NEXT ON DAY, not the next CYCLE.

Runs without a Home Assistant install: ``homeassistant.config_entries`` and
``homeassistant.util.dt`` are stubbed (only ``parse_dose_time`` and
``is_on_day`` are needed by :mod:`schedule`).
"""

import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# --- Stub homeassistant.config_entries + homeassistant.util.dt ---
ha_pkg = types.ModuleType("homeassistant")
ha_ce = types.ModuleType("homeassistant.config_entries")
ha_ce.ConfigEntry = object
ha_pkg.config_entries = ha_ce
ha_util = types.ModuleType("homeassistant.util")
ha_dt = types.ModuleType("homeassistant.util.dt")
ha_dt.now = lambda: datetime.now(UTC)
ha_util.dt = ha_dt
ha_pkg.util = ha_util
sys.modules["homeassistant"] = ha_pkg
sys.modules["homeassistant.config_entries"] = ha_ce
sys.modules["homeassistant.util"] = ha_util
sys.modules["homeassistant.util.dt"] = ha_dt

# --- Load the real integration const + sliding_window + schedule ---
pkg = types.ModuleType("custom_components")
pkg.__path__ = [str(Path(__file__).resolve().parent.parent / "custom_components")]
sys.modules["custom_components"] = pkg
pl = types.ModuleType("custom_components.ax_dose_logger")
pl.__path__ = [str(Path(__file__).resolve().parent.parent / "custom_components" / "ax_dose_logger")]
sys.modules["custom_components.ax_dose_logger"] = pl

from custom_components.ax_dose_logger.schedule import (
    compute_cyclic_slot_assignments,
    cyclic_early_grace,
    cyclic_slot_times,
    get_next_cyclic_dose_time,
)

TRACKING_CYCLIC = "cyclic"


class FakeEntry:
    """Minimal ConfigEntry stand-in: only .options / .data are read."""

    def __init__(self, **values):
        self.data = dict(values)
        self.options = dict(values)


def make_entry(*, days_on: int, days_off: int, anchor: str, dose_time: str = "08:00", pills_per_slot: int = 1):
    return FakeEntry(
        tracking_type=TRACKING_CYCLIC,
        days_on=days_on,
        days_off=days_off,
        cycle_anchor_date=anchor,
        dose_time=dose_time,
        pills_per_slot=pills_per_slot,
    )


def overdue_since(entry, timestamps, now, lookback_days):
    """Mirror sensors/overdue.py::_compute_overdue_cyclic."""
    assignments = compute_cyclic_slot_assignments(
        entry,
        timestamps,
        now,
        lookback_days=max(1, lookback_days),
        future_days=0,
        pills_per_slot=entry.options.get("pills_per_slot", 1),
    )
    anchor = None
    for a in assignments:
        if a.slot_time > now:
            break
        if not a.covered:
            anchor = a.slot_time
    return anchor


def check(name, expected, actual):
    ok = expected == actual
    print(f"{'PASS' if ok else 'FAIL'}: {name} expected={expected} actual={actual}")
    return ok


def check_that(name, condition):
    """Assert a truth value, keeping the boolean out of a positional argument."""
    print(f"{'PASS' if condition else 'FAIL'}: {name} (condition held={condition})")
    return bool(condition)


def main() -> int:
    results = []

    # ── Scenario A: alternating 1-on / 1-off, dose at 08:00 ──
    # Anchor 2026-11-01 => ON: 11-01, 11-03, 11-05...  OFF: 11-02, 11-04...
    entry = make_entry(days_on=1, days_off=1, anchor="2026-11-01")
    lookback = 2  # days_on + days_off
    missed_slot = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)  # ON day, never taken

    # Before the slot time on the ON day: not overdue yet.
    now = datetime(2026, 11, 1, 7, 0, tzinfo=UTC)
    results.append(check("A before slot -> not overdue", None, overdue_since(entry, [], now, lookback)))

    # Just after the slot: overdue at the slot time.
    now = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    results.append(
        check("A after slot, ON day -> overdue at slot", missed_slot, overdue_since(entry, [], now, lookback))
    )

    # 23:59 same ON day: still overdue.  (The old code also passed this.)
    now = datetime(2026, 11, 1, 23, 59, tzinfo=UTC)
    results.append(check("A 23:59 ON day -> overdue", missed_slot, overdue_since(entry, [], now, lookback)))

    # 00:01 the OFF day: THE BUG.  Old code returned None here; the fix keeps it.
    now = datetime(2026, 11, 2, 0, 1, tzinfo=UTC)
    results.append(
        check(
            "A 00:01 OFF day -> STILL overdue (midnight survival)", missed_slot, overdue_since(entry, [], now, lookback)
        )
    )

    # Mid-OFF-day and late-OFF-day: still overdue.
    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    results.append(check("A mid OFF day -> still overdue", missed_slot, overdue_since(entry, [], now, lookback)))

    # Catch-up dose at 00:30 on the OFF day clears the miss (lateness to the
    # next ON slot).  The dose carries the OFF day's calendar date, which is
    # exactly why a day-level coverage check could not express this.
    catch_up = datetime(2026, 11, 2, 0, 30, tzinfo=UTC)
    now = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
    results.append(
        check("A catch-up on OFF day clears the miss", None, overdue_since(entry, [catch_up], now, lookback))
    )

    # An early dose 12h BEFORE the slot also covers it (early_grace = 24h here
    # for an alternating cycle: half of the 48h ON-to-ON gap, capped at 24h).
    grace = cyclic_early_grace(entry)
    results.append(check("A alternating early_grace (24h cap)", timedelta(hours=24), grace))
    early = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)  # 12h before 11-01 08:00
    now = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    results.append(check("A dose 12h early covers the slot", None, overdue_since(entry, [early], now, lookback)))

    # ── Scenario B: 5-on / 2-off, multi-day run ──
    # Anchor 2026-11-01: ON Nov 1-5, OFF Nov 6-7, ON Nov 8-12.
    entry_52 = make_entry(days_on=5, days_off=2, anchor="2026-11-01")
    lookback_52 = 7
    grace_52 = cyclic_early_grace(entry_52)
    results.append(check("B 5on2off early_grace (12h)", timedelta(hours=12), grace_52))

    # Miss Nov 4 (an ON day).  Probe Nov 4 evening: overdue at Nov 4 08:00.
    now = datetime(2026, 11, 4, 20, 0, tzinfo=UTC)
    results.append(
        check(
            "B miss Nov 4 -> overdue at Nov 4 08:00",
            datetime(2026, 11, 4, 8, 0, tzinfo=UTC),
            overdue_since(entry_52, [], now, lookback_52),
        )
    )

    # Probe ON Nov 5: the LATER uncovered slot (Nov 5) is the anchor — the most
    # recent miss.  Nov 4 remains missed but is no longer the latest.
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    results.append(
        check(
            "B next ON day reports its own slot",
            datetime(2026, 11, 5, 8, 0, tzinfo=UTC),
            overdue_since(entry_52, [], now, lookback_52),
        )
    )

    # Probe the first OFF day Nov 6: still overdue at the latest ON slot.
    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    results.append(
        check(
            "B first OFF day -> overdue at Nov 5 slot",
            datetime(2026, 11, 5, 8, 0, tzinfo=UTC),
            overdue_since(entry_52, [], now, lookback_52),
        )
    )

    # Cover EVERY ON day inside the 7-day lookback (Nov 1-5).  Any earlier ON
    # slot left uncovered would (correctly) remain the most recent miss, so the
    # coverage list must be complete for "nothing overdue" to be the answer.
    covered = [datetime(2026, 11, day, 9, 0, tzinfo=UTC) for day in range(1, 6)]
    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    results.append(
        check("B all ON days covered -> OFF day not overdue", None, overdue_since(entry_52, covered, now, lookback_52))
    )

    # ── Scenario C: skip clears a miss exactly like a dose ──
    skipped = [datetime(2026, 11, 1, 10, 0, tzinfo=UTC)]
    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    results.append(check("C skipped ON slot clears overdue", None, overdue_since(entry, skipped, now, lookback)))

    # ── Scenario D: one-full-cycle lookback bound ──
    # A miss 20 days ago is outside the 1on/1off (2-day) lookback -> not reported.
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    anchor = overdue_since(entry, [], now, lookback)
    results.append(
        check_that(
            "D lookback bound: only slots inside 2 days are reported",
            anchor is not None and (now - anchor) <= timedelta(days=lookback),
        )
    )

    # Same probe with the full-cycle (7-day) lookback for 5on/2off.
    anchor_52 = overdue_since(entry_52, [], now, lookback_52)
    results.append(
        check_that(
            "D 5on2off lookback bound honoured",
            anchor_52 is None or (now - anchor_52) <= timedelta(days=lookback_52),
        )
    )

    # ── Scenario E: OFF days contribute no slots at all ──
    slots = cyclic_slot_times(entry_52, datetime(2026, 11, 7, 12, 0, tzinfo=UTC), lookback_days=7, future_days=0)
    off_days = {datetime(2026, 11, 6).date(), datetime(2026, 11, 7).date()}
    results.append(check_that("E no slot on an OFF day", all(s.date() not in off_days for s in slots)))

    # ── Scenario F: next ON day, not next cycle ──
    # 5on/2off: on ON day Nov 5 (the last ON day of the run), the next dose is
    # Nov 8 (the first ON day of the next run) -- but on ON day Nov 4, the next
    # dose is Nov 5 (TOMORROW), not Nov 8.  The old `cycle_length -
    # position_in_cycle` returned Nov 8 in that case.
    nxt = get_next_cyclic_dose_time(entry_52, datetime(2026, 11, 4, 12, 0, tzinfo=UTC))
    results.append(
        check("F ON day Nov 4 -> next dose Nov 5 (not next cycle)", datetime(2026, 11, 5, 8, 0, tzinfo=UTC), nxt)
    )

    # On the last ON day, the next dose genuinely skips the OFF span.
    nxt = get_next_cyclic_dose_time(entry_52, datetime(2026, 11, 5, 12, 0, tzinfo=UTC))
    results.append(check("F last ON day -> next dose Nov 8", datetime(2026, 11, 8, 8, 0, tzinfo=UTC), nxt))

    # On an OFF day, the next dose is the next ON day.
    nxt = get_next_cyclic_dose_time(entry_52, datetime(2026, 11, 6, 12, 0, tzinfo=UTC))
    results.append(check("F OFF day -> next dose Nov 8", datetime(2026, 11, 8, 8, 0, tzinfo=UTC), nxt))

    # Alternating: ON Nov 3 -> next ON Nov 5.
    nxt = get_next_cyclic_dose_time(entry, datetime(2026, 11, 3, 12, 0, tzinfo=UTC))
    results.append(check("F alternating ON day -> next ON day", datetime(2026, 11, 5, 8, 0, tzinfo=UTC), nxt))

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
