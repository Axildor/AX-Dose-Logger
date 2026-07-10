"""Medicine device — 24-hour intake amount sensor.

Sliding 24-hour window exposing the **total dose strength consumed in the
last 24h**, in the medication's own ``strength_unit`` (mg/mcg/g).  The
native value is directly comparable to a user-configured ``daily_limit``
so automations and the card can warn before the next dose pushes intake
over the 24h cap.

Reads ``AxDoseLoggerCoordinator.data.dose_history`` — a list of
``(datetime, strength)`` 2-tuples.  The coordinator already pushes
updates on every dose/undo/reset and recomputes every 1-min tick, so the
window slides in real time (``should_poll = False`` via the
``CoordinatorEntity`` base).

``daily_limit`` is an optional per-device config field (default ``0`` =
no limit).  When set, the ``remaining`` attribute exposes
``daily_limit - amount`` for "X mg of Y mg — Z left" dashboard text.
``strength_unit`` + ``daily_limit`` are re-read on every coordinator
update so options-flow edits propagate without a device reload (same
pattern as :class:`PillStrengthSensor`).
"""

from datetime import timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.core import callback

from ..entity import AxDoseLoggerSensorEntity

# Fixed 24-hour rolling window for this sensor.
_WINDOW_HOURS = 24


class PillDailyAmountSensor(AxDoseLoggerSensorEntity, RestoreSensor):
    """Total strength consumed in the last 24 hours (per medicine device)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "pill_daily_amount"
        self._attr_unique_id = f"{entry.entry_id}_daily_amount"
        self._strength_unit = "mg"
        self._daily_limit = 0.0
        self._load_config()

    def _load_config(self) -> None:
        """Reload strength unit + daily limit from the current config entry.

        Called on init and on every coordinator update so options-flow
        changes propagate without a device reload (HA mutates the entry
        object in-place on options-flow saves).
        """
        entry = self._entry
        strength_unit = entry.options.get("strength_unit", entry.data.get("strength_unit", "mg"))
        self._strength_unit = strength_unit
        self._attr_native_unit_of_measurement = strength_unit
        self._daily_limit = float(entry.options.get("daily_limit", entry.data.get("daily_limit", 0)))

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            self._attr_native_value = float(last_state.native_value)
        self._update_state()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-read config + recompute the 24h sum on every coordinator push."""
        self._load_config()
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self) -> None:
        """Sum dose strengths whose timestamp falls in the last 24h."""
        now = dt_util.now()
        cutoff = now - timedelta(hours=_WINDOW_HOURS)
        amount = 0.0
        doses_in_window = 0
        if self.coordinator.data and self.coordinator.data.dose_history:
            for ts, strength in self.coordinator.data.dose_history:
                if ts >= cutoff:
                    amount += float(strength)
                    doses_in_window += 1

        self._attr_native_value = round(amount, 3)

        limit = self._daily_limit if self._daily_limit > 0 else None
        remaining = round(limit - amount, 3) if limit is not None else None
        self._attr_extra_state_attributes = {
            "window_hours": _WINDOW_HOURS,
            "doses_in_window": doses_in_window,
            "daily_limit": limit,
            "remaining": remaining,
            "unit_of_measurement": self._strength_unit,
        }
