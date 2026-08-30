"""Medicine device — daily-limit remaining sensor.

Companion to :class:`PillDailyAmountSensor` exposing the **remaining daily
allowance** as a standalone entity: ``daily_limit − amount_24h`` in the
medication's own ``strength_unit`` (mg/mcg/g).  A negative value means the
24h limit is already exceeded (overage shown as e.g. ``-50.0``).

Promoted from the ``remaining`` attribute of the Amount in Last 24h sensor so
automations, dashboards, and history graphs can consume the value directly
without template sensors.  The ``remaining`` attribute stays on the host
sensor (deprecated, not removed) so existing user templates keep working.

Created only when a ``daily_limit > 0`` is configured (same guard as
:class:`Pill24hLimitExceededSensor`) — no dead entity when no limit is set.
``strength_unit`` + ``daily_limit`` are re-read on every coordinator update
so options-flow edits propagate without a device reload (same pattern as
:class:`PillDailyAmountSensor`).
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

# Fixed 24-hour rolling window (mirrors PillDailyAmountSensor).
_WINDOW_HOURS = 24


class PillDailyRemainingSensor(AxDoseLoggerSensorEntity, RestoreSensor):
    """Remaining daily allowance (daily_limit − amount in last 24h)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:progress-clock"

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "pill_daily_remaining"
        self._attr_unique_id = f"{entry.entry_id}_daily_remaining"
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
        """Re-read config + recompute the remaining allowance on every push."""
        self._load_config()
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self) -> None:
        """Compute daily_limit − amount_24h (negative = overage)."""
        now = dt_util.now()
        cutoff = now - timedelta(hours=_WINDOW_HOURS)
        amount = 0.0
        if self.coordinator.data and self.coordinator.data.dose_history:
            for ts, strength in self.coordinator.data.dose_history:
                if ts >= cutoff:
                    amount += float(strength)

        limit = self._daily_limit if self._daily_limit > 0 else None
        remaining = round(limit - amount, 3) if limit is not None else None
        self._attr_native_value = remaining

        self._attr_extra_state_attributes = {
            "role": "daily_remaining",
            "window_hours": _WINDOW_HOURS,
            "daily_limit": limit,
            "amount_24h": round(amount, 3),
            "unit_of_measurement": self._strength_unit,
        }
