"""Granular drink lifetime count sensor.

Replicates :class:`PillTotalSensor` but reads from a :class:`DrinkCoordinator`
(per-drink local history).  Lifetime count of drinks of this type.
"""

from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from ..const import DOMAIN
from ..drink_coordinator import DrinkCoordinator


class DrinkTotalSensor(RestoreSensor):
    """Lifetime count of drinks logged for this granular drink device."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_should_poll = False
    _attr_translation_key = "drink_total"
    _attr_icon = "mdi:counter"

    def __init__(self, entry, coordinator: DrinkCoordinator) -> None:
        """Initialize the granular total sensor."""
        self._entry = entry
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_drink_total"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get("name", entry.title),
            manufacturer="AX Dose Logger",
            model="Drink",
        )

    async def async_added_to_hass(self) -> None:
        """Restore last value, then subscribe to the coordinator."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            self._attr_native_value = int(last_state.native_value)
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Total drinks = length of coordinator dose_history."""
        if self._coordinator.data and self._coordinator.data.dose_history:
            self._attr_native_value = len(self._coordinator.data.dose_history)
        else:
            self._attr_native_value = 0
        self.async_write_ha_state()