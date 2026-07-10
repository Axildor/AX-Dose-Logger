"""Master Tracker PK sensor — global body mass for caffeine/alcohol.

Hosted on the virtual Caffeine Tracker / Alcohol Tracker devices (created
by the Drink Settings singleton).  Reads ``body_mass`` from the matching
:class:`DrinkMasterCoordinator` and exposes PK attributes.
"""

from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback

from ..const import (
    DRINK_TYPE_ALCOHOL,
    DRINK_TYPE_CAFFEINE,
)
from ..drink_coordinator import DrinkMasterCoordinator
from ._tracker_info import MASTER_TRACKERS, tracker_device_info

# Sensor-specific keys per substance (common keys live in MASTER_TRACKERS).
_SENSOR_INFO = {
    DRINK_TYPE_CAFFEINE: {
        "unique_id": "drink_master_caffeine",
        "translation_key": "total_caffeine_in_body",
        "icon": "mdi:coffee",
        "pk_model": "bateman_ir_uniform",
    },
    DRINK_TYPE_ALCOHOL: {
        "unique_id": "drink_master_alcohol",
        "translation_key": "total_alcohol_in_body",
        "icon": "mdi:glass-wine",
        "pk_model": "zero_order",
    },
}


class DrinkMasterSensor(RestoreSensor):
    """Global PK body-mass sensor on a Master Tracker device.

    Not a CoordinatorEntity subclass because the master coordinator is
    shared across all drinks of a substance (not tied to one config entry).
    Instead it subscribes to the master coordinator via
    ``async_add_listener`` and exposes ``drink_master`` in extra state
    attributes so the frontend card can filter it out.
    """

    _attr_has_entity_name = False  # Master Tracker device name is the entity name
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0  # default to no decimal places in HA UI
    _attr_should_poll = False

    def __init__(self, settings_entry, coordinator: DrinkMasterCoordinator) -> None:
        """Initialize the master PK sensor."""
        info = _SENSOR_INFO[coordinator.substance]
        common = MASTER_TRACKERS[coordinator.substance]
        self._coordinator = coordinator
        self._substance = coordinator.substance
        self._attr_unique_id = info["unique_id"]
        self._attr_translation_key = info["translation_key"]
        self._attr_native_unit_of_measurement = common["unit"]
        self._attr_icon = info["icon"]
        self._pk_model = info["pk_model"]
        # Stable device identifiers — not tied to entry_id so the device
        # survives Drink Settings entry recreation.  with_name=True because
        # this is the device's namesake entity (has_entity_name=False).
        self._attr_device_info = tracker_device_info(self._substance, with_name=True)
        self._attr_extra_state_attributes = {
            "substance": self._substance,
            "pk_model": self._pk_model,
            "drink_master": True,  # Frontend filter marker
            # NOTE: last_dose_time moved to the dedicated DrinkMasterLastDoseSensor
            # (TIMESTAMP device class) — single source of truth for the "Last"
            # fact. dose_count is retained as metadata for the Stats panel's
            # totalDoses mapping (there is also a DrinkTotalSensor per granular
            # drink, but no master-level total count sensor yet).
            "dose_count": 0,
        }

    async def async_added_to_hass(self) -> None:
        """Restore last value, then subscribe to the master coordinator."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            self._attr_native_value = float(last_state.native_value)
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_coordinator_update))
        # Push the current coordinator state immediately.
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Read body_mass + attributes from the coordinator."""
        data = self._coordinator.data
        if data is None:
            return
        self._attr_native_value = round(data.body_mass, 1)
        self._attr_extra_state_attributes = {
            "substance": self._substance,
            "pk_model": self._pk_model,
            "drink_master": True,
            "dose_count": len(data.dose_history),
        }
        self.async_write_ha_state()
