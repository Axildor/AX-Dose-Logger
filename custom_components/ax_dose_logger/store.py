"""
Persistent storage for dose history and daily metric data outside entity attributes.

Uses HA's ``storage.Store`` to persist dose history and daily metric values to
JSON files, avoiding SQLite bloat and the 16KB attribute limit.

Also persists aggregated drink-master dose history and zero-order PK state
(caffeine/alcohol) so the Master Tracker sensors can reconstruct their decay
curves across restarts.

All persistence uses ``Store.async_delay_save`` so writes are debounced
natively AND flushed automatically during the HA stop sequence
(``EVENT_HOMEASSISTANT_FINAL_WRITE``). This closes the fire-and-forget
race that previously dropped the last few doses if HA was restarted
before a queued ``async_create_task`` write completed — the root cause
of "Total Doses reverted from 7 to 2 after restart" and the sporadic
14-day bar graph.
"""

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import LOGGER, METRIC_STORE_KEY

STORAGE_VERSION = 1
STORAGE_KEY = "ax_dose_logger_dose_history"

# Skipped-dose slots — deliberate skips persisted so a reboot does not
# re-ring the overdue alarm for a slot the user explicitly skipped.
SKIPPED_STORAGE_VERSION = 1
SKIPPED_STORAGE_KEY = "ax_dose_logger_skipped_slots"

# Adherence overrides + reset time — manual "Mark Last Adherence Taken"
# corrections and treatment-restart anchors.  Persisted separately from
# skipped slots so a 365-day medical export can distinguish patient
# self-report corrections from prescriber-directed schedule skips.
# Shape: { entry_id: { "overrides": ["iso", ...], "reset_time": "iso" | null } }
ADHERENCE_STORAGE_VERSION = 1
ADHERENCE_STORAGE_KEY = "ax_dose_logger_adherence"

# Averages reset anchors — the timestamp of the last "Reset Averages" tool
# action.  Average sensors clamp their effective window start to
# max(history_start_date, reset_time) so doses logged before the reset stop
# counting toward the 7/14/30/365-day averages WITHOUT deleting any dose
# data (Total Doses, PK, stock, and Adherence % are untouched).
# Keys: entry_id for medicine + granular drink entries (both share the
# dose-history store keyed by entry_id); "master::{profile_id}::{substance}"
# for Master Tracker (per-substance aggregate) coordinators.
# Shape: { key: { "reset_time": "iso" | null } }
AVERAGES_STORAGE_VERSION = 1
AVERAGES_STORAGE_KEY = "ax_dose_logger_averages"

# Legacy storage key from the pre-rebrand "pill_logger" domain.
# Kept for the safer migration variant: on first load under the new key,
# if the new key is empty we copy data from the legacy key but do NOT
# delete the legacy file (enables rollback, ~1KB orphaned disk).
_LEGACY_STORAGE_KEY = "pill_logger_dose_history"

# Metric storage v2: date-keyed retention.
#   v1 shape (daily-discard): { entry_id: { metric_key: {"date": "YYYY-MM-DD", "value": float} } }
#   v2 shape (365-day retained): { entry_id: { metric_key: {"YYYY-MM-DD": float, ...} } }
# The v1→v2 migration preserves the single day v1 carried (keyed by its date)
# and drops the now-redundant {"date","value"} wrapper.  After migration the
# midnight-rollover clear in the coordinator is removed so historical days
# are retained for the 365-day export window.
METRIC_STORAGE_VERSION = 2

# Drink master storage — one Store per substance (caffeine/alcohol).
# Each substance's data dict shape:
#   {
#     "doses": [[iso_timestamp, strength, t_dur_hours], ...],
#     "body_mass": float,
#     "last_decay": iso_timestamp | None
#   }
DRINK_MASTER_STORAGE_VERSION = 1

# Debounce window for delayed saves (seconds). Rapid doses within this
# window coalesce into a single disk write.
_SAVE_DEBOUNCE_SECONDS = 5.0


