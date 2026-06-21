"""
Persistent storage for dose history data outside entity attributes.

Uses HA's storage.Store to persist dose history to a JSON file,
avoiding SQLite bloat and the 16KB attribute limit.
"""

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import LOGGER

STORAGE_VERSION = 1
STORAGE_KEY = "ax_dose_logger_dose_history"

# Legacy storage key from the pre-rebrand "pill_logger" domain.
# Kept for the safer migration variant: on first load under the new key,
# if the new key is empty we copy data from the legacy key but do NOT
# delete the legacy file (enables rollback, ~1KB orphaned disk).
_LEGACY_STORAGE_KEY = "pill_logger_dose_history"


class AxDoseLoggerStore:
    """
    Manages persistent storage for dose history data outside entity attributes.

    Data format: { entry_id: [[iso_timestamp, strength], ...] }
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self._hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, list[list[str | float]]] = {}

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
            return

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

    @callback
    def get_history(self, entry_id: str) -> list[list[str | float]]:
        """
        Get dose history for a specific entry.

        Returns [[iso_timestamp, strength], ...].
        """
        return self._data.get(entry_id, [])

    async def async_set_history(
        self, entry_id: str, history: list[list[str | float]]
    ) -> None:
        """Save dose history for a specific entry."""
        self._data[entry_id] = history
        await self._store.async_save(self._data)
