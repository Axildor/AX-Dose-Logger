"""
Coordinators for the Drinks category.

Two coordinator classes live here:

* :class:`DrinkCoordinator` — one per granular drink config entry.  Owns the
  local ``dose_history`` (for per-drink statistics: total, last dose, daily
  averages) and forwards each logged dose to the matching
  :class:`DrinkMasterCoordinator` so the global PK sensor can incorporate it.

* :class:`DrinkMasterCoordinator` — one per substance (caffeine/alcohol),
  created by the Drink Settings singleton entry.  Aggregates *all* doses of
  that substance across every granular drink device and computes the global
  body-mass decay curve.

  Caffeine uses the proven first-order ER Phase 1 math from
  :class:`PKModel` (zero-order absorption over ``drinking_duration`` →
  first-order gut→body → first-order elimination).  Full-history
  recompute on every tick (linear PK → superposition valid).

  Alcohol uses a zero-order elimination incremental simulation
  (Michaelis-Menten saturated elimination is non-linear → cannot use
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
    DOMAIN,
    DRINK_LOW_THRESHOLD,
    DRINK_TYPE_ALCOHOL,
    DRINK_TYPE_CAFFEINE,
    GLOBAL_PK_DEFAULTS,
    LOGGER,
    RELEASE_INSTANT,
)
from .pk_model import PKModel, PKParams, PKResult

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
    """Snapshot of derived state read by granular drink sensors."""

    dose_history: list[tuple[datetime, float]] = field(default_factory=list)
    last_dose_time: datetime | None = None


class DrinkCoordinator(DataUpdateCoordinator[DrinkCoordinatorData]):
    """
    Coordinator for a single granular drink device.

    Owns the local ``dose_history`` (for per-drink statistics) and, on each
    logged drink, forwards the ``dose_strength`` + ``drinking_duration``
    to the matching :class:`DrinkMasterCoordinator`.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: AxDoseLoggerStore,
        master_coordinators: dict[str, DrinkMasterCoordinator],
    ) -> None:
        """Initialize the granular drink coordinator."""
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

    async def _async_setup(self) -> None:
        """Load local dose history from the store on first refresh."""
        dose_history: list[tuple[datetime, float]] = []
        stored = self._store.get_history(self._entry.entry_id)
        for item in stored:
            try:
                ts_str, strength_val = item
                dt = dt_util.parse_datetime(ts_str)
                if dt:
                    dose_history.append((dt, float(strength_val)))
            except ValueError, TypeError, IndexError:
                continue
        last_dose = dose_history[-1][0] if dose_history else None
        self.data = DrinkCoordinatorData(
            dose_history=dose_history,
            last_dose_time=last_dose,
        )
        LOGGER.debug(
            "DrinkCoordinator setup for %s: %d doses loaded",
            self._entry.entry_id,
            len(dose_history),
        )

    async def _async_update_data(self) -> DrinkCoordinatorData:
        """Recompute last_dose_time (dose_history is mutated by API methods)."""
        data = self.data
        last_dose = data.dose_history[-1][0] if data.dose_history else None
        return DrinkCoordinatorData(
            dose_history=data.dose_history,
            last_dose_time=last_dose,
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
        )

    # ------------------------------------------------------------------
    # Lazy master lookup
    # ------------------------------------------------------------------
    def _get_master(self) -> DrinkMasterCoordinator | None:
        """Look up the live master coordinator for this drink's substance.

        Reads from ``hass.data[DOMAIN]["_drink_masters"]`` on each call so
        we always get the current coordinator instance.  This survives
        Drink Settings entry removal + re-creation (new coordinators replace
        the old shut-down ones in the shared dict) and gracefully returns
        ``None`` if the dict is missing (entry fully removed).
        """
        drink_type = self._entry.data.get("drink_type")
        masters = self.hass.data.get(DOMAIN, {}).get("_drink_masters", {})
        return masters.get(drink_type)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def async_log_drink(self, timestamp: datetime | None = None) -> None:
        """Record a drink: update local stats + forward to master coordinator."""
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

        # 1) Local stats
        self.data.dose_history.append((timestamp, dose_strength))
        self.data.last_dose_time = timestamp
        self._save()

        # 2) Forward to the master coordinator for global PK
        master = self._get_master()
        if master is not None:
            await master.async_add_dose(timestamp, dose_strength, drinking_duration_min / 60.0)

        # 3) Bus event for automations
        self.hass.bus.async_fire(
            "ax_dose_logger_drink_taken",
            {
                "entry_id": self._entry.entry_id,
                "drink_type": drink_type,
                "dose_strength": dose_strength,
                "drink_name": self._entry.data.get("name", self._entry.title),
            },
        )

        self._push_update()

    async def async_undo_drink(self) -> None:
        """Undo the most recent local drink and notify the master to undo its dose."""
        if not self.data.dose_history:
            return
        removed = self.data.dose_history.pop()
        self.data.last_dose_time = self.data.dose_history[-1][0] if self.data.dose_history else None
        self._save()

        drink_type = self._entry.data.get("drink_type")
        master = self._get_master()
        if master is not None:
            await master.async_undo_dose()

        self.hass.bus.async_fire(
            "ax_dose_logger_drink_undone",
            {"entry_id": self._entry.entry_id, "drink_type": drink_type},
        )
        self._push_update()

    async def async_reset(self) -> None:
        """Clear all local drink history and notify the master to reset."""
        self.data.dose_history.clear()
        self.data.last_dose_time = None
        self._save()

        master = self._get_master()
        if master is not None:
            await master.async_reset()

        self._push_update()

    def is_within_cooldown(self, now: datetime | None = None) -> bool:
        """Return True if a new drink would violate the cooldown window.

        ``cooldown_window`` is expressed in HOURS (aligned with medicine's
        time-window fields). Previously this was minutes — changed per user
        request for cross-device consistency.
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
        last = self.data.dose_history[-1][0]
        return (now - last) < timedelta(hours=cooldown_h)

    # ------------------------------------------------------------------
    # Debounced store persistence (HA-native async_delay_save)
    # ------------------------------------------------------------------
    # Delegated to ``AxDoseLoggerStore.schedule_save_history`` which calls
    # ``Store.async_delay_save``. HA's storage layer debounces natively and
    # flushes any pending delayed save during the stop sequence, so no
    # bespoke ``async_shutdown`` flush is needed.
    @callback
    def _save(self) -> None:
        """Serialize current dose history and schedule a debounced store save."""
        serialized = [[ts.isoformat(), strength] for ts, strength in self.data.dose_history]
        self._store.schedule_save_history(self._entry.entry_id, serialized)


# =====================================================================
# Per-substance master coordinator
# =====================================================================


@dataclass
class DrinkMasterCoordinatorData:
    """Snapshot of derived state read by the master PK sensor."""

    # Aggregated dose history across all drinks of this substance.
    # Each entry: (datetime, dose_strength, t_dur_hours)
    dose_history: list[tuple[datetime, float, float]] = field(default_factory=list)
    last_dose_time: datetime | None = None
    # Current body mass (mg for caffeine / g for alcohol).
    body_mass: float = 0.0
    # Last PK recompute timestamp (for attribute exposure).
    pk_result: PKResult | None = None
    # Names of granular drinks that have contributed doses (for attribute).
    # Resolved lazily from config entries via the device registry — kept as
    # a set of entry_ids here and translated to names by the sensor.
    contributing_entry_ids: set[str] = field(default_factory=set)
    # Forecasted peak body mass + the wall-clock time it occurs.  Used by
    # ``estimate_time_to_body_mass`` so the Estimated Low Time / Sleep
    # Disruption sensors predict from the calculated peak rather than the
    # still-rising current amount in body (see ``_forecast_caffeine_peak``).
    # For alcohol (instant absorption) peak_body_mass == body_mass and
    # peak_time == the recompute ``now`` — the peak is already in the past.
    peak_body_mass: float = 0.0
    peak_time: datetime | None = None


class DrinkMasterCoordinator(DataUpdateCoordinator[DrinkMasterCoordinatorData]):
    """
    Coordinator aggregating all doses of a single substance (caffeine/alcohol).

    Caffeine uses the ER Phase 1 Bateman math (linear PK → superposition).
    Alcohol uses zero-order elimination incremental simulation.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        substance: str,
        store: AxDoseLoggerStore,
        store_key: str,
        settings_entry: ConfigEntry,
    ) -> None:
        """Initialize the master coordinator for a substance.

        ``settings_entry`` is the Drink Settings singleton config entry.
        Passing it as ``config_entry`` to ``DataUpdateCoordinator`` causes
        HA to register ``async_shutdown`` on the entry's unload hook
        ([`update_coordinator.py:148`](/usr/src/homeassistant/homeassistant/helpers/update_coordinator.py:148)).
        Previously this was omitted, so the master coordinator's shutdown
        was never called and any pending debounced save was dropped on
        every restart — a root cause of drink-master data loss.
        """
        super().__init__(
            hass,
            LOGGER,
            name=f"AX Dose Logger Master ({substance})",
            config_entry=settings_entry,
            update_interval=timedelta(minutes=1),
            always_update=True,
        )
        self._substance = substance
        self._store = store
        self._store_key = store_key
        # PK constants — refreshed from the Drink Settings entry on every recompute.
        self._caffeine_half_life = GLOBAL_PK_DEFAULTS["global_caffeine_half_life"]
        self._caffeine_tmax = GLOBAL_PK_DEFAULTS["global_caffeine_tmax"]
        self._alcohol_elimination_rate = GLOBAL_PK_DEFAULTS["global_alcohol_elimination_rate"]
        # Last decay timestamp — used by alcohol zero-order simulation.
        self._last_decay: datetime | None = None

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

    async def _async_setup(self) -> None:
        """Load aggregated dose history + body mass from the store."""
        stored = self._store.get_drink_master(self._substance)
        doses: list[tuple[datetime, float, float]] = []
        for item in stored.get("doses", []):
            try:
                ts_str, strength_val, t_dur_val = item
                dt = dt_util.parse_datetime(ts_str)
                if dt:
                    doses.append((dt, float(strength_val), float(t_dur_val)))
            except ValueError, TypeError, IndexError:
                continue
        last_dose = doses[-1][0] if doses else None
        body_mass = float(stored.get("body_mass", 0.0))
        last_decay_str = stored.get("last_decay")
        last_decay = dt_util.parse_datetime(last_decay_str) if last_decay_str else None
        self._last_decay = last_decay

        # Rebuild contributing entry-id set from the doses (best-effort;
        # not stored per-dose — see contributing_entry_ids note in dataclass).
        self.data = DrinkMasterCoordinatorData(
            dose_history=doses,
            last_dose_time=last_dose,
            body_mass=body_mass,
        )
        # Forecast the caffeine peak + its wall-clock time so self.data is
        # fully valid (peak_body_mass / peak_time populated) the instant
        # setup returns.  Without this, the cached peak fields stay at the
        # dataclass defaults (0.0 / None) until the first periodic
        # ``_async_update_data`` run, so any sensor push or ``predict_low``
        # REST call in that brief window sees ``peak_time is None`` and
        # returns ``None`` (sensors read ``unknown``; popup stays "Low: …").
        # Calling ``_recompute_data`` here is idempotent — it recomputes the
        # body mass from the loaded dose history (caffeine) or applies the
        # zero-order elimination advance (alcohol) and caches the peak.
        self.data = self._recompute_data()
        LOGGER.debug(
            "DrinkMasterCoordinator setup (%s): %d doses, body=%.2f",
            self._substance,
            len(doses),
            self.data.body_mass,
        )

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> DrinkMasterCoordinatorData:
        """Recompute body mass on every tick."""
        return self._recompute_data()

    def _recompute_data(self) -> DrinkMasterCoordinatorData:
        data = self.data
        now = dt_util.now()

        if self._substance == DRINK_TYPE_CAFFEINE:
            body_mass, pk_result = self._compute_caffeine(data.dose_history, now)
            peak_body_mass, peak_time = self._forecast_caffeine_peak(data.dose_history, now, body_mass)
        else:
            body_mass, pk_result = self._compute_alcohol(data, now)
            # Alcohol absorbs instantly — the peak is the dose moment (now
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
        )

    def _push_update(self) -> None:
        self.async_set_updated_data(self._recompute_data())

    # ------------------------------------------------------------------
    # Caffeine PK — discretized uniform input + IR Bateman superposition
    # ------------------------------------------------------------------
    # Number of mini-boluses per drink for the uniform-absorption
    # approximation.  Higher N = smoother curve at the cost of more
    # Bateman evaluations (N * len(dose_history) per tick).  8 gives a
    # good balance for typical sip durations (5–60 min).
    _CAFFEINE_DISCRETIZATION_N = 8

    def _build_caffeine_ir_params(self) -> PKParams:
        """Build IR Bateman PKParams for caffeine using global constants.

        Caffeine is modeled as a series of instant-release mini-boluses
        spread evenly across ``drinking_duration`` (uniform absorption
        approximation).  Each mini-bolus is absorbed via the standard IR
        Bateman equation (gut → body, first-order).  Linear PK → the total
        body mass is the exact superposition of all mini-boluses.
        """
        return PKParams(
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

    def _compute_caffeine(
        self,
        dose_history: list[tuple[datetime, float, float]],
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
        n = self._CAFFEINE_DISCRETIZATION_N
        total_body = 0.0
        total_gut = 0.0
        for dose_time, strength, t_dur in dose_history:
            mini_strength = strength / n
            dt_step = max(t_dur, 1e-9) / n  # hours between mini-boluses
            for i in range(n):
                mini_time = dose_time + timedelta(hours=i * dt_step)
                result = PKModel.compute(params, [(mini_time, mini_strength)], now)
                total_body += result.body
                total_gut += result.gut_ir
        pk_result = PKResult(
            body=total_body,
            gut_ir=total_gut,
            matrix_sr=0.0,
            gut_sr=0.0,
            ka=0.0,  # not meaningful in the aggregate; sensors don't expose it
            kr=0.0,
        )
        return total_body, pk_result

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
    # to locate the maximum.  The window is short (typically 0.25–2 h), so the
    # sweep is ≤ ~24 evaluations and is shared across all estimate callers via
    # the cached dataclass fields (not recomputed per call).
    _CAFFEINE_PEAK_SAMPLE_STEP = timedelta(minutes=5)

    def _forecast_caffeine_peak(
        self,
        dose_history: list[tuple[datetime, float, float]],
        now: datetime,
        current_mass: float,
    ) -> tuple[float, datetime]:
        """Return ``(peak_body_mass, peak_time)`` for the caffeine curve.

        When every dose is fully absorbed (``now`` past the absorption window)
        the peak is in the past, so ``(current_mass, now)`` is returned — the
        downstream exponential-tail estimate is then mathematically identical
        to the prior current-mass-anchored behaviour (backward compatible).
        """
        if not dose_history:
            return current_mass, now

        # Latest mini-bolus peak time = dose_time + drinking_duration + t_max.
        # The last mini-bolus is emitted at dose_time + (N-1)/N * t_dur; using
        # the full t_dur is a safe upper bound and keeps the window inclusive.
        t_max = self._caffeine_tmax
        peak_window_end = max(
            dose_time + timedelta(hours=t_dur + t_max) for dose_time, _strength, t_dur in dose_history
        )
        if peak_window_end <= now:
            # All doses absorbed — the current mass is the post-peak value.
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
        return peak_mass, peak_time

    # ------------------------------------------------------------------
    # Alcohol PK — zero-order elimination incremental simulation
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
    ) -> None:
        """Add a dose from a granular drink to the aggregated history."""
        self.data.dose_history.append((timestamp, dose_strength, t_dur_hours))
        self.data.last_dose_time = timestamp

        if self._substance == DRINK_TYPE_ALCOHOL:
            # Instant absorption for alcohol — add to body immediately,
            # then let the next tick handle elimination.
            self.data.body_mass += dose_strength

        self._save()
        self._push_update()

    async def async_undo_dose(self) -> None:
        """Undo the most recent aggregated dose."""
        if not self.data.dose_history:
            return
        removed = self.data.dose_history.pop()
        removed_strength = removed[1]
        self.data.last_dose_time = self.data.dose_history[-1][0] if self.data.dose_history else None
        if self._substance == DRINK_TYPE_ALCOHOL:
            self.data.body_mass = max(0.0, self.data.body_mass - removed_strength)
        self._save()
        self._push_update()

    async def async_reset(self) -> None:
        """Clear all aggregated history and body mass."""
        self.data.dose_history.clear()
        self.data.last_dose_time = None
        self.data.body_mass = 0.0
        self._last_decay = None
        self._save()
        self._push_update()

    # ------------------------------------------------------------------
    # Debounced store persistence (HA-native async_delay_save)
    # ------------------------------------------------------------------
    # Delegated to ``AxDoseLoggerStore.schedule_save_drink_master`` which
    # calls ``Store.async_delay_save`` on the per-substance Store instance.
    # HA's storage layer debounces natively and flushes any pending delayed
    # save during the stop sequence, so no bespoke ``async_shutdown`` flush
    # is needed. The ``config_entry`` passed to ``super().__init__`` ensures
    # ``async_shutdown`` is registered on the Drink Settings entry's unload
    # hook so the coordinator is properly torn down.
    @callback
    def _save(self) -> None:
        """Serialize current master state and schedule a debounced store save."""
        data = self.data
        serialized = {
            "doses": [[ts.isoformat(), strength, t_dur] for ts, strength, t_dur in data.dose_history],
            "body_mass": data.body_mass,
            "last_decay": self._last_decay.isoformat() if self._last_decay else None,
        }
        self._store.schedule_save_drink_master(self._substance, serialized)

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
    def dose_history(self) -> list[tuple[datetime, float, float]]:
        return self.data.dose_history

    @property
    def last_dose_time(self) -> datetime | None:
        return self.data.last_dose_time

    # ------------------------------------------------------------------
    # Predictive helpers — used by the Sleep Disruption sensor to estimate
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
        exponential estimate — backward compatible.

        Alcohol uses zero-order elimination (linear):  t = (M - target) /
        elimination_rate.  Alcohol absorbs instantly so the peak is the dose
        moment (already past) and the current body_mass is the post-peak
        value — no peak forecast needed.

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
    # What-if prediction — used by the REST predict_low endpoint to show
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
        threshold — the drink would not lift the user above Low, so there is
        no predicted descent (the popup renders "Low: —" in that case).

        Also returns ``None`` when ``self.data`` is not yet populated (master
        coordinator before its first refresh completes or during a reload
        window) — the REST endpoint then returns ``{"low_time": null}`` and
        the popup renders ``Low: —`` instead of hanging on the ``Low: …``
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
                (now, float(dose_strength), float(t_dur_hours)),
            ]
            peak_mass, peak_time = self._forecast_caffeine_peak(hypothetical, now, current_mass)
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
