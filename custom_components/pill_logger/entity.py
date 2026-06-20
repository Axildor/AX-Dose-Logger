"""Base entity class for pill_logger integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PillLoggerCoordinator


class PillLoggerEntity(CoordinatorEntity[PillLoggerCoordinator]):
    """
    Base entity class for Pill Logger integration.

    Extends ``CoordinatorEntity`` so all entities receive coordinator
    updates.  The coordinator is the single source of truth for dose
    history — entities read ``self.coordinator.data`` in
    ``_handle_coordinator_update`` instead of maintaining their own
    copies and listening to dispatcher signals.

    Provides shared ``device_info``, ``_entry_id``, and ``_med_name``
    derived from the config entry.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: PillLoggerCoordinator,
    ) -> None:
        """Initialize the entity from a config entry and coordinator."""
        super().__init__(coordinator)
        self._entry_id = entry.entry_id
        self._med_name = entry.data["medication_name"]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )


class PillLoggerSensorEntity(PillLoggerEntity):
    """
    Base class for Pill Logger sensor entities.

    Inherits device_info and entry parsing from PillLoggerEntity.
    Sensor classes should use multiple inheritance with RestoreSensor:

        class PillXxxSensor(PillLoggerSensorEntity, RestoreSensor):

    The MRO ensures super().async_added_to_hass() chains through
    CoordinatorEntity (which adds the coordinator listener) and then
    RestoreSensor (for state restoration).
    """
