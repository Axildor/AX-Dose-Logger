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

# Legacy storage key from the pre-rebrand "pill_logger" domain.
# Kept for the safer migration variant: on first load under the new key,
# if the new key is empty we copy data from the legacy key but do NOT
# delete the legacy file (enables rollback, ~1KB orphaned disk).
_LEGACY_STORAGE_KEY = "pill_logger_dose_history"

METRIC_STORAGE_VERSION = 1

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
        self._metric_store: Store = Store(hass, METRIC_STORAGE_VERSION, METRIC_STORE_KEY)
        self._metric_data: dict[str, dict[str, dict]] = {}
        # Per-substance drink master stores (created lazily)
        self._drink_master_stores: dict[str, Store] = {}
        self._drink_master_data: dict[str, dict] = {}

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
            legacy_store: Store = Store(
                self._hass, STORAGE_VERSION, _LEGACY_STORAGE_KEY
            )
            legacy_data = await legacy_store.async_load()
            if legacy_data:
                LOGGER.info(
                    "Migrating dose history from legacy storage key '%s' to '%s' "
                    "(legacy key retained for rollback)",
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

        # Load metric data from separate store
        metric_data = await self._metric_store.async_load()
        if metric_data:
            self._metric_data = metric_data
        else:
            self._metric_data = {}

    async def async_load_drink_master(self, substance: str, store_key: str) -> None:
        """Load (or initialize) the drink master store for a substance.

        Called once per substance during Drink Settings entry setup.
        """
        store = Store(self._hass, DRINK_MASTER_STORAGE_VERSION, store_key)
        self._drink_master_stores[substance] = store
        data = await store.async_load()
        if data:
            self._drink_master_data[substance] = data
        else:
            self._drink_master_data[substance] = {
                "doses": [],
                "body_mass": 0.0,
                "last_decay": None,
            }
        doses = self._drink_master_data[substance].get("doses", [])
        LOGGER.info(
            "AX Dose Logger drink master '%s' loaded: %d doses, body_mass=%.2f",
            substance,
            len(doses),
            float(self._drink_master_data[substance].get("body_mass", 0.0)),
        )

    @callback
    def get_history(self, entry_id: str) -> list[list[str | float]]:
        """
        Get dose history for a specific entry.

        Returns [[iso_timestamp, strength], ...].
        """
        return self._data.get(entry_id, [])

    @callback
    def schedule_save_history(
        self, entry_id: str, history: list[list[str | float]]
    ) -> None:
        """Update the in-memory slice for an entry and schedule a debounced save.

        Replaces the previous ``async_set_history`` (which awaited a full
        ``async_save`` on every dose). The shared medicine store now uses
        ``Store.async_delay_save`` so rapid doses coalesce into one write
        and HA flushes any pending write during the stop sequence.
        """
        self._data[entry_id] = history
        self._store.async_delay_save(
            lambda: self._data, _SAVE_DEBOUNCE_SECONDS
        )

    @callback
    def get_metrics(self, entry_id: str) -> dict[str, dict]:
        """
        Get daily metric values for a specific entry.

        Returns { metric_key: { "date": "YYYY-MM-DD", "value": float }, ... }
        """
        return self._metric_data.get(entry_id, {})

    @callback
    def schedule_save_metrics(
        self, entry_id: str, metrics: dict[str, dict]
    ) -> None:
        """Update the in-memory metric slice for an entry and schedule a debounced save."""
        self._metric_data[entry_id] = metrics
        self._metric_store.async_delay_save(
            lambda: self._metric_data, _SAVE_DEBOUNCE_SECONDS
        )

    # ------------------------------------------------------------------
    # Drink master storage (caffeine/alcohol aggregated PK)
    # ------------------------------------------------------------------
    @callback
    def get_drink_master(self, substance: str) -> dict:
        """Get the aggregated drink master data for a substance.

        Returns {"doses": [[iso, strength, t_dur_hours], ...],
                 "body_mass": float, "last_decay": iso | None}.
        """
        return self._drink_master_data.get(
            substance,
            {"doses": [], "body_mass": 0.0, "last_decay": None},
        )

    @callback
    def schedule_save_drink_master(self, substance: str, data: dict) -> None:
        """Update the in-memory master data for a substance and schedule a debounced save.

        Each substance has its own ``Store`` instance (keyed by
        ``DRINK_MASTER_STORE_KEYS[substance]``), so the delayed save
        serializes only that substance's data.
        """
        self._drink_master_data[substance] = data
        store = self._drink_master_stores.get(substance)
        if store is not None:
            store.async_delay_save(lambda: data, _SAVE_DEBOUNCE_SECONDS)
