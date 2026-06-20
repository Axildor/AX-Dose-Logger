"""Persistent storage for dose history data outside entity attributes.

Uses HA's storage.Store to persist dose history to a JSON file,
avoiding SQLite bloat and the 16KB attribute limit.
"""

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

STORAGE_VERSION = 1
STORAGE_KEY = "pill_logger_dose_history"


class PillLoggerStore:
    """Manages persistent storage for dose history data outside entity attributes.

    Data format: { entry_id: [[iso_timestamp, strength], ...] }
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self._hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, list[list[str | float]]] = {}

    async def async_load(self) -> None:
        """Load data from storage."""
        data = await self._store.async_load()
        if data:
            self._data = data

    @callback
    def get_history(self, entry_id: str) -> list[list[str | float]]:
        """Get dose history for a specific entry.

        Returns [[iso_timestamp, strength], ...].
        """
        return self._data.get(entry_id, [])

    async def async_set_history(
        self, entry_id: str, history: list[list[str | float]]
    ) -> None:
        """Save dose history for a specific entry."""
        self._data[entry_id] = history
        await self._store.async_save(self._data)
