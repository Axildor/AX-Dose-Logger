from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorStateClass
from ..entity import PillLoggerSensorEntity


class PillStrengthSensor(PillLoggerSensorEntity, RestoreSensor):
    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "strength"
        self._attr_unique_id = f"{entry.entry_id}_strength"
        self._attr_icon = "mdi:pill"
        self._strength_unit = entry.options.get("strength_unit", entry.data.get("strength_unit", "mg"))
        self._attr_native_unit_of_measurement = self._strength_unit
        self._attr_device_class = SensorDeviceClass.WEIGHT
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_extra_state_attributes = {"strength_unit": self._strength_unit}
        self._attr_native_value = float(entry.options.get("strength", entry.data.get("strength", 0)))
