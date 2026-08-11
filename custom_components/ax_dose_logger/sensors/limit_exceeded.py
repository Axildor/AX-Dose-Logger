"""Medicine device — 24h strength limit exceeded binary sensor.

Binary sensor that turns **on** when the 24h sliding-window dose strength
sum has already exceeded the user-configured ``daily_limit`` OR when the
next configured dose would push the total over the limit (pre-warning).

This is a **pharmacological safety limit** (e.g. 4000 mg paracetamol,
3200 mg ibuprofen per 24 h), not a behavioural compliance metric. Safety
limits warn immediately — no grace period — because delaying a
hepatotoxicity / GI-bleeding warning by even 30 minutes could allow an
extra dose to be taken while already over the toxicity threshold.

The sensor is only created when ``daily_limit > 0`` (see ``sensor.py``
``_setup_medicine_sensors``). When the user sets ``daily_limit = 0`` (no
limit), the entity is removed via the ``_STRUCTURAL_KEYS`` reload in
``__init__.py``.

State attributes expose the raw numbers so the frontend card and
automations can display "X mg of Y mg — Z left" and distinguish
``already_exceeded`` (you're over) from ``would_exceed`` (the next dose
would push you over):

* ``current_amount`` — total strength consumed in the last 24 h
* ``daily_limit``    — the configured 24 h cap
* ``next_dose_strength`` — the per-dose strength (``strength`` config field)
* ``remaining``      — ``daily_limit - current_amount``
* ``already_exceeded`` — ``True`` when ``current_amount > daily_limit``
* ``would_exceed``   — ``True`` when ``current_amount + next_dose_strength > daily_limit``
  but not already exceeded
* ``role``           — ``"24h_limit_exceeded"`` (frontend entity resolution)
* ``unit_of_measurement`` — the medication's ``strength_unit``

Reads ``AxDoseLoggerCoordinator.data.dose_history`` — a list of
``(datetime, strength)`` 2-tuples. The coordinator pushes updates on
every dose/undo/reset and recomputes every 1-min tick, so the sensor
stays current (``should_poll = False`` via ``CoordinatorEntity``).
"""

from datetime import timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import callback

from ..entity import AxDoseLoggerSensorEntity

# Fixed 24-hour rolling window (mirrors PillDailyAmountSensor).
_WINDOW_HOURS = 24


class Pill24hLimitExceededSensor(AxDoseLoggerSensorEntity, BinarySensorEntity):
    """Binary sensor: on when the 24h strength limit is or would be exceeded."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert-circle"

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "pill_24h_limit_exceeded"
        self._attr_unique_id = f"{entry.entry_id}_24h_limit_exceeded"
        self._daily_limit = 0.0
        self._strength = 0.0
        self._strength_unit = "mg"
        self._load_config()

    def _load_config(self) -> None:
        """Reload daily_limit + strength + unit from the current config entry.

        Called on init and on every coordinator update so options-flow
        changes propagate without a device reload (HA mutates the entry
        object in-place on options-flow saves).
        """
        entry = self._entry
        self._daily_limit = float(entry.options.get("daily_limit", entry.data.get("daily_limit", 0)))
        self._strength = float(entry.options.get("strength", entry.data.get("strength", 0)))
        self._strength_unit = entry.options.get("strength_unit", entry.data.get("strength_unit", "mg"))

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-read config + recompute the limit state on every coordinator push."""
        self._load_config()
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self) -> None:
        """Compute whether the 24h limit is or would be exceeded."""
        now = dt_util.now()
        cutoff = now - timedelta(hours=_WINDOW_HOURS)
        amount = 0.0
        if self.coordinator.data and self.coordinator.data.dose_history:
            for ts, strength in self.coordinator.data.dose_history:
                if ts >= cutoff:
                    amount += float(strength)

        limit = self._daily_limit
        already_exceeded = amount > limit
        would_exceed = (amount + self._strength) > limit and not already_exceeded

        self._attr_is_on = already_exceeded or would_exceed
        self._attr_extra_state_attributes = {
            "role": "24h_limit_exceeded",
            "current_amount": round(amount, 3),
            "daily_limit": limit,
            "next_dose_strength": self._strength,
            "remaining": round(limit - amount, 3),
            "already_exceeded": already_exceeded,
            "would_exceed": would_exceed,
            "unit_of_measurement": self._strength_unit,
            "window_hours": _WINDOW_HOURS,
        }
