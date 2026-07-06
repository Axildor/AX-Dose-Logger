"""Granular drink last-dose timestamp sensor.

Replicates :class:`PillLastDoseSensor` but reads from a :class:`DrinkCoordinator`.
Returns the timestamp of the most recent drink of this granular device.
"""


import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor
from homeassistant.components.sensor.const import SensorDeviceClass
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from ..const import DOMAIN
from ..drink_coordinator import DrinkCoordinator


class DrinkLastDoseSensor(RestoreSensor):
    """Timestamp of the most recent drink of this granular device."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    # NOTE: no state_class — TIMESTAMP device class requires state_class=None
    # (a measurement state class is invalid for timestamps per HA core validation).
    _attr_should_poll = False
    _attr_translation_key = "drink_last_dose"
    _attr_icon = "mdi:clock-time-four"

    def __init__(self, entry, coordinator: DrinkCoordinator) -> None:
        """Initialize the granular last-dose sensor."""
        self._entry = entry
        self._coordinator = coordinator
        self._substance = entry.data.get("drink_type")
        self._attr_unique_id = f"{entry.entry_id}_drink_last_dose"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get("name", entry.title),
            manufacturer="AX Dose Logger",
            model="Drink",
        )
        # Frontend contract: substance + device_type so the card can detect a
        # granular drink device and group by substance.
        self._attr_extra_state_attributes = {
            "substance": self._substance,
            "device_type": "drink",
        }

    async def async_added_to_hass(self) -> None:
        """Restore last value, then subscribe to the coordinator."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            try:
                self._attr_native_value = dt_util.parse_datetime(str(last_state.native_value))
            except (ValueError, TypeError):
                self._attr_native_value = None
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Last drink time = last entry of coordinator dose_history."""
        if self._coordinator.data and self._coordinator.data.dose_history:
            self._attr_native_value = self._coordinator.data.last_dose_time
        else:
            self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "substance": self._substance,
            "device_type": "drink",
        }
        self.async_write_ha_state()
