"""Granular drink day-coverage average sensor.

Replicates the as_needed path of :class:`PillAvgDosesSensor` — simple
``doses_in_window / window_days`` since drinks have no schedule.  Four
instances per drink (7/14/30/365-day windows).
"""

from datetime import date, datetime, timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from ..const import DOMAIN
from ..drink_coordinator import DrinkCoordinator


def _local_date(dt: datetime) -> date:
    """Convert a datetime to its local calendar date (mirrors frontend)."""
    if dt.tzinfo is not None:
        return dt.astimezone().date()
    return dt.date()


class DrinkAvgDosesSensor(RestoreSensor):
    """Rolling average daily drinks over a fixed window (as_needed path)."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_should_poll = False
    _attr_translation_key = "drink_avg_doses"
    _attr_icon = "mdi:chart-bell-curve"

    def __init__(self, entry, coordinator: DrinkCoordinator, window_days: int) -> None:
        """Initialize the granular avg-doses sensor."""
        self._entry = entry
        self._coordinator = coordinator
        self._window_days = window_days
        self._substance = entry.data.get("drink_type")
        self._attr_unique_id = f"{entry.entry_id}_drink_avg_{window_days}"
        self._attr_translation_placeholders = {"window": str(window_days)}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get("name", entry.title),
            manufacturer="AX Dose Logger",
            model="Drink",
        )
        self._history_start_date: datetime | None = None
        self._attr_extra_state_attributes = {
            "window_days": window_days,
            "history_start_date": None,
            "substance": self._substance,
            "device_type": "drink",
            "role": "avg",
        }

    async def async_added_to_hass(self) -> None:
        """Restore history_start_date, then subscribe to the coordinator."""
        await super().async_added_to_hass()
        last_state_obj = await self.async_get_last_state()
        if last_state_obj and last_state_obj.attributes.get("history_start_date"):
            self._history_start_date = dt_util.parse_datetime(last_state_obj.attributes["history_start_date"])
        # Anchor to earliest dose from coordinator dose_history.
        if self._coordinator.data and self._coordinator.data.dose_history:
            self._history_start_date = min(ts for ts, _ in self._coordinator.data.dose_history)
        if self._history_start_date is None:
            self._history_start_date = dt_util.now()
        self._update_state()
        self.async_write_ha_state()
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_coordinator_update))

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute the average on coordinator updates (dose + 1-min tick)."""
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self) -> None:
        """Compute doses_in_window / window_days (as_needed path)."""
        now = dt_util.now()
        if not self._history_start_date:
            self._history_start_date = now
        days_since_start = (now - self._history_start_date).total_seconds() / 86400.0
        days_since_start = max(1.0, days_since_start)
        actual_window_days = min(days_since_start, float(self._window_days))

        cutoff = now - timedelta(days=actual_window_days)
        timestamps = []
        if self._coordinator.data and self._coordinator.data.dose_history:
            timestamps = [ts for ts, _ in self._coordinator.data.dose_history]
        valid_timestamps = [ts for ts in timestamps if ts >= cutoff]

        # As-needed average: doses in window / window days.
        self._attr_native_value = round(len(valid_timestamps) / actual_window_days, 1)
        self._attr_extra_state_attributes = {
            "window_days": self._window_days,
            "effective_window_days": round(actual_window_days, 1),
            "doses_in_window": len(valid_timestamps),
            "history_start_date": self._history_start_date.isoformat() if self._history_start_date else None,
            "substance": self._substance,
            "device_type": "drink",
            "role": "avg",
        }
