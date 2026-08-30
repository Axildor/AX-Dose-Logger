"""
AxDoseLoggerCoordinator — single source of truth for dose history and daily metrics.

Owns the authoritative ``dose_history`` list, ``metric_values`` dict, debounced
store saves, and a 1-minute refresh interval.  Entities become
``CoordinatorEntity`` subscribers and read ``coordinator.data`` instead of
maintaining their own copies of dose history and listening to dispatcher signals.

During the 1D-1 → 1D-3 transition the coordinator still fires the legacy
dispatcher signals so that not-yet-migrated sensors continue to work.
Once all entities are migrated (1D-2) the signal firing is removed (1D-3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import LOGGER, PK_DEFAULTS, RELEASE_INSTANT, RETENTION_DAYS
from .pk_model import PKModel, PKParams, PKResult
from .retention import (
    prune_dose_pairs,
    prune_metric_dict,
    prune_timestamps,
    retention_cutoff,
    retention_cutoff_date,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .store import AxDoseLoggerStore

__all__ = ["AxDoseLoggerCoordinator", "AxDoseLoggerCoordinatorData"]


@dataclass
class AxDoseLoggerCoordinatorData:
    """
    Snapshot of all derived state that entities read from the coordinator.

    ``dose_history`` is the single in-memory source of truth — entities
    must NOT maintain their own copies.  ``concentration`` and
    ``pk_result`` are recomputed on every refresh so the concentration
    sensor and steady-state sensor can read them directly instead of
    via inter-sensor dispatcher signals.

    ``metric_values`` stores daily-locked effectiveness metric values.
    Format (v2 date-keyed): { metric_key: { "YYYY-MM-DD": float, ... } }
    Historical days are retained up to the entry's ``retention_days`` for
    the 365-day medical export; the midnight-rollover clear was removed so
    a new day simply gets a new date key (today's slider reads ``unknown``
    until set).  Only the retention cutoff drops old days.
    """

    dose_history: list[tuple[datetime, float]] = field(default_factory=list)
    last_dose_time: datetime | None = None
    concentration: float | None = 0.0
    pk_result: PKResult | None = None
    # Adherence-specific state (does not affect dose_history)
    adherence_overrides: list[datetime] = field(default_factory=list)
    adherence_reset_time: datetime | None = None
    # Averages reset anchor (does not affect dose_history / PK / stock /
    # adherence).  Set by the "Reset Averages" tool; average sensors clamp
    # their effective window start to max(history_start_date, avg_reset_time)
    # so pre-reset doses stop counting toward the rolling averages without
    # deleting any dose data.
    avg_reset_time: datetime | None = None
    # Skipped-dose slots (does not affect dose_history / PK / stock).
    # Consumed ONLY by the overdue + next_dose sensors so a deliberate
    # skip clears the overdue alarm and advances the schedule without
    # logging a phantom dose. NOT consumed by adherence (stays penalized),
    # concentration, total, last_dose, days_left, pill_limit, or avg_doses.
    # Mirrors the ``adherence_overrides`` pattern with opposite consumers.
    skipped_slots: list[datetime] = field(default_factory=list)
    # Daily-locked metric values (v2 date-keyed):
    # { metric_key: { "YYYY-MM-DD": float, ... } }
    # Historical days retained up to retention_days; midnight no longer clears.
    metric_values: dict[str, dict] = field(default_factory=dict)


class AxDoseLoggerCoordinator(DataUpdateCoordinator[AxDoseLoggerCoordinatorData]):
    """
    Coordinator that owns dose history and drives all entity updates.

    Push-based updates (dose taken / undo / reset) call the ``async_*``
    API methods which update ``self.data`` and notify listeners via
    ``async_set_updated_data``.  The 1-minute ``update_interval`` handles
    periodic refresh (PK decay, pill-limit window, next-dose countdown,
    midnight rollover).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: AxDoseLoggerStore,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            name=f"AX Dose Logger ({entry.title})",
            config_entry=entry,
            update_interval=timedelta(minutes=1),
            always_update=True,
        )
        self._entry = entry
        self._store = store
        self._last_midnight_check: datetime | None = None

    # ------------------------------------------------------------------
    # Retention window
    # ------------------------------------------------------------------
    def _retention_days(self) -> int:
        """Return this entry's retention window in days (default 365).

        Reads ``retention_days`` from options/data with the
        :data:`RETENTION_DAYS` fallback so a never-configured entry still
        gets the 365-day default without requiring a config-flow migration.
        """
        val = self._entry.options.get(
            "retention_days",
            self._entry.data.get("retention_days", RETENTION_DAYS),
        )
        try:
            return max(1, int(val))
        except (TypeError, ValueError):
            return RETENTION_DAYS

    # ------------------------------------------------------------------
    # Setup — load dose history and metrics from store on first refresh
    # ------------------------------------------------------------------
    async def _async_setup(self) -> None:
        """Load dose history, skipped slots, adherence overrides, and metric
        values from the store, pruning each to the entry's retention window.

        Pruning on load frees RAM for installations that previously ran
        unbounded (pre-retention), and the same retention cutoff is applied
        again on save to keep the ``.storage`` JSON bounded.  The 1-min
        ``_recompute_data`` tick never prunes, so a 365-day sensor reading
        the list mid-window still sees the full window.
        """
        now = dt_util.now()
        cutoff = retention_cutoff(now, self._retention_days())
        cutoff_date = retention_cutoff_date(now, self._retention_days())

        dose_history: list[tuple[datetime, float]] = []
        stored = self._store.get_history(self._entry.entry_id)
        if stored:
            for item in stored:
                try:
                    ts_str, strength_val = item
                    dt = dt_util.parse_datetime(ts_str)
                    if dt:
                        dose_history.append((dt, float(strength_val)))
                except (ValueError, TypeError, IndexError):
                    continue
        dose_history = prune_dose_pairs(dose_history, cutoff)

        last_dose = dose_history[-1][0] if dose_history else None

        # Load retained daily metric values (v2 date-keyed shape) and prune
        # to the retention window.  Historical days are kept across midnight
        # rollovers (the prior daily-discard clear is removed in
        # ``_recompute_data``); only the retention cutoff drops old days.
        raw_metrics = self._store.get_metrics(self._entry.entry_id)
        metric_values: dict[str, dict] = {}
        if isinstance(raw_metrics, dict):
            metric_values = prune_metric_dict(raw_metrics, cutoff_date)

        # Load skipped-dose slots (persists deliberate skips across restarts
        # so a reboot does not re-ring the overdue alarm for an explicitly
        # skipped slot). Parallel to dose_history loading.
        skipped_slots: list[datetime] = []
        raw_skipped = self._store.get_skipped(self._entry.entry_id)
        for ts_str in raw_skipped:
            dt = dt_util.parse_datetime(ts_str)
            if dt:
                skipped_slots.append(dt)
        skipped_slots = prune_timestamps(skipped_slots, cutoff)

        # Load adherence overrides + reset time.  Forward-only: a pre-fix
        # installation has no adherence store, so an empty dict yields
        # empty overrides and a None reset anchor — no retroactive
        # adherence credit for past missed slots.
        raw_adherence = self._store.get_adherence(self._entry.entry_id)
        adherence_overrides: list[datetime] = []
        adherence_reset_time: datetime | None = None
        if isinstance(raw_adherence, dict):
            for ts_str in raw_adherence.get("overrides", []) or []:
                dt = dt_util.parse_datetime(ts_str)
                if dt:
                    adherence_overrides.append(dt)
            adherence_overrides = prune_timestamps(adherence_overrides, cutoff)
            reset_str = raw_adherence.get("reset_time")
            if isinstance(reset_str, str):
                adherence_reset_time = dt_util.parse_datetime(reset_str)

        # Averages reset anchor (Reset Averages tool).  Forward-only: a
        # pre-fix installation has no averages store, so the anchor loads
        # as None — averages keep their full history until explicitly reset.
        raw_averages = self._store.get_averages_reset(self._entry.entry_id)
        avg_reset_time: datetime | None = None
        if isinstance(raw_averages, dict):
            avg_reset_str = raw_averages.get("reset_time")
            if isinstance(avg_reset_str, str):
                avg_reset_time = dt_util.parse_datetime(avg_reset_str)

        self.data = AxDoseLoggerCoordinatorData(
            dose_history=dose_history,
            last_dose_time=last_dose,
            adherence_overrides=adherence_overrides,
            adherence_reset_time=adherence_reset_time,
            avg_reset_time=avg_reset_time,
            skipped_slots=skipped_slots,
            metric_values=metric_values,
        )
        LOGGER.debug(
            "AxDoseLoggerCoordinator setup for %s: %d doses, %d skipped, "
            "%d adherence overrides, %d metric-date keys (retention=%dd)",
            self._entry.entry_id,
            len(dose_history),
            len(skipped_slots),
            len(adherence_overrides),
            sum(len(v) for v in metric_values.values() if isinstance(v, dict)),
            self._retention_days(),
        )

    # ------------------------------------------------------------------
    # Data recomputation — shared by periodic tick and push updates
    # ------------------------------------------------------------------
    def _recompute_data(self) -> AxDoseLoggerCoordinatorData:
        """
        Build a fresh coordinator data snapshot with recomputed PK.

        Called by both the 1-minute periodic tick (via
        ``_async_update_data``) and push-based dose events (via
        ``_push_update``).  The dose_history list is already up-to-date
        (mutated by the ``async_*`` API methods), so this method only
        recomputes the derived fields (concentration, PK result).

        On midnight rollover, the PK concentration + derived fields are
        recomputed; metric_values are now RETAINED across midnight (v2
        date-keyed shape) so historical PRO days survive for the 365-day
        export — today's slider reads ``unknown`` until set because today's
        date key is absent, matching the prior UX without the data loss.
        """
        data = self.data
        now = dt_util.now()

        # Recompute PK concentration from full dose history.
        # When elimination is disabled (half_life is 0) the Bateman model
        # would permanently accumulate every dose with no decay — a
        # meaningless climbing number.  Guard at the coordinator level
        # (single source of truth for PK state) so the concentration
        # sensor renders ``unknown`` instead of an infinitely growing
        # value.  Matches the existing ``PillSteadyStateSensor`` guard.
        params = self._build_pk_params()
        if data.dose_history and params.half_life > 0:
            pk_result = PKModel.compute(params, data.dose_history, now)
            concentration = pk_result.body
        elif data.dose_history:
            # Elimination disabled (half_life is 0) — no meaningful concentration.
            pk_result = None
            concentration = None
        else:
            pk_result = None
            concentration = 0.0

        # Midnight rollover detection — entities that need day-boundary
        # recalculation check ``data.midnight_rolled`` in their
        # ``_handle_coordinator_update``.
        midnight_rolled = self._check_midnight(now)

        # Metrics are RETAINED across midnight (v2 date-keyed shape).  A new
        # day simply gets a new date key when the user sets it; today's slider
        # reads ``unknown`` until set because today's key is absent — the same
        # UX as the prior daily-discard clear, but historical days survive for
        # the 365-day medical export.  Old days are dropped only by the
        # retention cutoff at save time.
        return AxDoseLoggerCoordinatorData(
            dose_history=data.dose_history,
            last_dose_time=data.last_dose_time,
            concentration=concentration,
            pk_result=pk_result,
            adherence_overrides=data.adherence_overrides,
            adherence_reset_time=data.adherence_reset_time,
            avg_reset_time=data.avg_reset_time,
            skipped_slots=data.skipped_slots,
            metric_values=data.metric_values,
        )

    def _push_update(self) -> None:
        """Recompute PK and notify listeners instantly (no debounce delay).

        Used by push-based dose events (take, undo, reset, adherence)
        to ensure sensor state updates are visible immediately on the
        card, bypassing the 10-second debounce of async_request_refresh.
        """
        self.async_set_updated_data(self._recompute_data())

    # ------------------------------------------------------------------
    # Periodic refresh — called every 1 minute by the coordinator timer
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> AxDoseLoggerCoordinatorData:
        """Recompute derived state (PK concentration) on every tick."""
        return self._recompute_data()

    def _check_midnight(self, now: datetime) -> bool:
        """Return True if midnight has passed since the last check."""
        if self._last_midnight_check is None:
            self._last_midnight_check = now
            return False
        rolled = now.date() > self._last_midnight_check.date()
        if rolled:
            self._last_midnight_check = now
        return rolled

    # ------------------------------------------------------------------
    # PK parameter helper
    # ------------------------------------------------------------------
    def _build_pk_params(self) -> PKParams:
        """Build a PKParams snapshot from the current config entry."""
        entry = self._entry
        opts = entry.options
        data = entry.data
        return PKParams(
            release_type=data.get("release_type", RELEASE_INSTANT),
            strength=float(opts.get("strength", data.get("strength", 0))),
            half_life=float(opts.get("half_life", data.get("half_life", 0))),
            hours_to_peak=float(opts.get("hours_to_peak", data.get("hours_to_peak", 0.0))),
            bioavailability=float(
                opts.get("bioavailability", data.get("bioavailability", PK_DEFAULTS["bioavailability"]))
            ),
            ir_fraction=float(opts.get("ir_fraction", data.get("ir_fraction", PK_DEFAULTS["ir_fraction"]))),
            zero_order_duration=float(
                opts.get("zero_order_duration", data.get("zero_order_duration", PK_DEFAULTS["zero_order_duration"]))
            ),
            release_half_life=float(
                opts.get("release_half_life", data.get("release_half_life", PK_DEFAULTS["release_half_life"]))
            ),
            lag_time=float(opts.get("lag_time", data.get("lag_time", PK_DEFAULTS["lag_time"]))),
            ir_hours_to_peak=float(
                opts.get("ir_hours_to_peak", data.get("ir_hours_to_peak", PK_DEFAULTS["ir_hours_to_peak"]))
            ),
        )

    # ------------------------------------------------------------------
    # Public API — called by buttons and services
    # ------------------------------------------------------------------
    async def async_take_dose(self, timestamp: datetime | None = None) -> None:
        """
        Record a dose and trigger an immediate refresh.

        ``timestamp`` defaults to ``now``.  The dose strength is read
        from the config entry (supports options-flow changes).
        """
        if timestamp is None:
            timestamp = dt_util.now()

        strength = float(self._entry.options.get("strength", self._entry.data.get("strength", 0)))
        self.data.dose_history.append((timestamp, strength))
        self.data.last_dose_time = timestamp
        self._save()

        # Fire legacy signal so not-yet-migrated sensors still work
        async_dispatcher_send(self.hass, f"pill_taken_{self._entry.entry_id}", timestamp)
        # Fire HA bus event for frontend / automations
        self.hass.bus.async_fire(
            "ax_dose_logger_dose_taken",
            {"entry_id": self._entry.entry_id, "timestamp": timestamp.isoformat()},
        )

        self._push_update()

    async def async_undo_dose(self) -> None:
        """Remove the most recent dose and trigger an immediate refresh."""
        if not self.data.dose_history:
            return
        self.data.dose_history.pop()
        self.data.last_dose_time = self.data.dose_history[-1][0] if self.data.dose_history else None
        self._save()

        # Fire legacy signal
        async_dispatcher_send(self.hass, f"pill_undone_{self._entry.entry_id}")
        self.hass.bus.async_fire(
            "ax_dose_logger_dose_undone",
            {"entry_id": self._entry.entry_id},
        )

        self._push_update()

    async def async_reset(self) -> None:
        """Clear all dose history and trigger an immediate refresh."""
        self.data.dose_history.clear()
        self.data.last_dose_time = None
        self.data.adherence_overrides.clear()
        self.data.adherence_reset_time = None
        self.data.avg_reset_time = None
        self.data.skipped_slots.clear()
        self._save()
        self._save_skipped()
        self._save_adherence()
        # A full history wipe makes the averages anchor meaningless — clear
        # it so the averages re-anchor to the (now empty) history cleanly.
        self._store.schedule_save_averages_reset(self._entry.entry_id, None)

        # Fire legacy signal
        async_dispatcher_send(self.hass, f"pill_reset_{self._entry.entry_id}")

        self._push_update()

    async def async_adherence_reset(self) -> None:
        """Clear adherence-specific state only (no dose history impact).

        Persists the cleared overrides + new reset anchor so a HA restart
        no longer silently resurrects pre-reset overrides (pre-fix the
        reset was lost on every restart because adherence state was not
        persisted at all — see Gap B in the retention plan).
        """
        self.data.adherence_overrides.clear()
        self.data.adherence_reset_time = dt_util.now()
        self._save_adherence()

        # Fire legacy signal
        async_dispatcher_send(self.hass, f"pill_adherence_reset_{self._entry.entry_id}")

        self._push_update()

    async def async_averages_reset(self) -> None:
        """Reset the rolling averages only (no dose history impact).

        Sets a persisted reset anchor; the average sensors clamp their
        effective window start to max(history_start_date, avg_reset_time)
        so pre-reset doses stop counting toward the 7/14/30/365-day
        averages.  Total Doses, Amount in Body (PK), stock, and Adherence %
        are untouched — no dose data is deleted.
        """
        self.data.avg_reset_time = dt_util.now()
        self._store.schedule_save_averages_reset(
            self._entry.entry_id, self.data.avg_reset_time.isoformat()
        )

        # Fire legacy signal
        async_dispatcher_send(self.hass, f"pill_averages_reset_{self._entry.entry_id}")

        self._push_update()

    async def async_adherence_override(self) -> None:
        """Mark the most recent missed adherence slot as covered.

        Persists the override so it survives HA restarts (pre-fix every
        override was lost on restart, silently dropping the 365-day
        adherence % after each reboot — see Gap B in the retention plan).
        """
        self.data.adherence_overrides.append(dt_util.now())
        self._save_adherence()

        # Fire legacy signal
        async_dispatcher_send(self.hass, f"pill_adherence_override_{self._entry.entry_id}")
        self.hass.bus.async_fire(
            "ax_dose_logger_adherence_override",
            {"entry_id": self._entry.entry_id},
        )

        self._push_update()

    async def async_skip_dose(self) -> None:
        """Record a deliberately-skipped scheduled dose slot.

        Clears the overdue alarm and advances the next-dose schedule
        WITHOUT logging a dose — PK (Amount in Body), stock (Pills Left /
        Days Left), Total Doses, and Last Dose are all untouched. The
        skipped slot is consumed ONLY by the overdue + next_dose sensors.

        Adherence stays penalized: a skip is not adherence credit. A
        patient on a prescriber-directed skip presses both Skip Dose
        (this method) AND Mark Last Adherence Taken (async_adherence_override)
        — two intentional actions for a deliberate decision.
        """
        now = dt_util.now()
        self.data.skipped_slots.append(now)
        self._save_skipped()

        self.hass.bus.async_fire(
            "ax_dose_logger_dose_skipped",
            {"entry_id": self._entry.entry_id, "timestamp": now.isoformat()},
        )

        self._push_update()

    async def async_add_stock(self, amount: float) -> None:
        """
        Notify the stock entity to add pills.

        Stock management is independent of dose history — this just
        fires the legacy signal so ``PillStockNumber`` can increment.
        """
        async_dispatcher_send(self.hass, f"pill_add_stock_{self._entry.entry_id}", amount)

    # ------------------------------------------------------------------
    # Daily-locked metric API
    # ------------------------------------------------------------------
    async def async_set_metric(self, metric_key: str, value: float, override: bool = False) -> None:
        """
        Set a daily-locked effectiveness metric value.

        Enforces the one-set-per-day rule: if the metric has already been
        logged today and ``override`` is False, raises HomeAssistantError.

        v2 date-keyed shape: ``metric_values[metric_key]`` is a
        ``{"YYYY-MM-DD": float}`` map; today's date key is written (overwriting
        any prior value for today).  Historical days are retained for the
        365-day export window — only the retention cutoff drops old dates.
        """
        today = dt_util.now().date().isoformat()
        dated = self.data.metric_values.get(metric_key)
        if not isinstance(dated, dict):
            dated = {}
            self.data.metric_values[metric_key] = dated

        if today in dated and not override:
            raise HomeAssistantError(
                f"Metric '{metric_key}' already set to {dated[today]} today. Use override to change it."
            )

        dated[today] = float(value)
        self._save_metrics()
        self._push_update()

    def is_metric_logged_today(self, metric_key: str) -> bool:
        """Return True if the metric has been logged today."""
        today = dt_util.now().date().isoformat()
        dated = self.data.metric_values.get(metric_key)
        return isinstance(dated, dict) and today in dated

    def get_metric_value(self, metric_key: str) -> float | None:
        """
        Return the metric value if logged today, else None.

        Returns None for unlogged metrics (entity state will be ``unknown``).
        """
        today = dt_util.now().date().isoformat()
        dated = self.data.metric_values.get(metric_key)
        if isinstance(dated, dict) and today in dated:
            return float(dated[today])
        return None

    # ------------------------------------------------------------------
    # Debounced store persistence (HA-native async_delay_save)
    # ------------------------------------------------------------------
    # Persistence is delegated to ``AxDoseLoggerStore.schedule_save_*``
    # which call ``Store.async_delay_save``. HA's storage layer debounces
    # natively AND flushes any pending delayed save during the stop
    # sequence (``EVENT_HOMEASSISTANT_FINAL_WRITE``), so a restart can
    # never drop a queued write. No bespoke ``async_shutdown`` flush is
    # needed — the base ``DataUpdateCoordinator.async_shutdown`` suffices.
    @callback
    def _save(self) -> None:
        """Serialize current dose history and schedule a debounced store save.

        Prunes the serialized copy to the retention window so the
        ``.storage`` JSON stays bounded; the in-memory list is pruned on
        load, and a 365-day sensor reading the list mid-window still sees
        the full window because the tick never prunes.
        """
        cutoff = retention_cutoff(dt_util.now(), self._retention_days())
        kept = prune_dose_pairs(self.data.dose_history, cutoff)
        serialized = [[ts.isoformat(), strength] for ts, strength in kept]
        self._store.schedule_save_history(self._entry.entry_id, serialized)

    @callback
    def _save_metrics(self) -> None:
        """Serialize current metric values and schedule a debounced store save.

        Prunes the serialized copy to the retention window (date-keyed
        v2 shape) so historical PRO days are retained up to the cutoff and
        older days are dropped, keeping the JSON bounded.
        """
        cutoff_date = retention_cutoff_date(dt_util.now(), self._retention_days())
        kept = prune_metric_dict(self.data.metric_values, cutoff_date)
        self._store.schedule_save_metrics(self._entry.entry_id, kept)

    @callback
    def _save_skipped(self) -> None:
        """Serialize skipped-dose slots and schedule a debounced store save.

        Prunes the serialized copy to the retention window.
        """
        cutoff = retention_cutoff(dt_util.now(), self._retention_days())
        kept = prune_timestamps(self.data.skipped_slots, cutoff)
        serialized = [ts.isoformat() for ts in kept]
        self._store.schedule_save_skipped(self._entry.entry_id, serialized)

    @callback
    def _save_adherence(self) -> None:
        """Serialize adherence overrides + reset time and schedule a debounced save.

        Prunes overrides to the retention window.  ``reset_time`` is a
        single anchor (not a per-day value) and is persisted as-is.
        """
        cutoff = retention_cutoff(dt_util.now(), self._retention_days())
        kept = prune_timestamps(self.data.adherence_overrides, cutoff)
        serialized = [ts.isoformat() for ts in kept]
        reset_iso = (
            self.data.adherence_reset_time.isoformat()
            if self.data.adherence_reset_time is not None
            else None
        )
        self._store.schedule_save_adherence(self._entry.entry_id, serialized, reset_iso)

    # ------------------------------------------------------------------
    # Accessors for entities
    # ------------------------------------------------------------------
    @property
    def dose_history(self) -> list[tuple[datetime, float]]:
        """Direct access to the dose history list (read-only contract)."""
        return self.data.dose_history

    @property
    def last_dose_time(self) -> datetime | None:
        """Timestamp of the most recent dose, or None."""
        return self.data.last_dose_time