def _migrate_metric_v1_to_v2(
    v1: dict[str, dict[str, dict]],
) -> dict[str, dict[str, dict]]:
    """Convert the daily-discard v1 metric shape to the retained v2 shape.

    v1: { entry_id: { metric_key: {"date": "YYYY-MM-DD", "value": float} } }
    v2: { entry_id: { metric_key: {"YYYY-MM-DD": float, ...} } }

    Each metric_key carried exactly one {"date","value"} entry in v1; we
    preserve that single day keyed by its date in the new map.  Malformed
    v1 entries (missing date/value, non-dict) are dropped — they were
    unusable in v1 as well.
    """
    migrated: dict[str, dict[str, dict]] = {}
    for entry_id, metrics in v1.items():
        if not isinstance(metrics, dict):
            continue
        new_metrics: dict[str, dict] = {}
        for key, entry in metrics.items():
            if not isinstance(entry, dict):
                continue
            d = entry.get("date")
            v = entry.get("value")
            if isinstance(d, str) and isinstance(v, (int, float)):
                new_metrics[key] = {d: float(v)}
        if new_metrics:
            migrated[entry_id] = new_metrics
    return migrated


class MetricStore(Store):
    """``Store`` subclass that owns the metric storage v1→v2 migration.

    HA's ``Store.async_load`` invokes ``_async_migrate_func`` whenever the
    on-disk major/minor version differs from the ``Store``'s constructed
    version — it does **not** return ``None`` for an older-version file
    (that was the assumption that crashed setup with ``NotImplementedError``
    from the base ``Store._async_migrate_func``).

    Returning the migrated dict here lets HA core persist it at the new
    version atomically (``storage.py`` calls ``await self.async_save(stored)``
    after a successful migration), so callers just receive the v2 shape and
    never need to know which disk version they encountered.  This is the
    idiomatic pattern used by HA core's registries (area/entity/device/label).

    v1 shape (daily-discard): { entry_id: { metric_key: {"date": "YYYY-MM-DD", "value": float} } }
    v2 shape (365-day retained): { entry_id: { metric_key: {"YYYY-MM-DD": float, ...} } }
    """

    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        """Migrate older metric storage to the current v2 date-keyed shape."""
        if old_major_version == 1:
            return _migrate_metric_v1_to_v2(old_data)
        # Unknown future version — let HA surface it as an unsupported version
        # rather than silently corrupting data.
        raise NotImplementedError


