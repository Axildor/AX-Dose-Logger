"""Base entity class for pill_logger integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class PillLoggerEntity(CoordinatorEntity):
    """Base entity class for Pill Logger integration."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id: str, med_name: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._med_name = med_name
        self._attr_unique_id = entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=med_name,
            manufacturer="Pill Logger",
        )
