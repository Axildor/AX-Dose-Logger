from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorStateClass
from homeassistant.helpers.device_registry import DeviceInfo
from ..const import DOMAIN

class PillStrengthSensor(RestoreSensor):
    def __init__(self, entry):
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._attr_name = f"{med_name} Strength"
        self._attr_unique_id = f"{entry.entry_id}_strength"
        self._attr_icon = "mdi:pill"
        self._entry_id = entry.entry_id
        self._attr_native_unit_of_measurement = "mg"
        self._attr_device_class = SensorDeviceClass.WEIGHT
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_value = float(entry.options.get("strength", entry.data.get("strength", 0)))

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )
