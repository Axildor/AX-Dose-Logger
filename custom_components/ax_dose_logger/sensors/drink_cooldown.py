"""Granular drink cooldown sensor.

Mirrors :class:`PillLimitSensor`'s shape so the frontend card consumes it
identically to medicine's ``pill_limit`` entity:

* ``native_value`` is ``1`` when a drink is available (outside the cooldown
  window or no history) and ``0`` when the cooldown is active (limit
  reached for this window).  The "limit is always 1" rule is enforced the
  same way ``PillLimitSensor`` enforces ``max_pills - count``.
* ``state_class`` is ``MEASUREMENT`` (matches ``PillLimitSensor``).
* Attributes expose ``cooldown_ends_at`` (analogous to ``PillLimitSensor``'s
  ``window_expires_at``), ``last_dose_time``, ``cooldown_window_hours`` and
  ``within_cooldown`` (raw boolean mirror of
  ``DrinkCoordinator.is_within_cooldown()``).

The backend never blocks a drink log (see ``DrinkLogButton`` /
``_async_log_drink``); this sensor is the contract the card reads to
soft-disable the Log button + show a Last/Next countdown, while the user
can always override by pressing anyway.

Card display contract when cooldown active (``native_value == 0``):
  * ``Last XXm`` = ``now - last_dose_time``
  * ``Next XXm`` = ``cooldown_ends_at - now``
"""

from datetime import timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from ..const import DOMAIN
from ..drink_coordinator import DrinkCoordinator


class DrinkCooldownSensor(RestoreSensor):
    """Drinks available within the current cooldown window (0 or 1)."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_should_poll = False
    _attr_translation_key = "drink_cooldown"
    _attr_icon = "mdi:cup-water"

    def __init__(self, entry, coordinator: DrinkCoordinator) -> None:
        """Initialize the granular drink cooldown sensor."""
        self._entry = entry
        self._coordinator = coordinator
        self._substance = entry.data.get("drink_type")
        self._attr_unique_id = f"{entry.entry_id}_drink_cooldown"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get("name", entry.title),
            manufacturer="AX Dose Logger",
            model="Drink",
        )
        self._attr_extra_state_attributes = {
            "cooldown_ends_at": None,
            "last_dose_time": None,
            "cooldown_window_hours": 0.0,
            "within_cooldown": False,
            "substance": self._substance,
            "device_type": "drink",
        }
        self._attr_native_value = 1

    async def async_added_to_hass(self) -> None:
        """Restore last value, then subscribe to the coordinator."""
        await super().async_added_to_hass()
        last_state_obj = await self.async_get_last_state()
        if last_state_obj is not None:
            try:
                self._attr_native_value = int(float(last_state_obj.state))
            except ValueError, TypeError:
                self._attr_native_value = 1
            if last_state_obj.attributes:
                self._attr_extra_state_attributes = {
                    "cooldown_ends_at": last_state_obj.attributes.get("cooldown_ends_at"),
                    "last_dose_time": last_state_obj.attributes.get("last_dose_time"),
                    "cooldown_window_hours": float(last_state_obj.attributes.get("cooldown_window_hours", 0.0) or 0.0),
                    "within_cooldown": bool(last_state_obj.attributes.get("within_cooldown", False)),
                    "substance": self._substance,
                    "device_type": "drink",
                }
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_coordinator_update))
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute available-drink count + cooldown attributes on every push/tick."""
        now = dt_util.now()
        coordinator = self._coordinator

        cooldown_h = float(
            self._entry.options.get(
                "cooldown_window",
                self._entry.data.get("cooldown_window", 0),
            )
        )

        last_dose_time = None
        cooldown_ends_at = None
        within = False
        available = 1

        if coordinator.data and coordinator.data.dose_history:
            last_dose_time = coordinator.data.dose_history[-1][0]
            if cooldown_h > 0:
                within = coordinator.is_within_cooldown(now)
                cooldown_ends_at = last_dose_time + timedelta(hours=cooldown_h)
                available = 0 if within else 1

        self._attr_native_value = available
        self._attr_extra_state_attributes = {
            "cooldown_ends_at": cooldown_ends_at.isoformat() if cooldown_ends_at else None,
            "last_dose_time": last_dose_time.isoformat() if last_dose_time else None,
            "cooldown_window_hours": cooldown_h,
            "within_cooldown": within,
            "substance": self._substance,
            "device_type": "drink",
        }
        # Dynamic icon: cup when available, sand-empty when locked.
        self._attr_icon = "mdi:cup-water" if available else "mdi:timer-sand-empty"
        self.async_write_ha_state()