class AxDoseLoggerStore:
    """
    Manages persistent storage for dose history and daily metric data.

    Dose history format: { entry_id: [[iso_timestamp, strength], ...] }
    Metric format: { entry_id: { metric_key: { "date": "YYYY-MM-DD", "value": float } } }

    The medicine and metric stores are shared singletons (one Store per
    HA instance, keyed by ``STORAGE_KEY`` / ``METRIC_STORE_KEY``), so each
    delayed save serializes the *entire* in-memory dict — not just one
    entry's slice. The per-substance drink master stores are separate
    ``Store`` instances, each with its own storage key.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self._hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, list[list[str | float]]] = {}
        # MetricStore (Store subclass) owns the v1→v2 migration via
        # ``_async_migrate_func``; HA core persists the migrated data
        # atomically on first load of a v1 file.
        self._metric_store: MetricStore = MetricStore(hass, METRIC_STORAGE_VERSION, METRIC_STORE_KEY)
        self._metric_data: dict[str, dict[str, dict]] = {}
        # Skipped-dose slots: { entry_id: ["iso_timestamp", ...] }
        self._skipped_store: Store = Store(hass, SKIPPED_STORAGE_VERSION, SKIPPED_STORAGE_KEY)
        self._skipped_data: dict[str, list[str]] = {}
        # Adherence overrides + reset time:
        # { entry_id: { "overrides": ["iso", ...], "reset_time": "iso" | None } }
        self._adherence_store: Store = Store(hass, ADHERENCE_STORAGE_VERSION, ADHERENCE_STORAGE_KEY)
        self._adherence_data: dict[str, dict] = {}
        # Averages reset anchors:
        # { key: { "reset_time": "iso" | None } } — key is entry_id for
        # medicine + granular drink entries, "master::{profile_id}::{substance}"
        # for Master Tracker coordinators.
        self._averages_store: Store = Store(hass, AVERAGES_STORAGE_VERSION, AVERAGES_STORAGE_KEY)
        self._averages_data: dict[str, dict] = {}
        # Per-(profile_id, substance) drink master stores (created lazily).
        # Rekeyed from substance-only to the 2D (profile_id, substance) tuple
        # for the M2M multi-profile topology (see plans/m2m-decoupled-topology-plan.md).
        self._drink_master_stores: dict[tuple[str, str], Store] = {}
        self._drink_master_data: dict[tuple[str, str], dict] = {}

    async def async_load(self) -> None:
        """Load data from storage, migrating from the legacy key if needed.

        Safer migration variant: if the new key has no data but the legacy
        ``pill_logger_dose_history`` key does, copy the data into the new
        key and persist it. The legacy key file is intentionally left in
        place so the integration can be rolled back without data loss.
        """
        data = await self._store.async_load()
        if data:
            self._data = data
        else:
            # New key is empty — attempt one-time migration from the legacy key.
            legacy_store: Store = Store(self._hass, STORAGE_VERSION, _LEGACY_STORAGE_KEY)
            legacy_data = await legacy_store.async_load()
            if legacy_data:
                LOGGER.info(
                    "Migrating dose history from legacy storage key '%s' to '%s' (legacy key retained for rollback)",
                    _LEGACY_STORAGE_KEY,
                    STORAGE_KEY,
                )
                self._data = legacy_data
                await self._store.async_save(self._data)
            else:
                self._data = {}

        # Log loaded entry counts at INFO so future persistence gaps are
        # visible in the log (aids post-fix verification on the live server).
        total_doses = sum(len(v) for v in self._data.values())
        LOGGER.info(
            "AX Dose Logger dose history store loaded: %d entries, %d total doses",
            len(self._data),
            total_doses,
        )

        # Load metric data from the MetricStore.  A v1 file on disk is
        # migrated to the v2 date-keyed shape transparently by the
        # ``MetricStore._async_migrate_func`` override; HA core persists
        # the migrated dict atomically and returns it, so the caller just
        # receives the v2 shape.  A missing/empty store returns None → {}.
        # See the v1→v2 migration notes at the ``MetricStore`` class and
        # the ``migrate_metric_v1_to_v2`` module-level helper.
        metric_data = await self._metric_store.async_load()
        self._metric_data = metric_data if isinstance(metric_data, dict) else {}
        total_metric_keys = sum(len(v) for v in self._metric_data.values() if isinstance(v, dict))
        LOGGER.info(
            "AX Dose Logger metric store loaded (v2): %d entries, %d metric-date keys",
            len(self._metric_data),
            total_metric_keys,
        )

        # Load skipped-dose slots from separate store
        skipped_data = await self._skipped_store.async_load()
        if skipped_data:
            self._skipped_data = skipped_data
        else:
            self._skipped_data = {}
        total_skipped = sum(len(v) for v in self._skipped_data.values())
        LOGGER.info(
            "AX Dose Logger skipped-slots store loaded: %d entries, %d total skips",
            len(self._skipped_data),
            total_skipped,
        )

        # Load adherence overrides + reset time from separate store.
        # Forward-only: a pre-fix installation has no adherence store, so every
        # entry defaults to {"overrides": [], "reset_time": None} — no
        # retroactive adherence credit is granted for past missed slots.
        adherence_data = await self._adherence_store.async_load()
        if adherence_data:
            self._adherence_data = adherence_data
        else:
            self._adherence_data = {}
        total_overrides = sum(len(v.get("overrides", [])) for v in self._adherence_data.values())
        LOGGER.info(
            "AX Dose Logger adherence store loaded: %d entries, %d total overrides",
            len(self._adherence_data),
            total_overrides,
        )

        # Load averages reset anchors from separate store.
        # Forward-only: a pre-fix installation has no averages store, so every
        # entry loads as {"reset_time": None} — averages keep their full
        # history until the user explicitly resets them.
        averages_data = await self._averages_store.async_load()
        if averages_data:
            self._averages_data = averages_data
        else:
            self._averages_data = {}
        total_anchors = sum(1 for v in self._averages_data.values() if v.get("reset_time"))
        LOGGER.info(
            "AX Dose Logger averages store loaded: %d entries, %d reset anchors",
            len(self._averages_data),
            total_anchors,
        )

    async def async_load_drink_master(self, profile_id: str, substance: str, store_key: str) -> None:
        """Load (or initialize) the drink master store for one profile + substance.

        Called once per (profile_id, substance) during Drink Settings entry
        setup.  ``store_key`` is the profile-scoped storage key (see
        ``const.drink_master_store_key``); the in-memory dicts are keyed by
        the ``(profile_id, substance)`` tuple so multiple profiles' masters
        coexist without collision (M2M topology).
        """
        key = (profile_id, substance)
        # C7: reuse the existing Store instance across reloads.  Creating a
        # fresh Store here would orphan any pending ``async_delay_save`` on
        # the old instance, racing it against the new one writing the same
        # storage file (lost writes when a dose is logged <5s before a
        # reload).  The reused instance still (re)loads its persisted data.
        store = self._drink_master_stores.get(key)
        if store is None:
            store = Store(self._hass, DRINK_MASTER_STORAGE_VERSION, store_key)
            self._drink_master_stores[key] = store
        data = await store.async_load()
        if data:
            self._drink_master_data[key] = data
        else:
            self._drink_master_data[key] = {
                "doses": [],
                "body_mass": 0.0,
                "last_decay": None,
            }
        doses = self._drink_master_data[key].get("doses", [])
        LOGGER.info(
            "AX Dose Logger drink master (profile=%s, %s) loaded: %d doses, body_mass=%.2f",
            profile_id,
            substance,
            len(doses),
            float(self._drink_master_data[key].get("body_mass", 0.0)),
        )

    @callback
    def get_history(self, entry_id: str) -> list[list[str | float]]:
        """
        Get dose history for a specific entry.

        Returns [[iso_timestamp, strength], ...].
        """
        return self._data.get(entry_id, [])

    @callback
    def schedule_save_history(self, entry_id: str, history: list[list[str | float]]) -> None:
        """Update the in-memory slice for an entry and schedule a debounced save.

        Replaces the previous ``async_set_history`` (which awaited a full
        ``async_save`` on every dose). The shared medicine store now uses
        ``Store.async_delay_save`` so rapid doses coalesce into one write
        and HA flushes any pending write during the stop sequence.
        """
        self._data[entry_id] = history
        self._store.async_delay_save(lambda: self._data, _SAVE_DEBOUNCE_SECONDS)

    @callback
    def get_metrics(self, entry_id: str) -> dict[str, dict]:
        """Get retained daily metric values for a specific entry (v2 shape).

        Returns ``{ metric_key: { "YYYY-MM-DD": float, ... }, ... }``.
        Historical days are retained up to the entry's ``retention_days``
        (the coordinator prunes on save).  Read paths should look up by
        date string, e.g. ``metrics.get(key, {}).get(today)``.
        """
        return self._metric_data.get(entry_id, {})

    @callback
    def schedule_save_metrics(self, entry_id: str, metrics: dict[str, dict]) -> None:
        """Update the in-memory metric slice for an entry and schedule a debounced save."""
        self._metric_data[entry_id] = metrics
        self._metric_store.async_delay_save(lambda: self._metric_data, _SAVE_DEBOUNCE_SECONDS)

    # ------------------------------------------------------------------
    # Skipped-dose slots (deliberate skips, not real doses)
    # ------------------------------------------------------------------
    @callback
    def get_skipped(self, entry_id: str) -> list[str]:
        """Get skipped-dose slot timestamps for a specific entry.

        Returns ["iso_timestamp", ...].
        """
        return self._skipped_data.get(entry_id, [])

    @callback
    def schedule_save_skipped(self, entry_id: str, skipped: list[str]) -> None:
        """Update the in-memory skipped-slots slice and schedule a debounced save.

        Mirrors ``schedule_save_history``: replaces the per-entry slice and
        uses ``Store.async_delay_save`` so rapid skips coalesce into one
        write and HA flushes any pending write during the stop sequence.
        """
        self._skipped_data[entry_id] = skipped
        self._skipped_store.async_delay_save(lambda: self._skipped_data, _SAVE_DEBOUNCE_SECONDS)

    # ------------------------------------------------------------------
    # Adherence overrides + reset time (patient self-report corrections)
    # ------------------------------------------------------------------
    # Shape: { entry_id: { "overrides": ["iso", ...], "reset_time": "iso" | None } }
    # Persisted separately from skipped slots so a medical export can
    # distinguish patient self-report corrections from prescriber-directed
    # schedule skips.  Forward-only: pre-fix installations have no store,
    # so every entry loads as {"overrides": [], "reset_time": None}.
    @callback
    def get_adherence(self, entry_id: str) -> dict:
        """Get adherence override + reset-time state for a specific entry.

        Returns ``{"overrides": ["iso", ...], "reset_time": "iso" | None}``;
        an empty dict if the entry has no persisted adherence state.
        """
        return self._adherence_data.get(entry_id, {})

    @callback
    def schedule_save_adherence(
        self,
        entry_id: str,
        overrides: list[str],
        reset_time: str | None,
    ) -> None:
        """Update the in-memory adherence slice and schedule a debounced save.

        ``overrides`` is a list of ISO timestamp strings (patient self-report
        corrections).  ``reset_time`` is the ISO timestamp of the last
        treatment-restart anchor, or ``None`` if never reset.
        """
        self._adherence_data[entry_id] = {"overrides": overrides, "reset_time": reset_time}
        self._adherence_store.async_delay_save(lambda: self._adherence_data, _SAVE_DEBOUNCE_SECONDS)

    # ------------------------------------------------------------------
    # Averages reset anchors (Reset Averages tool)
    # ------------------------------------------------------------------
    @callback
    def get_averages_reset(self, key: str) -> dict:
        """Get the averages reset anchor for a specific key.

        ``key`` is an entry_id (medicine + granular drink coordinators) or
        ``"master::{profile_id}::{substance}"`` (Master Tracker
        coordinators).  Returns ``{"reset_time": "iso" | None}``; an empty
        dict if the key has no persisted reset anchor.
        """
        return self._averages_data.get(key, {})

    @callback
    def schedule_save_averages_reset(self, key: str, reset_time: str | None) -> None:
        """Update the in-memory averages slice and schedule a debounced save.

        ``reset_time`` is the ISO timestamp of the last "Reset Averages"
        action, or ``None`` if never reset.
        """
        self._averages_data[key] = {"reset_time": reset_time}
        self._averages_store.async_delay_save(lambda: self._averages_data, _SAVE_DEBOUNCE_SECONDS)

    # ------------------------------------------------------------------
    # Drink master storage (caffeine/alcohol aggregated PK)
    # ------------------------------------------------------------------
    @callback
    def get_drink_master(self, profile_id: str, substance: str) -> dict:
        """Get the aggregated drink master data for one profile + substance.

        Returns {"doses": [[iso, strength, t_dur_hours], ...],
                 "body_mass": float, "last_decay": iso | None}.
        """
        return self._drink_master_data.get(
            (profile_id, substance),
            {"doses": [], "body_mass": 0.0, "last_decay": None},
        )

    @callback
    def schedule_save_drink_master(self, profile_id: str, substance: str, data: dict) -> None:
        """Update the in-memory master data for one profile + substance and schedule a debounced save.

        Each (profile_id, substance) pair has its own ``Store`` instance
        (keyed by the profile-scoped ``drink_master_store_key(profile_id,
        substance)``), so the delayed save serializes only that pair's data.
        """
        key = (profile_id, substance)
        self._drink_master_data[key] = data
        store = self._drink_master_stores.get(key)
        if store is not None:
            store.async_delay_save(lambda: data, _SAVE_DEBOUNCE_SECONDS)
