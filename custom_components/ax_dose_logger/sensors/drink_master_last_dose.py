"""Master Tracker last-dose timestamp sensor — global last drink of a substance.

Hosted on the virtual Caffeine Tracker / Alcohol Tracker devices (created
by the Drink Settings singleton).  Mirrors the granular
:class:`DrinkLastDoseSensor` and the medicine :class:`PillLastDoseSensor`
but subscribes to the matching :class:`DrinkMasterCoordinator` so its state
is the timestamp of the most recent drink of that substance across *all*
granular drink devices.

This is the canonical source of the "Last" fact for a Master Tracker —
the frontend card's ``computeTimeSinceLastDose`` reads this entity's
*state* (a TIMESTAMP device-class value), exactly like the medicine path.
The body-mass master sensor no longer carries ``last_dose_time`` as an
attribute (single source of truth = this sensor's state).
"""

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor
from homeassistant.components.sensor.const import SensorDeviceClass
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from ..const import (
    ALCOHOL_TRACKER_ID,
    CAFFEINE_TRACKER_ID,
    DOMAIN,
    DRINK_TYPE_ALCOHOL,
    DRINK_TYPE_CAFFEINE,
)
from ..drink_coordinator import DrinkMasterCoordinator

# Stable device identifiers + per-substance translation key + unique-id stem.
_TRACKER_INFO = {
    DRINK_TYPE_CAFFEINE: {
        "tracker_id": CAFFEINE_TRACKER_ID,
        "unique_id": "drink_master_last_dose_caffeine",
        "translation_key": "drink_master_last_dose_caffeine",
        "icon": "mdi:clock-time-four",
    },
    DRINK_TYPE_ALCOHOL: {
        "tracker_id": ALCOHOL_TRACKER_ID,
        "unique_id": "drink_master_last_dose_alcohol",
        "translation_key": "drink_master_last_dose_alcohol",
        "icon": "mdi:clock-time-four",
    },
}


class DrinkMasterLastDoseSensor(RestoreSensor):
    """Timestamp of the most recent drink of a substance (Master Tracker).

    Subscribes to the shared :class:`DrinkMasterCoordinator` (one per
    substance) so it aggregates every logged drink across all granular
    drink devices.  State = ``coordinator.data.last_dose_time`` (a
    timezone-aware datetime), or ``None`` when no drink has been logged.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    # NOTE: no state_class — TIMESTAMP device class requires state_class=None
    # (a measurement state class is invalid for timestamps per HA core validation).
    _attr_should_poll = False

    def __init__(self, settings_entry, coordinator: DrinkMasterCoordinator) -> None:
        """Initialize the substance-aggregate last-dose sensor."""
        info = _TRACKER_INFO[coordinator.substance]
        self._coordinator = coordinator
        self._substance = coordinator.substance
        # Stable unique_id — survives Drink Settings entry recreation, mirrors
        # the master PK sensor's drink_master_{substance} pattern.
        self._attr_unique_id = info["unique_id"]
        self._attr_translation_key = info["translation_key"]
        self._attr_icon = info["icon"]
        # Stable device identifiers — standalone virtual Master Tracker device,
        # not tied to entry_id (see DrinkMasterSensor for the rationale).
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info["tracker_id"])},
            manufacturer="AX Dose Logger",
            model="Master Tracker",
        )
        self._attr_extra_state_attributes = {
            "substance": self._substance,
            "drink_master": True,  # Frontend filter marker
            "role": "last_dose",  # Frontend classifier (survives entity_id renames)
        }

    async def async_added_to_hass(self) -> None:
        """Restore last value, then subscribe to the master coordinator."""
        await super().async_added_to_hass()
        last_state_obj = await self.async_get_last_state()
        if last_state_obj and last_state_obj.state not in (None, "unknown", "unavailable"):
            parsed = dt_util.parse_datetime(last_state_obj.state)
            if parsed:
                self._attr_native_value = parsed
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )
        # Push the current coordinator state immediately.
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Last drink time = coordinator.data.last_dose_time."""
        if self._coordinator.data and self._coordinator.data.last_dose_time:
            self._attr_native_value = self._coordinator.data.last_dose_time
        else:
            self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "substance": self._substance,
            "drink_master": True,
            "role": "last_dose",
        }
        self.async_write_ha_state()
