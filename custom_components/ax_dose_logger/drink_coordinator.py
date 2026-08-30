"""
Coordinators for the Drinks category.

Two coordinator classes live here:

* :class:`DrinkCoordinator` -- one per granular drink config entry.  Owns the
  local ``dose_history`` (for per-drink statistics: total, last dose, daily
  averages) and, on each logged drink, forwards the dose to the matching
  :class:`DrinkMasterCoordinator` for the *effective profile* (the profile
  whose PK curve should change).  In the M2M decoupled topology a drink is a
  global inventory node with an ``allowed_profiles`` array; the
  ``log_drink`` service's ``target_profile`` argument selects which profile
  receives the PK payload (see plans/m2m-decoupled-topology-plan.md).

* :class:`DrinkMasterCoordinator` -- one per (profile_id, substance), created
  by the Drink Settings entry for that profile.  Aggregates *all* doses routed
  to that profile + substance and computes the per-profile body-mass decay
  curve.

  Caffeine uses the proven first-order ER Phase 1 math from
  :class:`PKModel` (zero-order absorption over ``drinking_duration`` ->
  first-order gut->body -> first-order elimination).  Full-history
  recompute on every tick (linear PK -> superposition valid).

  Alcohol uses a zero-order elimination incremental simulation
  (Michaelis-Menten saturated elimination is non-linear -> cannot use
  superposition).  State (``body_mass`` + ``last_decay``) is persisted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DEFAULT_PROFILE_ID,
    DEVICE_CATEGORY_DRINK_SETTINGS,
    DOMAIN,
    DRINK_LOW_THRESHOLD,
    DRINK_TYPE_ALCOHOL,
    DRINK_TYPE_CAFFEINE,
    GLOBAL_PK_DEFAULTS,
    LOGGER,
    RELEASE_INSTANT,
    RETENTION_DAYS,
)
from .pk_model import PKModel, PKParams, PKResult
from .retention import (
    prune_dose_triples,
    retention_cutoff,
)
from .sliding_window import effective_dose_buffer_minutes

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .store import AxDoseLoggerStore

__all__ = [
    "DrinkCoordinator",
    "DrinkCoordinatorData",
    "DrinkMasterCoordinator",
    "DrinkMasterCoordinatorData",
]


# =====================================================================
# Granular per-drink coordinator
# =====================================================================


@dataclass
class DrinkCoordinatorData:
    """Snapshot of derived state read by granular drink sensors.

    ``dose_history`` entries are 3-tuples ``(timestamp, dose_strength,
    effective_profile_id)`` where ``effective_profile_id`` is the immutable
    profile id (UUID, or ``DEFAULT_PROFILE_ID``) whose master received the PK
    payload, or ``None`` for a pure-inventory log (no PK routing).  The 3rd
    element enables correct per-dose undo/reset routing in the M2M topology.
    Old 2-element store data is read defensively (``item[2] if len > 2``).
    """

    dose_history: list[tuple[datetime, float, str | None]] = field(default_factory=list)
    last_dose_time: datetime | None = None
    # Averages reset anchor (Reset Averages tool) — does not affect
    # dose_history / PK / stock.  Average sensors clamp their effective
    # window start to max(history_start_date, avg_reset_time).
    avg_reset_time: datetime | None = None


class DrinkCoordinator(DataUpdateCoordinator[DrinkCoordinatorData]):
    """
    Coordinator for a single granular drink device (a global inventory node).

    Owns the local ``dose_history`` (for per-drink statistics) and, on each
    logged drink, forwards the ``dose_strength`` + ``drinking_duration`` to
    the :class:`DrinkMasterCoordinator` of the *effective profile*.  The
    effective profile is resolved from the ``log_drink`` service's
    ``target_profile`` argument (validated against the drink's
    ``allowed_profiles`` array), with a single-profile convenience default
    when ``allowed_profiles`` has exactly one entry and ``target_profile`` is
    omitted.  See plans/m2m-decoupled-topology-plan.md.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: AxDoseLoggerStore,
        master_coordinators: dict[tuple[str, str], DrinkMasterCoordinator],
    ) -> None:
        """Initialize the granular drink coordinator.

        ``master_coordinators`` is the shared 2D ``_drink_masters`` dict keyed
        by ``(profile_id, substance)``; the coordinator resolves the right
        master per-dose at log time (the target profile varies per log in the
        M2M topology), so it does NOT bind a single master at construction.
        """
        super().__init__(
            hass,
            LOGGER,
            name=f"AX Dose Logger Drink ({entry.title})",
            config_entry=entry,
            update_interval=timedelta(minutes=1),
            always_update=True,
        )
        self._entry = entry
        self._store = store
        self._masters = master_coordinators
        # E3: cached Drink Settings entry reference so ``_retention_days``
        # doesn't scan all config entries on every save (see _retention_days).
        self._settings_entry_cache: ConfigEntry | None = None

    async def _async_setup(self) -> None:
        """Load local dose history from the store on first refresh.

        Prunes the loaded list to the universal drinks retention window so
        an installation that previously ran unbounded frees RAM on load.

        The stored entries may be 2-element ``[ts, strength]`` (legacy) or
        3-element ``[ts, strength, effective_profile_id]`` (M2M).  The load
        path reads defensively: ``effective_profile_id = item[2] if len(item)
        > 2 else None``.  ``None`` means native/default routing (identical to
        the pre-M2M behavior), so single-user installs are unchanged.
        """
        cutoff = retention_cutoff(dt_util.now(), self._retention_days())
        dose_history: list[tuple[datetime, float, str | None]] = []
        stored = self._store.get_history(self._entry.entry_id)
        for item in stored:
            try:
                ts_str = item[0]
                strength_val = item[1]
                dt = dt_util.parse_datetime(ts_str)
                if dt is None:
                    continue
                # Defensive 3rd-element read: legacy 2-element entries -> None
                # (native/default routing), M2M 3-element -> the profile id.
                eff_profile: str | None = item[2] if len(item) > 2 else None
                if eff_profile is not None and not isinstance(eff_profile, str):
                    eff_profile = None
                dose_history.append((dt, float(strength_val), eff_profile))
            except ValueError, TypeError, IndexError:
                continue
        dose_history = prune_dose_triples(dose_history, cutoff)
        # Sort-on-load: legacy stores may contain backdated doses written
        # before ordering was enforced; keep the chronological invariant so
        # ``[-1]`` is always the most recent dose.
        dose_history.sort(key=lambda dose: dose[0])
        last_dose = dose_history[-1][0] if dose_history else None
        # Averages reset anchor (Reset Averages tool).  Forward-only: a
        # pre-fix installation has no averages store, so the anchor loads
        # as None — averages keep their full history until explicitly reset.
        raw_averages = self._store.get_averages_reset(self._entry.entry_id)
        avg_reset_time: datetime | None = None
        if isinstance(raw_averages, dict):
            avg_reset_str = raw_averages.get("reset_time")
            if isinstance(avg_reset_str, str):
                avg_reset_time = dt_util.parse_datetime(avg_reset_str)
        self.data = DrinkCoordinatorData(
            dose_history=dose_history,
            last_dose_time=last_dose,
            avg_reset_time=avg_reset_time,
        )
        LOGGER.debug(
            "DrinkCoordinator setup for %s: %d doses loaded (retention=%dd)",
            self._entry.entry_id,
            len(dose_history),
            self._retention_days(),
        )

    async def _async_update_data(self) -> DrinkCoordinatorData:
        """Recompute last_dose_time (dose_history is mutated by API methods)."""
        data = self.data
        last_dose = data.dose_history[-1][0] if data.dose_history else None
        return DrinkCoordinatorData(
            dose_history=data.dose_history,
            last_dose_time=last_dose,
            avg_reset_time=data.avg_reset_time,
        )

    def _push_update(self) -> None:
        """Notify listeners instantly (no debounce)."""
        self.async_set_updated_data(self._async_update_data_sync())

    def _async_update_data_sync(self) -> DrinkCoordinatorData:
        data = self.data
        last_dose = data.dose_history[-1][0] if data.dose_history else None
        return DrinkCoordinatorData(
            dose_history=data.dose_history,
            last_dose_time=last_dose,
            avg_reset_time=data.avg_reset_time,
        )

    # ------------------------------------------------------------------
    # M2M routing helpers
    # ------------------------------------------------------------------
    def _allowed_profiles(self) -> list[str]:
        """Return this drink's allowed_profiles array (mutable, options-first).

        The options flow writes ``allowed_profiles`` to ``entry.options``
        (see ``async_step_drink_options``), so read options first and fall
        back to ``entry.data`` for pre-migration entries that only carry it
        in data.  Defaults to ``[DEFAULT_PROFILE_ID]`` for pre-migration
        entries so single-user installs route to the legacy default master
        unchanged.
        """
        allowed = self._entry.options.get("allowed_profiles", self._entry.data.get("allowed_profiles"))
        if not allowed:
            return [DEFAULT_PROFILE_ID]
        return list(allowed)

    def _native_profile_id(self) -> str:
        """Return the drink's native profile id (first allowed, or default).

        Used as the ``device_owner_id`` telemetry field (whose inventory
        decremented) and as the convenience-default routing target when
        ``allowed_profiles`` has exactly one entry and ``target_profile`` is
        omitted.  For pre-migration entries this is ``DEFAULT_PROFILE_ID``.
        """
        allowed = self._allowed_profiles()
        return allowed[0] if allowed else DEFAULT_PROFILE_ID

    def _get_master_for(self, profile_id: str) -> DrinkMasterCoordinator | None:
        """Look up the live master coordinator for (profile_id, this drink's substance).

        Reads from ``hass.data[DOMAIN]["_drink_masters"]`` on each call so we
        always get the current coordinator instance (survives Drink Settings
        entry removal + re-creation).  Returns ``None`` if the master is
        missing (the profile was deleted -- dead-pointer guard, Fault 1); the
        caller logs a warning and skips the PK forward rather than crashing.
        """
        drink_type = self._entry.data.get("drink_type")
        masters = self.hass.data.get(DOMAIN, {}).get("_drink_masters", {})
        return masters.get((profile_id, drink_type))

    def _resolve_effective_profile(self, target_profile: str | None) -> str | None:
        """Resolve which profile receives the PK payload for this log.

        Routing decision (M2M topology, see plan section 2.2):
        * ``target_profile`` provided -> validate it is in ``allowed_profiles``
          (else raise ``HomeAssistantError``); use it.
        * omitted + exactly one allowed profile -> convenience default to it.
        * omitted + multiple allowed profiles -> raise (must disambiguate;
          the frontend card always passes ``target_profile``).
        * omitted + zero allowed profiles -> ``None`` (pure inventory tracker,
          no PK routing).

        Returns the effective profile id (or ``None`` for pure-inventory).
        Raises ``HomeAssistantError`` on invalid / ambiguous input.
        """
        from homeassistant.core import HomeAssistantError

        allowed = self._allowed_profiles()
        if target_profile is not None:
            if target_profile not in allowed:
                raise HomeAssistantError(
                    f"Profile '{target_profile}' is not in this drink's "
                    f"allowed_profiles {allowed}; cannot route PK payload."
                )
            return target_profile
        # target_profile omitted
        if len(allowed) == 0:
            return None  # pure inventory tracker
        if len(allowed) == 1:
            return allowed[0]  # convenience default
        raise HomeAssistantError(
            f"This drink is shared by multiple profiles; specify target_profile (allowed_profiles={allowed})."
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def async_log_drink(
        self,
        timestamp: datetime | None = None,
        *,
        target_profile: str | None = None,
    ) -> None:
        """Record a drink: update local stats + forward PK to the effective profile's master.

        M2M routing (see ``_resolve_effective_profile``):
        * ``target_profile`` selects which profile's master receives the PK
          payload (validated against ``allowed_profiles``).
        * The local inventory (dose_history + stock) always decrements,
          independent of which profile receives the PK payload.

        Telemetry (Fault 2): the ``ax_dose_logger_drink_taken`` bus event
        carries ``device_owner_id`` (the drink's native profile id -- whose
        inventory decremented) and ``target_profile_id`` (the effective
        profile -- whose PK curve changed) so automations can disambiguate.
        """
        if timestamp is None:
            timestamp = dt_util.now()

        dose_strength = float(
            self._entry.options.get(
                "dose_strength",
                self._entry.data.get("dose_strength", 0),
            )
        )
        drinking_duration_min = float(
            self._entry.options.get(
                "drinking_duration",
                self._entry.data.get("drinking_duration", 15),
            )
        )
        drink_type = self._entry.data.get("drink_type")

        # Resolve the effective profile (raises HomeAssistantError on invalid
        # / ambiguous input before any state mutation).
        effective_profile = self._resolve_effective_profile(target_profile)

        # 1) Local stats (always, regardless of PK routing).
        self.data.dose_history.append((timestamp, dose_strength, effective_profile))
        # Sort-on-insert: the service accepts an explicit (possibly backdated)
        # ``timestamp``, so keep dose_history chronologically ordered and set
        # ``last_dose_time`` to the true most-recent dose -- never blindly to
        # the inserted timestamp.
        self.data.dose_history.sort(key=lambda dose: dose[0])
        self.data.last_dose_time = self.data.dose_history[-1][0]
        self._save()

        # 2) Forward the PK payload to the effective profile's master.
        if effective_profile is not None:
            master = self._get_master_for(effective_profile)
            if master is None:
                # Fault 1 dead-pointer guard: the profile's master is gone
                # (profile deleted between service validation and this call --
                # a narrow race, or the drink was reassigned but target_profile
                # still references an old id).  Log a warning and skip the PK
                # forward; the local inventory decrement + bus event still
                # complete so the user sees the drink logged.
                LOGGER.warning(
                    "Profile %s has no live master for %s; PK routing aborted "
                    "(profile may have been deleted). Local inventory still updated.",
                    effective_profile,
                    drink_type,
                )
            else:
                await master.async_add_dose(
                    timestamp,
                    dose_strength,
                    drinking_duration_min / 60.0,
                    # B1 provenance tagging: record which drink entry
                    # contributed this dose so a per-drink reset can
                    # surgically remove only its own master-side doses.
                    source_entry_id=self._entry.entry_id,
                )

        # 3) Bus event for automations (Fault 2 telemetry).
        self.hass.bus.async_fire(
            "ax_dose_logger_drink_taken",
            {
                "entry_id": self._entry.entry_id,
                "drink_type": drink_type,
                "dose_strength": dose_strength,
                "drink_name": self._entry.data.get("name", self._entry.title),
                # device_owner_id: whose inventory decremented (the drink's
                # native profile -- the first allowed profile, or default).
                "device_owner_id": self._native_profile_id(),
                # target_profile_id: whose PK curve changed (the effective
                # profile; None for pure-inventory logs with no PK routing).
                "target_profile_id": effective_profile,
            },
        )

        self._push_update()

    async def async_undo_drink(self) -> None:
        """Undo the most recent local drink and notify the correct profile's master.

        Per-dose routing (M2M): the popped dose's ``effective_profile_id``
        records which profile received the PK payload, so undo routes to
        *that* profile's master (not the drink's native profile).  Dead-pointer
        guard (Fault 1): if that master is gone, log + skip the PK undo; the
        local stats undo still completes.
        """
        if not self.data.dose_history:
            return
        history = self.data.dose_history
        # Pop the max-timestamp entry (not blindly the last element) so
        # undoing after a backdated insert removes the true latest drink.
        max_idx = max(range(len(history)), key=lambda i: history[i][0])
        removed = history.pop(max_idx)
        effective_profile = removed[2] if len(removed) > 2 else None
        # Re-sort so the chronological invariant holds for legacy unsorted data.
        history.sort(key=lambda dose: dose[0])
        self.data.last_dose_time = history[-1][0] if history else None
        self._save()

        drink_type = self._entry.data.get("drink_type")
        # Route to the profile that actually received the dose.  If the
        # recorded effective_profile is None (legacy/pre-M2M or pure-inventory
        # dose), fall back to the drink's native profile.
        undo_profile = effective_profile or self._native_profile_id()
        master = self._get_master_for(undo_profile)
        if master is None:
            LOGGER.warning(
                "Profile %s has no live master for %s; PK undo aborted "
                "(profile may have been deleted). Local stats undo completed.",
                undo_profile,
                drink_type,
            )
        else:
            # B1 surgical undo: remove exactly this one dose by provenance
            # (newest-first match on source_entry_id) instead of popping the
            # master's most-recent dose, which may belong to another drink.
            # Legacy untagged doses fall back to pop-newest inside
            # async_remove_doses (with a warning), so old stores still undo.
            await master.async_remove_doses(self._entry.entry_id, 1)

        self.hass.bus.async_fire(
            "ax_dose_logger_drink_undone",
            {
                "entry_id": self._entry.entry_id,
                "drink_type": drink_type,
                "device_owner_id": self._native_profile_id(),
                "target_profile_id": undo_profile,
            },
        )
        self._push_update()

    async def async_reset(self) -> None:
        """Clear all local drink history and surgically undo this drink's PK contributions.

        M2M per-dose targeted undo (Option R1): instead of calling one
        master's ``async_reset()`` (which would wipe an entire profile's
        substance curve and leave split-routed doses orphaned in other
        profiles), iterate the local ``dose_history`` and call
        ``async_undo_dose()`` on each dose's effective-profile master.  This
        surgically removes only this drink's contributions from each affected
        profile, leaving other drinks' doses intact.  Dead-pointer guard
        (Fault 1) per dose: a missing master is logged + skipped.
        """
        drink_type = self._entry.data.get("drink_type")
        # Build a per-profile count of THIS drink's doses so each master can
        # surgically remove exactly those doses (B1 provenance): each master
        # dose tuple carries the contributing ``source_entry_id``, so
        # ``async_remove_doses`` removes only this drink's entries
        # (newest-first) and leaves other drinks' interleaved doses -- and
        # their body-mass contributions -- intact.  Legacy untagged doses
        # fall back to pop-newest inside ``async_remove_doses`` (with a
        # warning), so pre-B1 stores never crash or under-reset.
        per_profile_counts: dict[str, int] = {}
        for dose in self.data.dose_history:
            eff = dose[2] if len(dose) > 2 else None
            prof = eff or self._native_profile_id()
            per_profile_counts[prof] = per_profile_counts.get(prof, 0) + 1

        self.data.dose_history.clear()
        self.data.last_dose_time = None
        self._save()

        for profile_id, count in per_profile_counts.items():
            master = self._get_master_for(profile_id)
            if master is None:
                LOGGER.warning(
                    "Profile %s has no live master for %s; %d PK undo(s) aborted "
                    "(profile may have been deleted). Local reset completed.",
                    profile_id,
                    drink_type,
                    count,
                )
                continue
            await master.async_remove_doses(self._entry.entry_id, count)

        self.hass.bus.async_fire(
            "ax_dose_logger_drink_reset",
            {
                "entry_id": self._entry.entry_id,
                "drink_type": drink_type,
            },
        )
        self._push_update()

    async def async_averages_reset(self) -> None:
        """Reset this granular drink's rolling averages only (no history impact).

        Sets a persisted reset anchor; the DrinkAvgDosesSensor instances
        clamp their effective window start to
        max(history_start_date, avg_reset_time) so pre-reset drinks stop
        counting toward the 7/14/30/365-day averages.  Total drinks, PK
        (body mass), stock, and the master's aggregate averages are
        untouched — no drink data is deleted.
        """
        self.data.avg_reset_time = dt_util.now()
        self._store.schedule_save_averages_reset(self._entry.entry_id, self.data.avg_reset_time.isoformat())
        self._push_update()

    def is_within_cooldown(self, now: datetime | None = None) -> bool:
        """Return True if a new drink would violate the cooldown window.

        ``cooldown_window`` is expressed in HOURS (aligned with medicine's
        time-window fields). Previously this was minutes -- changed per user
        request for cross-device consistency.

        The cooldown is relaxed by the anti-drift dose buffer
        (:func:`effective_dose_buffer_minutes`): the last drink expires
        ``buffer`` minutes earlier than the strict cooldown, so gradual
        drink-timing drift is bounded (mirrors the medicine pill_limit gate).
        """
        cooldown_h = float(
            self._entry.options.get(
                "cooldown_window",
                self._entry.data.get("cooldown_window", 0),
            )
        )
        if cooldown_h <= 0 or not self.data.dose_history:
            return False
        if now is None:
            now = dt_util.now()
        buffer_minutes = effective_dose_buffer_minutes(self._entry, cooldown_h)
        last = self.data.dose_history[-1][0]
        return (now - last) < timedelta(hours=cooldown_h) - timedelta(minutes=buffer_minutes)

    # ------------------------------------------------------------------
    # Retention window -- inherited from the Drink Settings singleton
    # ------------------------------------------------------------------
    def _retention_days(self) -> int:
        """Return the universal drinks retention window from Drink Settings.

        Granular drink entries do NOT carry their own ``retention_days`` (the
        slider lives only in the Drink Settings singleton).  E3: the resolved
        singleton entry reference is cached and re-validated with a cheap
        O(1) registry lookup instead of scanning every config entry on each
        call (``_save`` invokes this on every debounced save).  The cached
        entry's ``options`` are still read live, so a Drink Settings
        options-flow change takes effect without restarting the granular
        coordinator; a removed/replaced singleton forces exactly one re-scan.
        Falls back to :data:`RETENTION_DAYS` if the singleton is somehow
        absent or the key is missing.
        """
        entry = getattr(self, "_settings_entry_cache", None)
        if entry is not None and self.hass.config_entries.async_get_entry(entry.entry_id) is None:
            # The cached singleton was removed (e.g. entry re-creation on
            # reload) — force exactly one re-scan.
            entry = None
            self._settings_entry_cache = None
        if entry is None:
            for candidate in self.hass.config_entries.async_entries(DOMAIN):
                if candidate.data.get("device_category") == DEVICE_CATEGORY_DRINK_SETTINGS:
                    entry = candidate
                    self._settings_entry_cache = candidate
                    break
        if entry is None:
            return RETENTION_DAYS
        val = entry.options.get(
            "retention_days",
            entry.data.get("retention_days", RETENTION_DAYS),
        )
        try:
            return max(1, int(val))
        except TypeError, ValueError:
            return RETENTION_DAYS

    # ------------------------------------------------------------------
    # Debounced store persistence (HA-native async_delay_save)
    # ------------------------------------------------------------------
    # Delegated to ``AxDoseLoggerStore.schedule_save_history`` which calls
    # ``Store.async_delay_save``. HA's storage layer debounces natively and
    # flushes any pending delayed save during the stop sequence, so no
    # bespoke ``async_shutdown`` flush is needed.
    @callback
    def _save(self) -> None:
        """Serialize current dose history and schedule a debounced store save.

        Prunes the serialized copy to the universal drinks retention window
        so the ``.storage`` JSON stays bounded.  Writes the 3-element form
        ``[ts, strength, effective_profile_id]``; old 2-element entries
        round-trip as the 3-element form with ``None`` (no data loss).
        """
        cutoff = retention_cutoff(dt_util.now(), self._retention_days())
        kept = prune_dose_triples(self.data.dose_history, cutoff)
        serialized = [[ts.isoformat(), strength, eff] for ts, strength, eff in kept]
        self._store.schedule_save_history(self._entry.entry_id, serialized)


# =====================================================================
# Per-(profile, substance) master coordinator
# =====================================================================


@dataclass
class DrinkMasterCoordinatorData:
    """Snapshot of derived state read by the master PK sensor."""

    # Aggregated dose history routed to this (profile, substance).
    # Each entry: (datetime, dose_strength, t_dur_hours, source_entry_id)
    # where ``source_entry_id`` is the granular drink config entry that
    # contributed the dose (B1 provenance tagging) so a per-drink reset can
    # surgically remove only its own doses.  Legacy 3-element store rows
    # load defensively with ``source_entry_id = None`` (unknown contributor).
    dose_history: list[tuple[datetime, float, float, str | None]] = field(default_factory=list)
    last_dose_time: datetime | None = None
    # Current body mass (mg for caffeine / g for alcohol).
    body_mass: float = 0.0
    # Last PK recompute timestamp (for attribute exposure).
    pk_result: PKResult | None = None
    # Names of granular drinks that have contributed doses (for attribute).
    # Resolved lazily from config entries via the device registry -- kept as
    # a set of entry_ids here and translated to names by the sensor.
    # Per-dose provenance now also lives in ``dose_history``'s
    # ``source_entry_id`` column (B1); this set remains for the attribute.
    contributing_entry_ids: set[str] = field(default_factory=set)
    # Forecasted peak body mass + the wall-clock time it occurs.  Used by
    # ``estimate_time_to_body_mass`` so the Estimated Low Time / Sleep
    # Disruption sensors predict from the calculated peak rather than the
    # still-rising current amount in body (see ``_forecast_caffeine_peak``).
    # For alcohol (instant absorption) peak_body_mass == body_mass and
    # peak_time == the recompute ``now`` -- the peak is already in the past.
    peak_body_mass: float = 0.0
    peak_time: datetime | None = None
    # Averages reset anchor (Reset Averages tool) — does not affect
    # dose_history / PK / stock.  The master's average sensors clamp their
    # effective window start to max(history_start_date, avg_reset_time).
    avg_reset_time: datetime | None = None


class DrinkMasterCoordinator(DataUpdateCoordinator[DrinkMasterCoordinatorData]):
    """
    Coordinator aggregating all doses routed to a single (profile, substance).

    One instance per (profile_id, substance).  Created by the Drink Settings
    entry for that profile; the immutable ``profile_id`` keys the store file
    and the Master Tracker device identifiers.

    Caffeine uses the ER Phase 1 Bateman math (linear PK -> superposition).
    Alcohol uses zero-order elimination incremental simulation.
    """

    def __init__(  # noqa: PLR0913 - constructor mirrors the (profile, substance) key tuple + shared store wiring
        self,
        hass: HomeAssistant,
        profile_id: str,
        substance: str,
        store: AxDoseLoggerStore,
        store_key: str,
        settings_entry: ConfigEntry,
    ) -> None:
        """Initialize the master coordinator for one (profile, substance).

        ``profile_id`` is the immutable profile identifier (UUID for named
        profiles, ``DEFAULT_PROFILE_ID`` for the legacy singleton).  It keys
        the per-profile store file (``store_key``) and is stored on the
        coordinator for log messages + store-save routing.

        ``settings_entry`` is the Drink Settings config entry for this profile.
        Passing it as ``config_entry`` to ``DataUpdateCoordinator`` causes
        HA to register ``async_shutdown`` on the entry's unload hook
        ([`update_coordinator.py:148`](/usr/src/homeassistant/homeassistant/helpers/update_coordinator.py:148)).
        Previously this was omitted, so the master coordinator's shutdown
        was never called and any pending debounced save was dropped on
        every restart -- a root cause of drink-master data loss.
        """
        super().__init__(
            hass,
            LOGGER,
            name=f"AX Dose Logger Master (profile={profile_id}, {substance})",
            config_entry=settings_entry,
            update_interval=timedelta(minutes=1),
            always_update=True,
        )
        self._profile_id = profile_id
        self._substance = substance
        self._store = store
        self._store_key = store_key
        # PK constants -- refreshed from the Drink Settings entry on every recompute.
        self._caffeine_half_life = GLOBAL_PK_DEFAULTS["global_caffeine_half_life"]
        self._caffeine_tmax = GLOBAL_PK_DEFAULTS["global_caffeine_tmax"]
        self._alcohol_elimination_rate = GLOBAL_PK_DEFAULTS["global_alcohol_elimination_rate"]
        # Last decay timestamp -- used by alcohol zero-order simulation.
        self._last_decay: datetime | None = None
        # Caffeine peak forecast cache -- the forecasted (peak_mass, peak_time)
        # is stationary between dose events (it doesn't move on a 1-min tick
        # unless a new dose arrives or the absorption window ends).  Caching it
        # avoids re-sweeping the full dose history x 8 mini-boluses x 5-min
        # steps on every tick during the absorption window (~99% CPU reduction
        # during that window).  Invalidated by: dose count change, last dose
        # timestamp change, or the cached peak time passing into the past.
        self._cached_peak_mass: float | None = None
        self._cached_peak_time: datetime | None = None
        self._cached_peak_dose_count: int = -1
        self._cached_peak_last_dose_time: datetime | None = None
        # E2: cached caffeine IR PKParams (derived from the constants above)
        # + pre-discretized mini-bolus list, so repeated ``_compute_caffeine``
        # calls (peak sweep ~24x/refresh, graph sampling up to 400 samples)
        # don't re-derive them on every call.  The params are invalidated by
        # ``update_global_constants``; the bolus list is keyed on a
        # (dose count, last dose timestamp) fingerprint.
        self._cached_caffeine_ir_params: PKParams | None = None
        self._caffeine_bolus_cache: tuple[tuple[int, datetime], list[tuple[datetime, float]]] | None = None

    # ------------------------------------------------------------------
    # Identity accessors (for sensors + views)
    # ------------------------------------------------------------------
    @property
    def profile_id(self) -> str:
        """The immutable profile id this master belongs to."""
        return self._profile_id

    # ------------------------------------------------------------------
    # Global PK constant refresh (called by __init__.py when Drink Settings loads/saves)
    # ------------------------------------------------------------------
    def update_global_constants(self, settings_entry: ConfigEntry) -> None:
        """Refresh global PK constants from the Drink Settings config entry."""
        opts = settings_entry.options
        data = settings_entry.data
        self._caffeine_half_life = float(
            opts.get(
                "global_caffeine_half_life",
                data.get("global_caffeine_half_life", GLOBAL_PK_DEFAULTS["global_caffeine_half_life"]),
            )
        )
        self._caffeine_tmax = float(
            opts.get(
                "global_caffeine_tmax", data.get("global_caffeine_tmax", GLOBAL_PK_DEFAULTS["global_caffeine_tmax"])
            )
        )
        self._alcohol_elimination_rate = float(
            opts.get(
                "global_alcohol_elimination_rate",
                data.get("global_alcohol_elimination_rate", GLOBAL_PK_DEFAULTS["global_alcohol_elimination_rate"]),
            )
        )
        # E2: the cached IR PKParams derive from the constants above.
        self._cached_caffeine_ir_params = None

    async def _async_setup(self) -> None:
        """Load aggregated dose history + body mass from the store.

        Prunes the loaded dose list to the universal drinks retention window.
        PK-safe: caffeine (linear PK) contributes <1% after 5 half-lives
        (~25h) so 365-day pruning is irrelevant; alcohol (incremental
        zero-order from persisted ``body_mass`` + ``last_decay``) does not
        recompute from history at all, so pruning is a no-op for it.
        """
        cutoff = retention_cutoff(dt_util.now(), self._retention_days())
        stored = self._store.get_drink_master(self._profile_id, self._substance)
        doses: list[tuple[datetime, float, float, str | None]] = []
        for item in stored.get("doses", []):
            try:
                dt = dt_util.parse_datetime(item[0])
                if dt:
                    # Defensive 4th-element read (B1 provenance): legacy
                    # 3-element store rows -> None (unknown contributor),
                    # mirroring the M2M effective_profile_id pattern.
                    source_entry_id = item[3] if len(item) > 3 else None
                    if source_entry_id is not None and not isinstance(source_entry_id, str):
                        source_entry_id = None
                    doses.append((dt, float(item[1]), float(item[2]), source_entry_id))
            except ValueError, TypeError, IndexError:
                continue
        doses = prune_dose_triples(doses, cutoff)
        # Sort-on-load: legacy stores may contain backdated doses written
        # before ordering was enforced; keep the chronological invariant so
        # ``[-1]`` is always the most recent dose.
        doses.sort(key=lambda dose: dose[0])
        last_dose = doses[-1][0] if doses else None
        body_mass = float(stored.get("body_mass", 0.0))
        last_decay_str = stored.get("last_decay")
        last_decay = dt_util.parse_datetime(last_decay_str) if last_decay_str else None
        self._last_decay = last_decay

        # Rebuild contributing entry-id set from the doses (best-effort;
        # not stored per-dose -- see contributing_entry_ids note in dataclass).
        # Averages reset anchor (Reset Averages tool).  Forward-only: a
        # pre-fix installation has no averages store, so the anchor loads
        # as None — averages keep their full history until explicitly reset.
        master_key = f"master::{self._profile_id}::{self._substance}"
        raw_averages = self._store.get_averages_reset(master_key)
        avg_reset_time: datetime | None = None
        if isinstance(raw_averages, dict):
            avg_reset_str = raw_averages.get("reset_time")
            if isinstance(avg_reset_str, str):
                avg_reset_time = dt_util.parse_datetime(avg_reset_str)
        self.data = DrinkMasterCoordinatorData(
            dose_history=doses,
            last_dose_time=last_dose,
            body_mass=body_mass,
            avg_reset_time=avg_reset_time,
        )
        # Forecast the caffeine peak + its wall-clock time so self.data is
        # fully valid (peak_body_mass / peak_time populated) the instant
        # setup returns.  Without this, the cached peak fields stay at the
        # dataclass defaults (0.0 / None) until the first periodic
        # ``_async_update_data`` run, so any sensor push or ``predict_low``
        # REST call in that brief window sees ``peak_time is None`` and
        # returns ``None`` (sensors read ``unknown``; popup stays "Low: ...").
        # Calling ``_recompute_data`` here is idempotent -- it recomputes the
        # body mass from the loaded dose history (caffeine) or applies the
        # zero-order elimination advance (alcohol) and caches the peak.
        self.data = self._recompute_data()
        LOGGER.debug(
            "DrinkMasterCoordinator setup (profile=%s, %s): %d doses, body=%.2f",
            self._profile_id,
            self._substance,
            len(doses),
            self.data.body_mass,
        )

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> DrinkMasterCoordinatorData:
        """Recompute body mass on every tick - offloaded to the executor (CPU-bound).

        ``_recompute_data`` is synchronous, CPU-bound work (caffeine: N=8 x
        len(history) Bateman evaluations per tick; alcohol: O(1) incremental
        arithmetic).  For a heavy caffeine user with 100+ drinks this is
        50-200 ms of pure-Python math that would block the HA event loop on
        every 1-min tick, causing latency spikes for ALL automations and
        entity updates.  HA best practice is to offload CPU-bound work to the
        executor thread pool so the event loop stays free during the
        computation.  ``_recompute_data`` is effectively read-only (it reads
        ``self.data``, constructs a new dataclass, and returns it - no
        mutation of shared state), so running it in a thread is safe.
        """
        return await self.hass.async_add_executor_job(self._recompute_data)

    def _recompute_data(self) -> DrinkMasterCoordinatorData:
        data = self.data
        now = dt_util.now()

        if self._substance == DRINK_TYPE_CAFFEINE:
            body_mass, pk_result = self._compute_caffeine(data.dose_history, now)
            peak_body_mass, peak_time = self._forecast_caffeine_peak(data.dose_history, now, body_mass)
        else:
            body_mass, pk_result = self._compute_alcohol(data, now)
            # Alcohol absorbs instantly -- the peak is the dose moment (now
            # in the past) and the current body_mass is the post-peak value.
            peak_body_mass = body_mass
            peak_time = now

        return DrinkMasterCoordinatorData(
            dose_history=data.dose_history,
            last_dose_time=data.last_dose_time,
            body_mass=body_mass,
            pk_result=pk_result,
            contributing_entry_ids=data.contributing_entry_ids,
            peak_body_mass=peak_body_mass,
            peak_time=peak_time,
            avg_reset_time=data.avg_reset_time,
        )

    def _push_update(self) -> None:
        """Schedule recompute + notify - fire-and-forget via executor.

        Used by push-based dose events (add / undo / reset) to ensure sensor
        state updates are visible promptly, bypassing the debounce of
        ``async_request_refresh``.  The recompute is CPU-bound (caffeine path)
        so it is offloaded to the executor to avoid blocking the event loop.
        The notification (``async_set_updated_data``) runs on the event loop
        after the executor thread completes.
        """
        self.hass.async_create_task(self._async_push_update())

    async def _async_push_update(self) -> None:
        """Executor-offloaded recompute + listener notification."""
        data = await self.hass.async_add_executor_job(self._recompute_data)
        self.async_set_updated_data(data)

    # ------------------------------------------------------------------
    # Caffeine PK -- discretized uniform input + IR Bateman superposition
    # ------------------------------------------------------------------
    # Number of mini-boluses per drink for the uniform-absorption
    # approximation.  Higher N = smoother curve at the cost of more
    # Bateman evaluations (N * len(dose_history) per tick).  8 gives a
    # good balance for typical sip durations (5-60 min).
    _CAFFEINE_DISCRETIZATION_N = 8

    def _build_caffeine_ir_params(self) -> PKParams:
        """Build IR Bateman PKParams for caffeine using global constants.

        Caffeine is modeled as a series of instant-release mini-boluses
        spread evenly across ``drinking_duration`` (uniform absorption
        approximation).  Each mini-bolus is absorbed via the standard IR
        Bateman equation (gut -> body, first-order).  Linear PK -> the total
        body mass is the exact superposition of all mini-boluses.

        E2: the params derive only from the global PK constants, so they are
        cached and re-derived only when ``update_global_constants`` changes
        them (the ``getattr`` default keeps the standalone simulation
        scripts, which bypass ``__init__``, working).
        """
        cached = getattr(self, "_cached_caffeine_ir_params", None)
        if cached is not None:
            return cached
        params = PKParams(
            release_type=RELEASE_INSTANT,
            strength=0,  # per-dose strengths come from dose_history
            half_life=self._caffeine_half_life,
            hours_to_peak=self._caffeine_tmax,
            bioavailability=100,
            ir_fraction=100,
            zero_order_duration=0,
            release_half_life=0,
            lag_time=0,
            ir_hours_to_peak=0,
        )
        self._cached_caffeine_ir_params = params
        return params

    def _compute_caffeine(
        self,
        dose_history: list[tuple[datetime, float, float, str | None]],
        now: datetime,
    ) -> tuple[float, PKResult | None]:
        """Compute total caffeine body mass via discretized uniform input.

        Each drink is split into N mini-boluses spread evenly across its
        ``drinking_duration`` (zero-order absorption approximation).  Each
        mini-bolus is absorbed via the IR Bateman equation using the global
        caffeine half-life and tmax.  The total body mass is the exact
        superposition of all mini-boluses from all drinks (linear PK).
        """
        if not dose_history:
            return 0.0, None
        params = self._build_caffeine_ir_params()
        # E2: one batched superposition over the pre-discretized mini-bolus
        # list instead of N x len(history) separate ``PKModel.compute`` calls
        # (each of which previously re-derived a fresh single-bolus list).
        # Linear PK -> the batched result is mathematically identical (same
        # summation order).
        boluses = self._caffeine_mini_boluses(dose_history)
        result = PKModel.compute(params, boluses, now)
        pk_result = PKResult(
            body=result.body,
            gut_ir=result.gut_ir,
            matrix_sr=0.0,
            gut_sr=0.0,
            ka=0.0,  # not meaningful in the aggregate; sensors don't expose it
            kr=0.0,
        )
        return result.body, pk_result

    def _caffeine_mini_boluses(
        self,
        dose_history: list[tuple[datetime, float, float, str | None]],
    ) -> list[tuple[datetime, float]]:
        """Return the pre-discretized mini-bolus list for ``dose_history`` (E2).

        Each drink is split into N mini-boluses spread evenly across its
        ``drinking_duration``.  The list is cached against a
        ``(dose count, last dose timestamp)`` fingerprint -- the same
        stationarity contract as the peak-forecast cache -- so the peak sweep
        (~24 calls/refresh) and graph sampling (up to 400 calls) reuse it.

        The cache is only written when ``dose_history`` IS the live shared
        history (``self.data.dose_history``): the what-if throwaway list in
        :meth:`predict_low_time_if_dose` is a different object, so a
        hypothetical bolus list can never pollute the shared cache (C2
        hygiene).  ``getattr`` defaults keep the standalone scripts (which
        bypass ``__init__``) working.
        """
        n = self._CAFFEINE_DISCRETIZATION_N
        fingerprint = (len(dose_history), dose_history[-1][0])
        cached = getattr(self, "_caffeine_bolus_cache", None)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        boluses: list[tuple[datetime, float]] = []
        # ``*_`` tolerates legacy 3-element dose tuples (provenance is
        # irrelevant to the PK math).
        for dose_time, strength, t_dur, *_ in dose_history:
            mini_strength = strength / n
            dt_step = max(t_dur, 1e-9) / n  # hours between mini-boluses
            for i in range(n):
                boluses.append((dose_time + timedelta(hours=i * dt_step), mini_strength))
        if dose_history is getattr(self.data, "dose_history", None):
            self._caffeine_bolus_cache = (fingerprint, boluses)
        return boluses

    # ------------------------------------------------------------------
    # Caffeine peak forecast (absorption-aware Estimated Low Time anchor)
    # ------------------------------------------------------------------
    # The Estimated Low Time / Sleep Disruption sensors predict the wall-clock
    # moment the body-mass decays into a lower band.  Anchoring that estimate
    # at the *current* body mass is only valid once absorption has finished
    # (post-peak exponential tail); during absorption the mass is still rising
    # toward a future peak, so the estimate would climb on every 1-min tick
    # ("counts up until the caffeine peaks").  Instead we forecast the peak
    # body mass + its wall-clock time once per refresh and cache it on the
    # dataclass; ``estimate_time_to_body_mass`` then anchors at the peak.
    #
    # The absorption window ends at the latest mini-bolus peak time across all
    # doses (drinking_duration + caffeine t_max).  We sample the deterministic
    # ``_compute_caffeine`` curve at a coarse 5-min step up to that window end
    # to locate the maximum.  The window is short (typically 0.25-2 h), so the
    # sweep is <= ~24 evaluations and is shared across all estimate callers via
    # the cached dataclass fields (not recomputed per call).
    _CAFFEINE_PEAK_SAMPLE_STEP = timedelta(minutes=5)

    def _forecast_caffeine_peak(
        self,
        dose_history: list[tuple[datetime, float, float, str | None]],
        now: datetime,
        current_mass: float,
        cache: dict[str, object] | None = None,
    ) -> tuple[float, datetime]:
        """Return ``(peak_body_mass, peak_time)`` for the caffeine curve.

        When every dose is fully absorbed (``now`` past the absorption window)
        the peak is in the past, so ``(current_mass, now)`` is returned -- the
        downstream exponential-tail estimate is then mathematically identical
        to the prior current-mass-anchored behaviour (backward compatible).

        ``cache`` selects the cache backend: ``None`` (default) uses the
        shared instance fields (the 1-min tick path), while a dict isolates
        the sweep in a caller-local cache -- the what-if path
        (:meth:`predict_low_time_if_dose`) passes a throwaway dict so a
        hypothetical peak can never leak into the shared cache (C2: the REST
        view runs the what-if on the executor concurrently with the tick's
        recompute, and two threads mutating the same fields is a data race).
        """

        def _cache_get(key: str, default: object) -> object:
            if cache is None:
                return getattr(self, key, default)
            return cache.get(key, default)

        def _cache_set(key: str, value: object) -> None:
            if cache is None:
                setattr(self, key, value)
            else:
                cache[key] = value

        if not dose_history:
            return current_mass, now

        # Cache hit: the dose history is unchanged (same count + same last
        # dose timestamp) AND the cached peak is still in the future.  The
        # forecasted peak doesn't move between ticks unless a new dose arrives
        # or the absorption window ends, so this avoids re-sweeping the full
        # history x 8 mini-boluses x 5-min steps on every 1-min tick during
        # the absorption window (~99% CPU reduction during that window).
        #
        # ``getattr`` defaults handle the standalone simulation scripts which
        # bypass ``__init__`` (via ``__new__``) and don't set the cache fields.
        last_dose_time = dose_history[-1][0]
        cached_peak_time = _cache_get("_cached_peak_time", None)
        if (
            len(dose_history) == _cache_get("_cached_peak_dose_count", -1)
            and last_dose_time == _cache_get("_cached_peak_last_dose_time", None)
            and cached_peak_time is not None
            and cached_peak_time > now
        ):
            return _cache_get("_cached_peak_mass", current_mass), cached_peak_time

        # Latest mini-bolus peak time = dose_time + drinking_duration + t_max.
        # The last mini-bolus is emitted at dose_time + (N-1)/N * t_dur; using
        # the full t_dur is a safe upper bound and keeps the window inclusive.
        t_max = self._caffeine_tmax
        peak_window_end = max(
            dose_time + timedelta(hours=t_dur + t_max) for dose_time, _strength, t_dur, *_ in dose_history
        )
        if peak_window_end <= now:
            # All doses absorbed -- the current mass is the post-peak value.
            # Cache this (cheap) result so subsequent ticks also hit the cache.
            _cache_set("_cached_peak_mass", current_mass)
            _cache_set("_cached_peak_time", now)
            _cache_set("_cached_peak_dose_count", len(dose_history))
            _cache_set("_cached_peak_last_dose_time", last_dose_time)
            return current_mass, now

        # Sample the deterministic PK curve from `now` to `peak_window_end`.
        peak_mass = current_mass
        peak_time = now
        step = self._CAFFEINE_PEAK_SAMPLE_STEP
        sample_time = now
        while sample_time <= peak_window_end:
            sample_mass, _ = self._compute_caffeine(dose_history, sample_time)
            if sample_mass > peak_mass:
                peak_mass = sample_mass
                peak_time = sample_time
            sample_time += step
        # Always evaluate the window end exactly (the loop may step past it).
        end_mass, _ = self._compute_caffeine(dose_history, peak_window_end)
        if end_mass > peak_mass:
            peak_mass = end_mass
            peak_time = peak_window_end
        # Cache the sweep result so subsequent ticks during the absorption
        # window return immediately without re-sweeping.
        _cache_set("_cached_peak_mass", peak_mass)
        _cache_set("_cached_peak_time", peak_time)
        _cache_set("_cached_peak_dose_count", len(dose_history))
        _cache_set("_cached_peak_last_dose_time", last_dose_time)
        return peak_mass, peak_time

    # ------------------------------------------------------------------
    # Alcohol PK -- zero-order elimination incremental simulation
    # ------------------------------------------------------------------
    def _compute_alcohol(self, data: DrinkMasterCoordinatorData, now: datetime) -> tuple[float, PKResult | None]:
        """Zero-order elimination: body -= rate * elapsed; doses add instantly.

        State (body_mass + last_decay) is persisted.  The 1-min tick advances
        the elimination; async_add_dose adds instantly and recomputes.
        """
        body = data.body_mass
        last_decay = self._last_decay or data.last_dose_time
        if last_decay is not None:
            elapsed_hours = (now - last_decay).total_seconds() / 3600.0
            if elapsed_hours > 0:
                body -= self._alcohol_elimination_rate * elapsed_hours
                if body < 0:
                    body = 0.0
        self._last_decay = now
        # No PKResult structure for alcohol (zero-order, not Bateman).
        # Expose a minimal PKResult for attribute consistency.
        pk_result = PKResult(
            body=body,
            gut_ir=0.0,
            matrix_sr=0.0,
            gut_sr=0.0,
            ka=0.0,
            kr=0.0,
        )
        return body, pk_result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def async_add_dose(
        self,
        timestamp: datetime,
        dose_strength: float,
        t_dur_hours: float,
        source_entry_id: str | None = None,
    ) -> None:
        """Add a dose from a granular drink to this profile's aggregated history.

        ``source_entry_id`` (B1 provenance tagging) records which granular
        drink config entry contributed the dose so a per-drink reset can
        surgically remove only its own contributions via
        ``async_remove_doses`` instead of popping the newest dose blindly.
        """
        self.data.dose_history.append((timestamp, dose_strength, t_dur_hours, source_entry_id))
        # Sort-on-insert: a backdated drink log must not corrupt
        # ``last_dose_time`` -- it always reflects the true most-recent dose.
        self.data.dose_history.sort(key=lambda dose: dose[0])
        self.data.last_dose_time = self.data.dose_history[-1][0]

        if self._substance == DRINK_TYPE_ALCOHOL:
            # Instant absorption for alcohol -- add to body immediately,
            # then let the next tick handle elimination.
            self.data.body_mass += dose_strength

        self._save()
        self._push_update()

    async def async_undo_dose(self) -> None:
        """Undo the most recent aggregated dose."""
        if not self.data.dose_history:
            return
        history = self.data.dose_history
        # Pop the max-timestamp entry (not blindly the last element) so
        # undoing after a backdated insert removes the true latest dose.
        max_idx = max(range(len(history)), key=lambda i: history[i][0])
        removed = history.pop(max_idx)
        removed_strength = removed[1]
        # Re-sort so the chronological invariant holds for legacy unsorted data.
        history.sort(key=lambda dose: dose[0])
        self.data.last_dose_time = history[-1][0] if history else None
        if self._substance == DRINK_TYPE_ALCOHOL:
            self.data.body_mass = max(0.0, self.data.body_mass - removed_strength)
        self._save()
        self._push_update()

    async def async_remove_doses(self, source_entry_id: str | None, count: int) -> int:
        """Surgically remove up to ``count`` doses contributed by ``source_entry_id``.

        B1 surgical reset: unlike :meth:`async_undo_dose` (which pops the
        master's most-recent dose regardless of contributor), this removes
        only doses tagged with the calling drink's ``source_entry_id``,
        newest-first, so interleaved drinks never destroy each other's PK
        state.  For alcohol, each removed dose's strength is subtracted from
        ``body_mass`` (mirroring ``async_undo_dose``).

        Legacy fallback: doses written before provenance tagging have
        ``source_entry_id = None`` and cannot be matched.  When fewer than
        ``count`` tagged doses exist, the remainder is removed with the
        pre-B1 pop-newest behavior (with a warning) so old stores never
        crash or silently under-reset.

        Returns the number of doses actually removed.
        """
        if count <= 0 or not self.data.dose_history:
            return 0
        history = self.data.dose_history
        removed_total = 0

        # Pass 1: newest-first, remove only doses tagged with this source.
        matching = [i for i, dose in enumerate(history) if len(dose) > 3 and dose[3] == source_entry_id]
        matching.sort(key=lambda i: history[i][0], reverse=True)
        for idx in matching[:count]:
            removed = history.pop(idx)
            if self._substance == DRINK_TYPE_ALCOHOL:
                self.data.body_mass = max(0.0, self.data.body_mass - removed[1])
            removed_total += 1

        # Pass 2 (legacy fallback): the calling drink contributed more doses
        # than the tagged rows can account for -- the remainder predates
        # provenance tagging (3-element rows, source None).  Fall back to the
        # pre-B1 behavior: pop the newest doses regardless of contributor.
        remaining = count - removed_total
        if remaining > 0:
            LOGGER.warning(
                "Master (profile=%s, %s): %d of %d doses from entry %s have no "
                "provenance (legacy pre-B1 store rows); falling back to "
                "pop-newest removal for the remainder.",
                self._profile_id,
                self._substance,
                remaining,
                count,
                source_entry_id,
            )
            for _ in range(remaining):
                if not history:
                    break
                max_idx = max(range(len(history)), key=lambda i: history[i][0])
                removed = history.pop(max_idx)
                if self._substance == DRINK_TYPE_ALCOHOL:
                    self.data.body_mass = max(0.0, self.data.body_mass - removed[1])
                removed_total += 1

        if removed_total:
            # Re-sort so the chronological invariant holds after the pops.
            history.sort(key=lambda dose: dose[0])
            self.data.last_dose_time = history[-1][0] if history else None
            self._save()
            self._push_update()
        return removed_total

    async def async_reset(self) -> None:
        """Clear all aggregated history and body mass for this (profile, substance)."""
        self.data.dose_history.clear()
        self.data.last_dose_time = None
        self.data.body_mass = 0.0
        self._last_decay = None
        self._save()
        self._push_update()

    async def async_averages_reset(self) -> None:
        """Reset this substance's aggregate rolling averages only (no history impact).

        Sets a persisted reset anchor; the DrinkMasterAvgDosesSensor
        instances clamp their effective window start to
        max(history_start_date, avg_reset_time) so pre-reset drinks (across
        ALL granular drinks of this substance) stop counting toward the
        7/14/30/365-day aggregate averages.  Body mass (PK), per-drink
        totals, and granular averages are untouched — no drink data is
        deleted.
        """
        self.data.avg_reset_time = dt_util.now()
        self._store.schedule_save_averages_reset(
            f"master::{self._profile_id}::{self._substance}",
            self.data.avg_reset_time.isoformat(),
        )
        self._push_update()

    # ------------------------------------------------------------------
    # Debounced store persistence (HA-native async_delay_save)
    # ------------------------------------------------------------------
    # Delegated to ``AxDoseLoggerStore.schedule_save_drink_master`` which
    # calls ``Store.async_delay_save`` on the per-(profile, substance) Store
    # instance.  HA's storage layer debounces natively and flushes any
    # pending delayed save during the stop sequence, so no bespoke
    # ``async_shutdown`` flush is needed. The ``config_entry`` passed to
    # ``super().__init__`` ensures ``async_shutdown`` is registered on the
    # Drink Settings entry's unload hook so the coordinator is properly torn
    # down.
    def _retention_days(self) -> int:
        """Return the universal drinks retention window from Drink Settings.

        The master coordinator already receives the Drink Settings entry as
        its ``config_entry`` (passed to ``DataUpdateCoordinator`` so shutdown
        is registered on the singleton's unload hook), so we read
        ``retention_days`` directly from it.  Falls back to
        :data:`RETENTION_DAYS` if the key is missing.
        """
        entry = self.config_entry
        val = entry.options.get(
            "retention_days",
            entry.data.get("retention_days", RETENTION_DAYS),
        )
        try:
            return max(1, int(val))
        except TypeError, ValueError:
            return RETENTION_DAYS

    @callback
    def _save(self) -> None:
        """Serialize current master state and schedule a debounced store save.

        Prunes the persisted ``doses`` list to the retention window.  Note
        that alcohol does NOT recompute body-mass from history (incremental
        zero-order simulation from ``body_mass`` + ``last_decay``), so
        pruning old alcohol doses is a PK no-op; caffeine (linear PK,
        superposition) contributes <1% after 5 half-lives (~25h) so pruning
        at 365 days is also PK-irrelevant.  See retention.py for the full
        PK-safety rationale.
        """
        cutoff = retention_cutoff(dt_util.now(), self._retention_days())
        kept = prune_dose_triples(self.data.dose_history, cutoff)
        serialized = {
            "doses": [
                # 4-element form (B1): [iso, strength, t_dur, source_entry_id].
                [d[0].isoformat(), d[1], d[2], d[3] if len(d) > 3 else None]
                for d in kept
            ],
            "body_mass": self.data.body_mass,
            "last_decay": self._last_decay.isoformat() if self._last_decay else None,
        }
        self._store.schedule_save_drink_master(self._profile_id, self._substance, serialized)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    @property
    def substance(self) -> str:
        return self._substance

    @property
    def body_mass(self) -> float:
        return self.data.body_mass

    @property
    def dose_history(self) -> list[tuple[datetime, float, float, str | None]]:
        return self.data.dose_history

    @property
    def last_dose_time(self) -> datetime | None:
        return self.data.last_dose_time

    # ------------------------------------------------------------------
    # Predictive helpers -- used by the Sleep Disruption sensor to estimate
    # how long until the body-mass decays into a lower band.
    # ------------------------------------------------------------------
    def estimate_time_to_body_mass(self, target: float) -> timedelta | None:
        """Estimate the time for ``body_mass`` to decay to ``target``.

        Caffeine predicts from the **forecasted peak** (see
        ``_forecast_caffeine_peak``): ``total_eta = time_to_peak +
        ln(peak_mass / target) / ke`` where ``ke = ln(2) / half_life``.
        Anchoring at the peak (rather than the still-rising current body
        mass) keeps the estimate stable through the absorption phase instead
        of climbing on every 1-min tick.  Once the peak has passed
        (``peak_time <= now``) the formula reduces to the prior pure-tail
        exponential estimate -- backward compatible.

        Alcohol uses zero-order elimination (linear):  t = (M - target) /
        elimination_rate.  Alcohol absorbs instantly so the peak is the dose
        moment (already past) and the current body_mass is the post-peak
        value -- no peak forecast needed.

        Returns ``None`` when the target is already met (``peak_body_mass <=
        target`` for caffeine / ``body_mass <= target`` for alcohol) or when
        the relevant PK constant is unavailable / zero.
        """
        if self._substance == DRINK_TYPE_CAFFEINE:
            peak_mass = self.data.peak_body_mass
            peak_time = self.data.peak_time
            if peak_time is None or peak_mass <= target:
                return None
            half_life = self._caffeine_half_life
            if not half_life or half_life <= 0:
                return None
            ke = math.log(2) / half_life  # per hour
            if ke <= 0:
                return None
            now = dt_util.now()
            time_to_peak = peak_time - now
            if time_to_peak.total_seconds() < 0:
                time_to_peak = timedelta(0)
            decay_hours = math.log(peak_mass / target) / ke
            if decay_hours < 0:
                return None
            return time_to_peak + timedelta(hours=decay_hours)
        if self._substance == DRINK_TYPE_ALCOHOL:
            mass = self.data.body_mass
            if mass <= target:
                return None
            rate = self._alcohol_elimination_rate
            if not rate or rate <= 0:
                return None
            hours = (mass - target) / rate
            if hours < 0:
                return None
            return timedelta(hours=hours)
        return None

    # ------------------------------------------------------------------
    # What-if prediction -- used by the REST predict_low endpoint to show
    # the predicted Low-band timestamp in the Log Drink popup BEFORE the
    # user commits to a drink.  Pure function: does NOT mutate self.data.
    # ------------------------------------------------------------------
    def predict_low_time_if_dose(self, dose_strength: float, t_dur_hours: float) -> datetime | None:
        """Predict the wall-clock time body-mass would enter the Low band if a
        hypothetical dose were logged now.

        Builds a throwaway dose list (current history + the new dose) and
        forecasts the peak + Low-band ETA from it.  ``self.data`` is never
        mutated, so a user who closes the popup without pressing the drink
        button has no side effect on the real coordinator state.

        Caffeine: forecasts the post-dose peak (``_forecast_caffeine_peak``
        already accepts a ``dose_history`` param) then applies the same
        ``time_to_peak + ln(peak_mass / low_threshold) / ke`` formula as
        :meth:`estimate_time_to_body_mass`.

        Alcohol: instant absorption means the post-dose body mass is
        ``current_body + strength``; ETA is linear zero-order elimination.

        Returns ``None`` when the post-dose peak/body never exceeds the Low
        threshold -- the drink would not lift the user above Low, so there is
        no predicted descent (the popup renders "Low: -" in that case).

        Also returns ``None`` when ``self.data`` is not yet populated (master
        coordinator before its first refresh completes or during a reload
        window) -- the REST endpoint then returns ``{"low_time": null}`` and
        the popup renders ``Low: -`` instead of hanging on the ``Low: ...``
        loading placeholder that an ``AttributeError`` 500 would produce.
        """
        if self.data is None:
            return None
        target = DRINK_LOW_THRESHOLD.get(self._substance)
        if target is None:
            return None
        now = dt_util.now()

        if self._substance == DRINK_TYPE_CAFFEINE:
            # Current body mass from a fresh recompute (cheap; the 1-min tick
            # already keeps self.data fresh, but recompute guarantees the
            # hypothetical peak is anchored at the live curve, not a stale
            # cached body_mass that may predate the last tick).
            current_mass, _ = self._compute_caffeine(self.data.dose_history, now)
            hypothetical = [
                *self.data.dose_history,
                (now, float(dose_strength), float(t_dur_hours), None),
            ]
            # C2: pass a throwaway cache dict so the hypothetical sweep never
            # writes the shared ``_cached_peak_*`` instance fields -- the REST
            # view runs this on the executor concurrently with the tick's
            # recompute, and a cached hypothetical peak would corrupt the
            # sensor's real forecast (data race).
            peak_mass, peak_time = self._forecast_caffeine_peak(hypothetical, now, current_mass, cache={})
            if peak_time is None or peak_mass <= target:
                return None
            half_life = self._caffeine_half_life
            if not half_life or half_life <= 0:
                return None
            ke = math.log(2) / half_life  # per hour
            if ke <= 0:
                return None
            time_to_peak = peak_time - now
            if time_to_peak.total_seconds() < 0:
                time_to_peak = timedelta(0)
            decay_hours = math.log(peak_mass / target) / ke
            if decay_hours < 0:
                return None
            return now + time_to_peak + timedelta(hours=decay_hours)

        if self._substance == DRINK_TYPE_ALCOHOL:
            post_mass = self.data.body_mass + float(dose_strength)
            if post_mass <= target:
                return None
            rate = self._alcohol_elimination_rate
            if not rate or rate <= 0:
                return None
            hours = (post_mass - target) / rate
            if hours < 0:
                return None
            return now + timedelta(hours=hours)

        return None

    # ------------------------------------------------------------------
    # Graph sampling (recorder-independent body-mass line graph)
    # ------------------------------------------------------------------
    def sample_body_mass_curve(
        self,
        start: datetime,
        end: datetime,
        points: int,
    ) -> list[tuple[datetime, float]]:
        """Sample the master body-mass curve at ``points`` evenly spaced instants.

        Serves the card's Amount-in-Body line graph for Master Tracker
        devices from the integration's own store instead of the HA recorder
        (whose ``purge_keep_days`` default of 10 days silently truncates
        long timeframes).

        Called from the REST graph view via ``hass.async_add_executor_job``
        (CPU-bound for caffeine). Read-only: never mutates ``self.data``.

        - **Caffeine**: exact per-sample recompute via ``_compute_caffeine``
          (linear PK superposition — deterministic, same as the 1-min tick).
        - **Alcohol**: exact segment-wise forward simulation of the
          zero-order model (linear decay, clamp at 0, instant dose
          additions) walked between the union of dose times and sample
          times — no approximation, identical to the live 1-min tick model.
          Seeding at 0 before the oldest retained dose is exact: any mass
          from doses older than the retention window has already fully
          eliminated (365 days × the elimination rate dwarfs any dose), so
          no residual offset exists — the curve ends exactly at the live
          sensor value.

        Returns ``[(timestamp, value), ...]`` with ``points`` samples from
        ``start`` to ``end`` inclusive, or ``[]`` when there is no history.
        """
        data = self.data
        if not data or not data.dose_history:
            return []

        n = max(2, min(int(points), 400))
        span = (end - start).total_seconds()

        if self._substance == DRINK_TYPE_CAFFEINE:
            # E1: 10-half-life prune (mirrors the medicine
            # ``sample_amount_curve``): doses older than ~10 half-lives
            # before the window start contribute <0.1% — drop them so the
            # per-sample recompute loop stays cheap on long histories.  The
            # alcohol branch below needs the FULL history for the forward
            # simulation — prune only the caffeine path.
            decay_horizon = start - timedelta(hours=self._caffeine_half_life * 10)
            relevant = [d for d in data.dose_history if d[0] >= decay_horizon]
            if not relevant:
                return []
            # Hoist params + the pruned mini-bolus list out of the sample
            # loop (E2): one bolus-list build for the whole curve instead of
            # one per sample.
            params = self._build_caffeine_ir_params()
            boluses = self._caffeine_mini_boluses(relevant)
            samples: list[tuple[datetime, float]] = []
            for i in range(n):
                t = start + timedelta(seconds=span * i / (n - 1))
                result = PKModel.compute(params, boluses, t)
                samples.append((t, result.body))
            return samples

        if self._substance == DRINK_TYPE_ALCOHOL:
            rate = self._alcohol_elimination_rate
            if rate <= 0:
                return []
            sample_times = [start + timedelta(seconds=span * i / (n - 1)) for i in range(n)]
            doses = sorted((dose[0], dose[1]) for dose in data.dose_history)
            # Segment-wise forward simulation: between events the body
            # decays linearly (exact for zero-order), doses add instantly,
            # and the clamp at 0 is applied at each segment boundary —
            # mathematically identical to the live incremental model.
            t_sim = min(doses[0][0], start)
            body = 0.0
            di = 0
            samples = []
            for t in sample_times:
                while di < len(doses) and doses[di][0] <= t:
                    t_d, s_d = doses[di]
                    body = max(0.0, body - rate * (t_d - t_sim).total_seconds() / 3600.0)
                    body += s_d
                    t_sim = t_d
                    di += 1
                body = max(0.0, body - rate * (t - t_sim).total_seconds() / 3600.0)
                t_sim = t
                samples.append((t, body))
            return samples

        return []
