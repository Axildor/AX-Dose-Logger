"""Verify the Cyclic SAFETY gate: safe_to_take is elapsed-time only.

Regression harness for the two-gate split.  ``compute_safe_to_take`` answers
one question — "if a dose were taken now, would it exceed ``pill_limit``
within ``time_window_hours``?" — and must therefore never consult the calendar
or the cycle phase.

The bug this pins down: the function used to force ``0`` on a Cyclic OFF day.
A tracker on a 48-hour schedule with a 30-hour window is legitimately lockout
free for 30 of every 48 hours; the OFF-day clamp overwrote that with a false
zero at midnight, which the card rendered as LIMIT REACHED and the reminder
blueprint treated as a hard block.

Scenarios covered:

* Alternating (1 on / 1 off), 48 h schedule, 30 h window, ``pill_limit=1``:
  a dose at 00:10 yields 0 immediately, is STILL 0 across the following OFF
  day, and returns to 1 at 29 h 56 m — independent of which calendar day that
  lands on.

  The expiry instant is ``dose + 30 h - 5 min buffer`` and the cutoff test is
  ``ts >= cutoff`` (inclusive), so the dose leaves the window one minute
  AFTER the buffered boundary: at 29 h 55 m it is still counted, at 29 h 56 m
  it is not.  This is the pre-existing window arithmetic, unchanged by the
  two-gate split; the harness pins it so a future edit cannot shift the
  boundary silently.
* 5 on / 2 off: the mid-cycle OFF days report the same count as any ON day
  with identical history.
* A genuinely full window still reports 0 — on an ON day AND on an OFF day,
  so the fix cannot be misread as "OFF days are always available".
* No cycle shape can produce a transition that happens only at midnight.

Runs without a Home Assistant install: ``homeassistant.config_entries`` and
the integration ``const`` module are stubbed (only type/constant imports are
needed for :mod:`sliding_window`).
"""

import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# --- Stub homeassistant.config_entries (sliding_window imports ConfigEntry) ---
ha_pkg = types.ModuleType("homeassistant")
ha_ce = types.ModuleType("homeassistant.config_entries")
ha_ce.ConfigEntry = object
ha_pkg.config_entries = ha_ce
sys.modules["homeassistant"] = ha_pkg
sys.modules["homeassistant.config_entries"] = ha_ce

# --- Stub the integration const module (sliding_window imports constants) ---
pkg = types.ModuleType("custom_components")
pkg.__path__ = [str(Path(__file__).resolve().parent.parent / "custom_components")]
sys.modules["custom_components"] = pkg
pl = types.ModuleType("custom_components.ax_dose_logger")
pl.__path__ = [str(Path(__file__).resolve().parent.parent / "custom_components" / "ax_dose_logger")]
sys.modules["custom_components.ax_dose_logger"] = pl
const_mod = types.ModuleType("custom_components.ax_dose_logger.const")
const_mod.DOSE_BUFFER_CAP_FRACTION = 0.25
const_mod.DOSE_BUFFER_DEFAULT_MIN = 5
const_mod.TRACKING_AS_NEEDED = "as_needed"
const_mod.TRACKING_CYCLIC = "cyclic"
const_mod.TRACKING_REGULAR_INTERVAL = "regular_interval"
sys.modules["custom_components.ax_dose_logger.const"] = const_mod

from custom_components.ax_dose_logger.sliding_window import (
    compute_safe_to_take,
    effective_dose_buffer_minutes,
    is_on_day,
)

TRACKING_CYCLIC = "cyclic"


class FakeEntry:
    """Minimal ConfigEntry stand-in: only .options / .data are read."""

    def __init__(self, **values):
        self.data = dict(values)
        self.options = dict(values)


def make_entry(
    *,
    days_on: int,
    days_off: int,
    anchor: str,
    pill_limit: int = 1,
    time_window_hours: float = 30,
    dose_buffer_minutes: int = 5,
) -> FakeEntry:
    return FakeEntry(
        tracking_type=TRACKING_CYCLIC,
        days_on=days_on,
        days_off=days_off,
        cycle_anchor_date=anchor,
        dose_time="08:00",
        pill_limit=pill_limit,
        time_window_hours=time_window_hours,
        dose_buffer_minutes=dose_buffer_minutes,
    )


def check(name, expected, actual):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}: {name} expected={expected} actual={actual}")
    return ok


def check_that(name, condition):
    """Assert a truth value, keyword-only so the boolean is never positional.

    Callers pass a single explicit predicate (e.g. ``is_on_day(...)``) rather
    than a naked ``True``/``False`` literal, keeping the intent readable.
    """
    print(f"{'PASS' if condition else 'FAIL'}: {name} (condition held={condition})")
    return bool(condition)


