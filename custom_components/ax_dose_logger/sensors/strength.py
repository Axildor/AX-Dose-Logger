from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.core import callback

from ..entity import AxDoseLoggerSensorEntity


class PillStrengthSensor(AxDoseLoggerSensorEntity, RestoreSensor):
    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "strength"
        self._attr_unique_id = f"{entry.entry_id}_strength"
        self._attr_icon = "mdi:pill"
        self._attr_device_class = SensorDeviceClass.WEIGHT
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._load_strength()

    def _load_strength(self) -> None:
        """Reload strength value + unit from the current config entry.

        Called on init and on every coordinator update so that options-flow
        changes (e.g. editing Dose Strength or its unit) propagate without
        requiring a device reload.  Uses ``self._entry`` (stored on the base
        entity at construction) rather than ``self.hass.config_entries``
        because ``self.hass`` is ``None`` during ``__init__`` — it is only
        set by HA when ``async_added_to_hass`` runs.  HA mutates the config
        entry object in-place on options-flow saves, so ``self._entry``
        always reflects the latest options.
        """
        entry = self._entry
        strength_unit = entry.options.get("strength_unit", entry.data.get("strength_unit", "mg"))
        self._strength_unit = strength_unit
        self._attr_native_unit_of_measurement = strength_unit
        self._attr_extra_state_attributes = {"strength_unit": strength_unit}
        self._attr_native_value = float(entry.options.get("strength", entry.data.get("strength", 0)))

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-read strength + unit so options-flow changes apply on the next refresh."""
        self._load_strength()
        self.async_write_ha_state()
