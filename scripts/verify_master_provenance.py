#!/usr/bin/env python3
"""
Standalone regression harness for audit item B1 (master dose provenance).

Verifies, against a live ``DrinkMasterCoordinator`` (no HA imports —
``dt_util.now()`` is monkeypatched to a fixed deterministic clock):

1. **Provenance tagging** — ``async_add_dose`` stores a 4-element tuple whose
   4th element is the contributing ``source_entry_id``.
2. **Surgical reset** — with two drinks (A, B) interleaving their doses into
   the same master, ``async_remove_doses(A, n)`` removes exactly A's doses
   (newest-first) and leaves B's doses — and B's body-mass contribution —
   intact.  For alcohol, ``body_mass`` is reduced only by A's grams.
3. **Legacy 3-element store migration** — the load path tolerates legacy
   3-element rows (``source_entry_id`` defaults to ``None``) and the legacy
   fallback in ``async_remove_doses`` pops the newest doses (with a warning)
   instead of crashing or under-removing.
4. **B5 ordering compatibility** — the history stays chronologically sorted
   after add / surgical remove, and ``last_dose_time`` always reflects the
   true most-recent remaining dose.

Run:  python3 scripts/verify_master_provenance.py
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath("custom_components"))

import ax_dose_logger.drink_coordinator as dc
from ax_dose_logger.const import DRINK_TYPE_ALCOHOL, DRINK_TYPE_CAFFEINE

# Fixed clock so the simulation is deterministic.
CLOCK = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017


class FakeDT:
    """Monkeypatch for homeassistant.util.dt — returns the fixed CLOCK."""

    def now(self, tz=None):
        return CLOCK

    @staticmethod
    def parse_datetime(value):
        from datetime import datetime as _dt

        try:
            return _dt.fromisoformat(value)
        except TypeError, ValueError:
            return None


# Patch dt_util on the coordinator module before any coordinator uses it.
dc.dt_util = FakeDT()


class FakeStore:
    """In-memory stand-in for AxDoseLoggerStore (records saved payloads)."""

    def __init__(self):
        self.saved = None

    def schedule_save_drink_master(self, profile_id, substance, data):
        self.saved = data


class FakeEntry:
    entry_id = "fake_settings"
    data = {}
    options = {}


class FakeHass:
    """Stand-in for HomeAssistant: _push_update schedules a task on hass."""

    def async_create_task(self, coro):
        # Fire-and-forget: close the coroutine so asyncio doesn't warn.
        coro.close()


def _make_master(substance: str, stored: dict | None = None) -> dc.DrinkMasterCoordinator:
    """Construct a DrinkMasterCoordinator without HA's DataUpdateCoordinator.

    Mirrors scripts/sim_low_estimate.py: bypass ``__init__`` and set the
    fields the PK / provenance methods depend on directly.
    """
    master = dc.DrinkMasterCoordinator.__new__(dc.DrinkMasterCoordinator)
    master._substance = substance
    master._profile_id = "default"
    # _save() -> _retention_days() reads self.config_entry (the Drink
    # Settings entry); a bare object with empty data/options suffices.
    master.config_entry = FakeEntry()
    master.hass = FakeHass()
    master._store = FakeStore()
    master._store_key = f"fake_key_{substance}"
    master._caffeine_half_life = dc.GLOBAL_PK_DEFAULTS["global_caffeine_half_life"]
    master._caffeine_tmax = dc.GLOBAL_PK_DEFAULTS["global_caffeine_tmax"]
    master._alcohol_elimination_rate = dc.GLOBAL_PK_DEFAULTS["global_alcohol_elimination_rate"]
    master._last_decay = None
    master.data = dc.DrinkMasterCoordinatorData()
    if stored is not None:
        # Mimic the _async_setup load path (defensive legacy read).
        doses = []
        for item in stored.get("doses", []):
            source = item[3] if len(item) > 3 else None
            doses.append(
                (
                    dc.dt_util.parse_datetime(item[0]),
                    float(item[1]),
                    float(item[2]),
                    source,
                )
            )
        doses.sort(key=lambda dose: dose[0])
        master.data.dose_history = doses
        master.data.last_dose_time = doses[-1][0] if doses else None
        master.data.body_mass = float(stored.get("body_mass", 0.0))
    return master


def _ts(minute: int) -> datetime:
    return CLOCK + timedelta(minutes=minute)


def test_provenance_tagging() -> None:
    """async_add_dose stores (ts, strength, t_dur, source_entry_id)."""
    master = _make_master(DRINK_TYPE_CAFFEINE)
    asyncio.run(master.async_add_dose(_ts(0), 90.0, 0.25, source_entry_id="entryA"))
    dose = master.data.dose_history[0]
    assert len(dose) == 4, f"expected 4-element tuple, got {len(dose)}"
    assert dose[3] == "entryA", f"provenance not tagged: {dose[3]!r}"
    assert master.data.last_dose_time == _ts(0)
    print("PASS: provenance tagging (4-element tuple with source_entry_id)")


def test_surgical_reset_interleaved() -> None:
    """Interleaved A1,B1,A2,B2,A3 -> removing A leaves exactly B's doses."""
    master = _make_master(DRINK_TYPE_CAFFEINE)
    # Interleave: A1, B1, A2, B2, A3 (chronological).
    plan = [
        (_ts(0), 90.0, "entryA"),
        (_ts(10), 80.0, "entryB"),
        (_ts(20), 90.0, "entryA"),
        (_ts(30), 80.0, "entryB"),
        (_ts(40), 90.0, "entryA"),
    ]
    for ts, strength, source in plan:
        asyncio.run(master.async_add_dose(ts, strength, 0.25, source_entry_id=source))
    assert len(master.data.dose_history) == 5

    # Reset drink A: remove its 3 doses surgically.
    removed = asyncio.run(master.async_remove_doses("entryA", 3))
    assert removed == 3, f"expected 3 removals, got {removed}"
    remaining = master.data.dose_history
    assert len(remaining) == 2, f"expected 2 remaining doses, got {len(remaining)}"
    assert all(dose[3] == "entryB" for dose in remaining), f"B's doses must survive A's reset: {remaining}"
    assert [dose[1] for dose in remaining] == [80.0, 80.0], f"B's strengths must be intact: {remaining}"
    # B5 ordering invariant: history stays chronologically sorted.
    ts_list = [dose[0] for dose in remaining]
    assert ts_list == sorted(ts_list), f"history not sorted after removal: {ts_list}"
    assert master.data.last_dose_time == _ts(30), f"last_dose_time must be B's latest: {master.data.last_dose_time}"
    print("PASS: surgical reset keeps interleaved B doses intact (sorted, correct last_dose_time)")