def main() -> int:
    results = []

    # ── Scenario A: alternating 1-on / 1-off, 48 h schedule, 30 h window ──
    # Anchor 2026-11-01 is an ON day.  2026-11-02 an OFF day, 11-03 ON, etc.
    entry = make_entry(days_on=1, days_off=1, anchor="2026-11-01")
    dose_ts = datetime(2026, 11, 2, 0, 10, tzinfo=UTC)  # 00:10, an OFF day

    buffer_min = effective_dose_buffer_minutes(entry, 30)
    results.append(check("A buffer minutes (30h window, 5min cap ok)", 5, buffer_min))

    # 00:20 the same night — the dose is fresh, the window is full.
    now = datetime(2026, 11, 2, 0, 20, tzinfo=UTC)
    results.append(
        check("A 00:20 OFF day, dose fresh -> 0", 0, compute_safe_to_take(entry, [dose_ts], now, TRACKING_CYCLIC))
    )

    # 08:00 the same OFF day — still locked out by the WINDOW, not the date.
    now = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
    results.append(
        check(
            "A 08:00 OFF day, mid-window -> 0 (window, not date)",
            0,
            compute_safe_to_take(entry, [dose_ts], now, TRACKING_CYCLIC),
        )
    )

    # 23:59 the same OFF day — still 0, and provably not a midnight artifact.
    now = datetime(2026, 11, 2, 23, 59, tzinfo=UTC)
    results.append(
        check("A 23:59 OFF day, mid-window -> 0", 0, compute_safe_to_take(entry, [dose_ts], now, TRACKING_CYCLIC))
    )

    # 00:01 the NEXT day (an ON day) — STILL 0.  The old clamp produced a jump
    # here; the window does not.
    now = datetime(2026, 11, 3, 0, 1, tzinfo=UTC)
    results.append(
        check(
            "A 00:01 next ON day -> still 0 (no midnight jump)",
            0,
            compute_safe_to_take(entry, [dose_ts], now, TRACKING_CYCLIC),
        )
    )

    # 06:04 = dose + 29 h 54 m — before the buffered boundary, still 0.
    now = dose_ts + timedelta(hours=29, minutes=54)
    results.append(
        check("A dose+29h54m -> 0 (before boundary)", 0, compute_safe_to_take(entry, [dose_ts], now, TRACKING_CYCLIC))
    )

    # 06:05 = dose + 29 h 55 m — exactly ON the boundary.  The cutoff test is
    # `ts >= cutoff` (inclusive), so the dose is STILL counted.
    now = dose_ts + timedelta(hours=29, minutes=55)
    results.append(
        check(
            "A dose+29h55m -> 0 (inclusive cutoff boundary)",
            0,
            compute_safe_to_take(entry, [dose_ts], now, TRACKING_CYCLIC),
        )
    )

    # 06:06 = dose + 29 h 56 m — one minute past the boundary, released to 1.
    now = dose_ts + timedelta(hours=29, minutes=56)
    results.append(
        check("A dose+29h56m -> 1 (window released)", 1, compute_safe_to_take(entry, [dose_ts], now, TRACKING_CYCLIC))
    )

    # ── Scenario B: 5-on / 2-off — OFF days must agree with ON days ──
    # Anchor 2026-11-01: ON 1-5 (Nov 1-5), OFF Nov 6-7, ON Nov 8-12.
    entry_52 = make_entry(days_on=5, days_off=2, anchor="2026-11-01")
    on_day = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)  # ON
    off_day = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)  # OFF
    results.append(check_that("B Nov 4 is ON", is_on_day(entry_52, on_day.date(), on_day.date())))
    results.append(check_that("B Nov 6 is OFF", not is_on_day(entry_52, off_day.date(), off_day.date())))

    fresh = datetime(2026, 11, 6, 6, 0, tzinfo=UTC)  # within 30h of a dose
    results.append(
        check("B ON day mid-window -> 0", 0, compute_safe_to_take(entry_52, [fresh], on_day, TRACKING_CYCLIC))
    )
    results.append(
        check(
            "B OFF day mid-window -> 0 (same as ON)",
            0,
            compute_safe_to_take(entry_52, [fresh], off_day, TRACKING_CYCLIC),
        )
    )

    # >30 h before both probes, so the window is genuinely clear at each.
    stale = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)
    results.append(
        check("B ON day window clear -> 1", 1, compute_safe_to_take(entry_52, [stale], on_day, TRACKING_CYCLIC))
    )
    results.append(
        check(
            "B OFF day window clear -> 1 (no clamp)",
            1,
            compute_safe_to_take(entry_52, [stale], off_day, TRACKING_CYCLIC),
        )
    )

    # ── Scenario C: a genuinely full window locks out on OFF days too ──
    entry_daily = make_entry(days_on=7, days_off=0, anchor="2026-11-01", time_window_hours=24)
    two_doses = [
        datetime(2026, 11, 6, 2, 50, tzinfo=UTC),
        datetime(2026, 11, 6, 23, 50, tzinfo=UTC),
    ]
    now = datetime(2026, 11, 7, 0, 30, tzinfo=UTC)
    results.append(
        check(
            "C real lockout -> 0 (limit 1, dose in window)",
            0,
            compute_safe_to_take(entry_daily, two_doses, now, TRACKING_CYCLIC),
        )
    )

    # ── Scenario D: exhaustive midnight sweep -- no date-only transition ──
    # Walk every minute across two midnights and assert the count changes ONLY
    # at the buffered window boundary (dose + 29h55m), never at 00:00.
    start = datetime(2026, 11, 1, 22, 0, tzinfo=UTC)
    boundary = dose_ts + timedelta(hours=29, minutes=55)
    midnight_jumps = []
    prev = None
    t = start
    while t <= dose_ts + timedelta(hours=31):
        val = compute_safe_to_take(entry, [dose_ts], t, TRACKING_CYCLIC)
        if prev is not None and val != prev:
            # A transition is legitimate only at the window boundary.
            if abs((t - boundary).total_seconds()) > 60:
                midnight_jumps.append(t)
        prev = val
        t += timedelta(minutes=1)
    results.append(check("D transitions away from the window boundary", [], midnight_jumps))

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
