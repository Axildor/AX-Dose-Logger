"""
Pure, side-effect-free retention helpers for ax_dose_logger.

All persistent history lists (medicine doses, skipped slots, drink doses,
drink-master doses, adherence overrides, and dated effectiveness metrics) are
bounded to a per-entry ``retention_days`` window to keep both RAM and the
``.storage`` JSON files from growing without limit while preserving a full
365-day (default) record for medical-export use.

Pruning is performed in two places — on load (frees RAM for installations
that previously ran unbounded) and on save (keeps the JSON bounded) — both
calling the pure helpers below.  The 1-minute ``_recompute_data`` tick never
prunes; a 365-day sensor reading the list mid-window still sees the full window
because the in-memory list is only pruned at load + save boundaries.

PK safety
---------
Caffeine body-mass is recomputed from the full in-memory history on each tick.
After pruning, doses older than ``retention_days`` are gone — but at 365 days
× 24 / 5 ≈ 1752 half-lives their PK contribution is effectively zero (<1% after
just 5 half-lives, ~25h).  Alcohol does NOT recompute from history (incremental
zero-order simulation from persisted ``body_mass`` + ``last_decay``), so
pruning old alcohol doses is a no-op for the alcohol simulation.  Do not
"restore" pruned doses — they are PK-irrelevant by design.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta


def retention_cutoff(now: datetime, retention_days: int) -> datetime:
    """Return the earliest ``datetime`` that should be retained."""
    retention_days = max(retention_days, 0)
    return now - timedelta(days=retention_days)


def retention_cutoff_date(now: datetime, retention_days: int) -> date:
    """Return the earliest local ``date`` that should be retained.

    Used for date-keyed maps (effectiveness metrics) where the key is a
    ``"YYYY-MM-DD"`` string.  The cutoff is the date component of the
    retention boundary, inclusive — any date strictly older is pruned.
    """
    return retention_cutoff(now, retention_days).date()


def prune_timestamps(timestamps: Iterable[datetime], cutoff: datetime) -> list[datetime]:
    """Drop timestamps older than ``cutoff``.  Pure — returns a new list."""
    return [ts for ts in timestamps if ts >= cutoff]


def prune_dose_pairs(history: Iterable[tuple], cutoff: datetime) -> list:
    """Drop ``(ts, strength)`` pairs whose timestamp is older than ``cutoff``."""
    return [pair for pair in history if pair[0] >= cutoff]


def prune_dose_triples(history: Iterable[tuple], cutoff: datetime) -> list:
    """Drop ``(ts, strength, t_dur)`` triples older than ``cutoff``.

    Used by :class:`DrinkMasterCoordinator` for caffeine/alcohol dose history
    where each entry carries a drinking-duration column.
    """
    return [triple for triple in history if triple[0] >= cutoff]


def prune_metric_dict(
    metrics: dict[str, dict],
    cutoff_date: date,
) -> dict[str, dict]:
    """Drop dated metric entries older than ``cutoff_date``.

    ``metrics`` shape (post-v2 migration):
        { metric_key: { "YYYY-MM-DD": float, ... }, ... }

    A date key is retained when ``key_date >= cutoff_date`` (string comparison
    is safe for ISO ``YYYY-MM-DD`` because the format is lexicographically
    ordered; we still parse to be defensive against malformed keys).
    """
    cutoff_str = cutoff_date.isoformat()
    kept: dict[str, dict] = {}
    for key, dated in metrics.items():
        if not isinstance(dated, dict):
            # Malformed entry — keep as-is so the coordinator can decide.
            kept[key] = dated
            continue
        kept_dates: dict[str, float] = {}
        for d, v in dated.items():
            if not isinstance(d, str):
                continue
            # Defensive parse: only retain parseable ISO dates newer than cutoff.
            try:
                parsed = date.fromisoformat(d)
            except ValueError:
                continue
            if parsed >= cutoff_date:
                kept_dates[d] = v
        if kept_dates:
            kept[key] = kept_dates
    return kept