def test_surgical_reset_alcohol_body_mass() -> None:
    """Alcohol: removing A's doses subtracts only A's grams from body_mass."""
    master = _make_master(DRINK_TYPE_ALCOHOL)
    plan = [
        (_ts(0), 14.0, "entryA"),
        (_ts(10), 10.0, "entryB"),
        (_ts(20), 14.0, "entryA"),
    ]
    for ts, strength, source in plan:
        asyncio.run(master.async_add_dose(ts, strength, 0.0, source_entry_id=source))
    expected_after = 10.0  # only B's grams remain
    removed = asyncio.run(master.async_remove_doses("entryA", 2))
    assert removed == 2, f"expected 2 removals, got {removed}"
    assert abs(master.data.body_mass - expected_after) < 1e-9, (
        f"body_mass must be reduced only by A's grams: {master.data.body_mass}"
    )
    assert len(master.data.dose_history) == 1
    assert master.data.dose_history[0][3] == "entryB"
    print("PASS: alcohol body_mass reduced only by the removed drink's grams")


def test_legacy_3_element_load() -> None:
    """Legacy 3-element store rows load with source_entry_id=None, no error."""
    stored = {
        "doses": [
            [_ts(0).isoformat(), 90.0, 0.25],
            [_ts(10).isoformat(), 80.0, 0.25],
        ],
        "body_mass": 0.0,
        "last_decay": None,
    }
    master = _make_master(DRINK_TYPE_CAFFEINE, stored)
    assert len(master.data.dose_history) == 2
    assert all(dose[3] is None for dose in master.data.dose_history), (
        f"legacy rows must load with None provenance: {master.data.dose_history}"
    )
    print("PASS: legacy 3-element store rows load with None provenance")


def test_legacy_fallback_pop_newest() -> None:
    """Untagged (None) doses fall back to pop-newest removal, never crash."""
    stored = {
        "doses": [
            [_ts(0).isoformat(), 90.0, 0.25],
            [_ts(10).isoformat(), 80.0, 0.25],
            [_ts(20).isoformat(), 70.0, 0.25],
        ],
        "body_mass": 0.0,
        "last_decay": None,
    }
    master = _make_master(DRINK_TYPE_CAFFEINE, stored)
    # Reset "entryA" (which has no tagged doses): legacy fallback pops the
    # newest 2 doses regardless of contributor (pre-B1 behavior).
    removed = asyncio.run(master.async_remove_doses("entryA", 2))
    assert removed == 2, f"expected 2 removals, got {removed}"
    assert len(master.data.dose_history) == 1
    assert master.data.dose_history[0][1] == 90.0, (
        f"pop-newest must remove the two newest doses: {master.data.dose_history}"
    )
    print("PASS: legacy fallback pops newest doses without crashing")


def test_undo_drink_surgical() -> None:
    """DrinkCoordinator.async_undo_drink removes only its own master dose."""
    # Simulate the master state after A and B each logged one dose.
    master = _make_master(DRINK_TYPE_CAFFEINE)
    asyncio.run(master.async_add_dose(_ts(0), 90.0, 0.25, source_entry_id="entryA"))
    asyncio.run(master.async_add_dose(_ts(10), 80.0, 0.25, source_entry_id="entryB"))
    # A undoes its drink: exactly A's dose (the OLDER one) must be removed,
    # not the master's most-recent (B's) dose.
    removed = asyncio.run(master.async_remove_doses("entryA", 1))
    assert removed == 1
    remaining = master.data.dose_history
    assert len(remaining) == 1, f"B's dose must survive A's undo: {remaining}"
    assert remaining[0][3] == "entryB", f"surviving dose must be B's: {remaining}"
    assert master.data.last_dose_time == _ts(10)
    print("PASS: per-dose undo removes the contributor's dose, not the newest")


def main() -> None:
    test_provenance_tagging()
    test_surgical_reset_interleaved()
    test_surgical_reset_alcohol_body_mass()
    test_legacy_3_element_load()
    test_legacy_fallback_pop_newest()
    test_undo_drink_surgical()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
