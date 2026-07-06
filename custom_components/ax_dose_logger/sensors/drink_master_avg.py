"""Master Tracker aggregate avg-doses sensor — counts every drink of a substance.

Hosted on the virtual Caffeine Tracker / Alcohol Tracker devices (created
by the Drink Settings singleton).  Mirrors :class:`DrinkAvgDosesSensor` but
subscribes to the matching :class:`DrinkMasterCoordinator` so it counts
*every* drink of that substance across all granular drink devices, not just
one drink type.

Drinks have no schedule, so the as_needed path applies (simple
``doses_in_window / window_days``).  The aggregated ``dose_history`` entries
are 3-tuples ``(datetime, strength, t_dur_hours)`` — the timestamp is index 0.
"""

from datetime import date, datetime, timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
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
        "unique_id_stem": "drink_master_avg_caffeine",
        "translation_key": "drink_master_avg_caffeine",
        "icon": "mdi:chart-bell-curve",
    },
    DRINK_TYPE_ALCOHOL: {
        "tracker_id": ALCOHOL_TRACKER_ID,
        "unique_id_stem": "drink_master_avg_alcohol",
        "translation_key": "drink_master_avg_alcohol",
        "icon": "mdi:chart-bell-curve",
    },
}


def _local_date(dt: datetime) -> date:
    """Convert a datetime to its local calendar date (mirrors frontend)."""
    if dt.tzinfo is not None:
        return dt.astimezone().date()
    return dt.date()


class DrinkMasterAvgDosesSensor(RestoreSensor):
    """Rolling average daily drinks of a substance over a fixed window.

    Subscribes to the shared :class:`DrinkMasterCoordinator` (one per
    substance) so it aggregates every logged drink across all granular drink
    devices.  Uses the as_needed averaging path (doses_in_window / window_days).
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_should_poll = False

    def __init__(
        self,
        settings_entry,
        coordinator: DrinkMasterCoordinator,
        window_days: int,
    ) -> None:
        """Initialize the substance-aggregate avg-doses sensor."""
        info = _TRACKER_INFO[coordinator.substance]
        self._coordinator = coordinator
        self._substance = coordinator.substance
        self._window_days = window_days
        # Stable unique_id — survives Drink Settings entry recreation, mirrors
        # the master PK sensor's drink_master_{substance} pattern.
        self._attr_unique_id = f"{info['unique_id_stem']}_{window_days}"
        self._attr_translation_key = info["translation_key"]
        self._attr_translation_placeholders = {"window": str(window_days)}
        self._attr_icon = info["icon"]
        # Stable device identifiers — standalone virtual Master Tracker device,
        # not tied to entry_id (see DrinkMasterSensor for the rationale).
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info["tracker_id"])},
            manufacturer="AX Dose Logger",
            model="Master Tracker",
        )
        self._history_start_date: datetime | None = None
        self._attr_extra_state_attributes = {
            "window_days": window_days,
            "history_start_date": None,
            "substance": self._substance,
            "drink_master": True,  # Frontend filter marker
        }

    async def async_added_to_hass(self) -> None:
        """Restore history_start_date, then subscribe to the master coordinator."""
        await super().async_added_to_hass()
        last_state_obj = await self.async_get_last_state()
        if last_state_obj and last_state_obj.attributes.get("history_start_date"):
            self._history_start_date = dt_util.parse_datetime(
                last_state_obj.attributes["history_start_date"]
            )
        # Anchor to the earliest aggregated dose from the master coordinator.
        if self._coordinator.data and self._coordinator.data.dose_history:
            self._history_start_date = min(
                ts for ts, _, _ in self._coordinator.data.dose_history
            )
        if self._history_start_date is None:
            self._history_start_date = dt_util.now()
        self._update_state()
        self.async_write_ha_state()
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

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
        timestamps: list[datetime] = []
        if self._coordinator.data and self._coordinator.data.dose_history:
            timestamps = [ts for ts, _, _ in self._coordinator.data.dose_history]
        valid_timestamps = [ts for ts in timestamps if ts >= cutoff]

        # As-needed average: doses in window / window days.
        self._attr_native_value = round(
            len(valid_timestamps) / actual_window_days, 1
        )
        self._attr_extra_state_attributes = {
            "window_days": self._window_days,
            "effective_window_days": round(actual_window_days, 1),
            "doses_in_window": len(valid_timestamps),
            "history_start_date": self._history_start_date.isoformat()
            if self._history_start_date
            else None,
            "substance": self._substance,
            "drink_master": True,
        }
