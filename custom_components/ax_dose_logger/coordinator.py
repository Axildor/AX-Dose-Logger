"""
PillLoggerCoordinator — single source of truth for dose history.

Owns the authoritative ``dose_history`` list, debounced store saves, and a
single 1-minute refresh interval.  Entities become ``CoordinatorEntity``
subscribers and read ``coordinator.data`` instead of maintaining their own
copies of dose history and listening to dispatcher signals.

During the 1D-1 → 1D-3 transition the coordinator still fires the legacy
dispatcher signals so that not-yet-migrated sensors continue to work.
Once all entities are migrated (1D-2) the signal firing is removed (1D-3).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import LOGGER, PK_DEFAULTS, RELEASE_INSTANT
from .pk_model import PKModel, PKParams, PKResult

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .store import PillLoggerStore

__all__ = ["PillLoggerCoordinator", "PillLoggerCoordinatorData"]

# Debounce window for store saves (seconds).  Rapid doses within this
# window coalesce into a single disk write.
_SAVE_DEBOUNCE_SECONDS = 5.0


@dataclass
class PillLoggerCoordinatorData:
    """
    Snapshot of all derived state that entities read from the coordinator.

    ``dose_history`` is the single in-memory source of truth — entities
    must NOT maintain their own copies.  ``concentration`` and
    ``pk_result`` are recomputed on every refresh so the concentration
    sensor and steady-state sensor can read them directly instead of
    via inter-sensor dispatcher signals.
    """

    dose_history: list[tuple[datetime, float]] = field(default_factory=list)
    last_dose_time: datetime | None = None
    concentration: float = 0.0
    pk_result: PKResult | None = None
    # Adherence-specific state (does not affect dose_history)
    adherence_overrides: list[datetime] = field(default_factory=list)
    adherence_reset_time: datetime | None = None


class PillLoggerCoordinator(DataUpdateCoordinator[PillLoggerCoordinatorData]):
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
        store: PillLoggerStore,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            name=f"Pill Logger ({entry.title})",
            config_entry=entry,
            update_interval=timedelta(minutes=1),
            always_update=True,
        )
        self._entry = entry
        self._store = store
        self._save_handle: asyncio.TimerHandle | None = None
        self._last_midnight_check: datetime | None = None

    # ------------------------------------------------------------------
    # Setup — load dose history from store on first refresh
    # ------------------------------------------------------------------
    async def _async_setup(self) -> None:
        """Load dose history from the store into the coordinator."""
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

        last_dose = dose_history[-1][0] if dose_history else None
        self.data = PillLoggerCoordinatorData(
            dose_history=dose_history,
            last_dose_time=last_dose,
        )
        LOGGER.debug(
            "PillLoggerCoordinator setup for %s: %d doses loaded",
            self._entry.entry_id,
            len(dose_history),
        )

    # ------------------------------------------------------------------
    # Periodic refresh — called every 1 minute by the coordinator timer
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> PillLoggerCoordinatorData:
        """
        Recompute derived state (PK concentration) on every tick.

        This is called both by the 1-minute interval and by
        ``async_request_refresh`` after a dose event.  The dose_history
        list is already up-to-date (mutated by the ``async_*`` API
        methods), so this method only recomputes the derived fields.
        """
        data = self.data
        now = dt_util.now()

        # Recompute PK concentration from full dose history
        params = self._build_pk_params()
        if data.dose_history:
            pk_result = PKModel.compute(params, data.dose_history, now)
            concentration = pk_result.body
        else:
            pk_result = None
            concentration = 0.0

        # Midnight rollover detection — entities that need day-boundary
        # recalculation check ``data.midnight_rolled`` in their
        # ``_handle_coordinator_update``.
        midnight_rolled = self._check_midnight(now)

        return PillLoggerCoordinatorData(
            dose_history=data.dose_history,
            last_dose_time=data.last_dose_time,
            concentration=concentration,
            pk_result=pk_result,
            adherence_overrides=data.adherence_overrides,
            adherence_reset_time=data.adherence_reset_time,
        )

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
            bioavailability=float(opts.get("bioavailability", data.get("bioavailability", PK_DEFAULTS["bioavailability"]))),
            ir_fraction=float(opts.get("ir_fraction", data.get("ir_fraction", PK_DEFAULTS["ir_fraction"]))),
            zero_order_duration=float(opts.get("zero_order_duration", data.get("zero_order_duration", PK_DEFAULTS["zero_order_duration"]))),
            release_half_life=float(opts.get("release_half_life", data.get("release_half_life", PK_DEFAULTS["release_half_life"]))),
            lag_time=float(opts.get("lag_time", data.get("lag_time", PK_DEFAULTS["lag_time"]))),
            ir_hours_to_peak=float(opts.get("ir_hours_to_peak", data.get("ir_hours_to_peak", PK_DEFAULTS["ir_hours_to_peak"]))),
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

        strength = float(
            self._entry.options.get("strength", self._entry.data.get("strength", 0))
        )
        self.data.dose_history.append((timestamp, strength))
        self.data.last_dose_time = timestamp
        self._schedule_save()

        # Fire legacy signal so not-yet-migrated sensors still work
        async_dispatcher_send(self.hass, f"pill_taken_{self._entry.entry_id}", timestamp)
        # Fire HA bus event for frontend / automations
        self.hass.bus.async_fire(
            "pill_logger_pill_taken",
            {"entry_id": self._entry.entry_id, "timestamp": timestamp.isoformat()},
        )

        await self.async_request_refresh()

    async def async_undo_dose(self) -> None:
        """Remove the most recent dose and trigger an immediate refresh."""
        if not self.data.dose_history:
            return
        self.data.dose_history.pop()
        self.data.last_dose_time = (
            self.data.dose_history[-1][0] if self.data.dose_history else None
        )
        self._schedule_save()

        # Fire legacy signal
        async_dispatcher_send(self.hass, f"pill_undone_{self._entry.entry_id}")
        self.hass.bus.async_fire(
            "pill_logger_pill_undone",
            {"entry_id": self._entry.entry_id},
        )

        await self.async_request_refresh()

    async def async_reset(self) -> None:
        """Clear all dose history and trigger an immediate refresh."""
        self.data.dose_history.clear()
        self.data.last_dose_time = None
        self.data.adherence_overrides.clear()
        self.data.adherence_reset_time = None
        self._schedule_save()

        # Fire legacy signal
        async_dispatcher_send(self.hass, f"pill_reset_{self._entry.entry_id}")

        await self.async_request_refresh()

    async def async_adherence_reset(self) -> None:
        """Clear adherence-specific state only (no dose history impact)."""
        self.data.adherence_overrides.clear()
        self.data.adherence_reset_time = dt_util.now()

        # Fire legacy signal
        async_dispatcher_send(self.hass, f"pill_adherence_reset_{self._entry.entry_id}")

        await self.async_request_refresh()

    async def async_adherence_override(self) -> None:
        """Mark the most recent missed adherence slot as covered."""
        self.data.adherence_overrides.append(dt_util.now())

        # Fire legacy signal
        async_dispatcher_send(self.hass, f"pill_adherence_override_{self._entry.entry_id}")
        self.hass.bus.async_fire(
            "pill_logger_adherence_override",
            {"entry_id": self._entry.entry_id},
        )

        await self.async_request_refresh()

    async def async_add_stock(self, amount: float) -> None:
        """
        Notify the stock entity to add pills.

        Stock management is independent of dose history — this just
        fires the legacy signal so ``PillStockNumber`` can increment.
        """
        async_dispatcher_send(
            self.hass, f"pill_add_stock_{self._entry.entry_id}", amount
        )

    # ------------------------------------------------------------------
    # Shutdown — cancel pending save and flush to store
    # ------------------------------------------------------------------
    async def async_shutdown(self) -> None:
        """Cancel pending debounced save and flush dose history to store."""
        if self._save_handle:
            self._save_handle.cancel()
            self._save_handle = None
            # Do a final flush save so no data is lost on unload
            serialized = [
                [ts.isoformat(), strength]
                for ts, strength in self.data.dose_history
            ]
            await self._store.async_set_history(self._entry.entry_id, serialized)
        await super().async_shutdown()

    # ------------------------------------------------------------------
    # Debounced store persistence
    # ------------------------------------------------------------------
    def _schedule_save(self) -> None:
        """Debounce store saves — coalesce rapid doses into one write."""
        if self._save_handle:
            self._save_handle.cancel()
        self._save_handle = self.hass.loop.call_later(
            _SAVE_DEBOUNCE_SECONDS, self._do_save
        )

    @callback
    def _do_save(self) -> None:
        """Persist dose history to the store (called after debounce window)."""
        self._save_handle = None
        serialized = [
            [ts.isoformat(), strength]
            for ts, strength in self.data.dose_history
        ]
        self.hass.async_create_task(
            self._store.async_set_history(self._entry.entry_id, serialized)
        )

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
