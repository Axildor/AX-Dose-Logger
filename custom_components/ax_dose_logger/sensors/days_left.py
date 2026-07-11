"""Days-left inventory-burn sensors.

A "Days left" sensor answers: *at the current consumption rate, how many
days will the current inventory last?*  The raw Pills Left / Inventory count
only equals days left when exactly one dose is taken per day.  For multi-dose
schedules, As-Needed medications, and drinks, the count must be divided by the
doses taken per day.

Two variants exist because the integration has two device families, each
with a different way to derive "doses per day":

* :class:`PillDaysLeftSensor` (medicine devices)
    - **Scheduled** (Regular Interval / Time of Day / Cyclic): doses/day is
      deterministic from config (``24 / hours_between_doses``,
      ``len(dose_times)``, or ``1`` for cyclic).  Named **"Days left"**.
    - **As Needed**: doses/day is the 7-day average from dose history.  Named
      **"Est. days left"** (empirical estimate).

* :class:`DrinkDaysLeftSensor` (granular drink devices)
    - Drinks have no schedule, so doses/day is the 7-day average from the
      granular ``DrinkCoordinator``.  Named **"Est. days left"**.

The Master Tracker (Caffeine / Alcohol aggregate) does **not** have a days-left
sensor — it has no single inventory of its own, and summing every granular
drink's stock is misleading on the aggregate device.  The per-granular-drink
:class:`DrinkDaysLeftSensor` surfaces each drink's burn rate on the Inventory
panel instead.

Both variants read the live stock state from the HA state machine (single
source of truth = the ``RestoreNumber`` inventory entity) and refresh on two
push sources: the coordinator (dose-history changes that move the average)
and ``async_track_state_change_event`` on the stock entity (dose /
undo / add-stock / manual edit).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event

from ..const import (
    DOMAIN,
    TRACKING_AS_NEEDED,
    TRACKING_REGULAR_INTERVAL,
    TRACKING_TIME_OF_DAY,
    get_dose_times,
)
from ..coordinator import AxDoseLoggerCoordinator
from ..drink_coordinator import DrinkCoordinator
from ..entity import AxDoseLoggerSensorEntity

# The averaging window (days) used for the empirical "Est. days left" variants.
_AVG_WINDOW_DAYS = 7


def _local_date(dt: datetime) -> date:
    """Convert a datetime to its local calendar date (mirrors frontend)."""
    if dt.tzinfo is not None:
        return dt.astimezone().date()
    return dt.date()


# =====================================================================
# Medicine — PillDaysLeftSensor
# =====================================================================


class PillDaysLeftSensor(AxDoseLoggerSensorEntity, RestoreSensor):
    """Days of inventory remaining for a medicine device.

    Scheduled medications use a config-derived doses/day (deterministic), so
    the sensor is named "Days left".  As-Needed medications use the 7-day
    average doses/day, so the sensor is named "Est. days left".
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_icon = "mdi:calendar-clock"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, coordinator: AxDoseLoggerCoordinator) -> None:
        super().__init__(entry, coordinator)
        self._tracking_type = entry.data.get("tracking_type", TRACKING_REGULAR_INTERVAL)
        if self._tracking_type == TRACKING_AS_NEEDED:
            self._attr_translation_key = "days_left_est"
            self._attr_unique_id = f"{entry.entry_id}_days_left_est"
            self._attr_suggested_display_precision = 1
        else:
            self._attr_translation_key = "days_left"
            self._attr_unique_id = f"{entry.entry_id}_days_left"
            self._attr_suggested_display_precision = 0
        # Stock number entity_id, resolved in async_added_to_hass.
        self._stock_entity_id: str | None = None
        self._unsub_stock: callable | None = None

    async def async_added_to_hass(self) -> None:
        """Restore last value, resolve the stock entity, subscribe to updates."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            self._attr_native_value = float(last_state.native_value)

        # Resolve the PillStockNumber entity_id via the entity registry.
        ent_reg = er.async_get(self.hass)
        self._stock_entity_id = ent_reg.async_get_entity_id("number", DOMAIN, f"{self._entry_id}_stock")
        if self._stock_entity_id:
            self._unsub_stock = async_track_state_change_event(
                self.hass, self._stock_entity_id, self._stock_state_changed
            )
        self._update_state()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up the stock state-change subscription."""
        if self._unsub_stock:
            self._unsub_stock()
            self._unsub_stock = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute on dose-history changes (dose/undo/reset + 1-min tick)."""
        self._update_state()
        self.async_write_ha_state()

    @callback
    def _stock_state_changed(self, _event) -> None:
        """Recompute when the Pills Left number entity changes."""
        self._update_state()
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # State computation
    # ------------------------------------------------------------------

    def _read_stock(self) -> float:
        """Read the current pill inventory from the live number entity."""
        if not self._stock_entity_id:
            return 0.0
        state = self.hass.states.get(self._stock_entity_id)
        if state is None or state.state in (None, "unknown", "unavailable"):
            return 0.0
        try:
            return max(0.0, float(state.state))
        except ValueError, TypeError:
            return 0.0

    def _doses_per_day_scheduled(self) -> float:
        """Config-derived doses/day for scheduled tracking types."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return 1.0
        if self._tracking_type == TRACKING_TIME_OF_DAY:
            parsed = get_dose_times(entry)
            return max(1.0, float(len(parsed)))
        if self._tracking_type == TRACKING_REGULAR_INTERVAL:
            hours = float(
                entry.options.get(
                    "hours_between_doses",
                    entry.data.get("hours_between_doses", 24.0),
                )
            )
            return max(1.0, 24.0 / hours) if hours > 0 else 1.0
        # Cyclic — project calendar days until depletion.  ON days consume
        # one dose; OFF days consume zero, so the cycle-average rate is
        # days_on / (days_on + days_off).  This matches the calendar-day
        # projection semantics of the Time-of-Day and Regular-Interval
        # branches (len(dose_times) and 24/hours_between_doses are both
        # calendar-day rates, not per-active-day rates).
        days_on = float(
            entry.options.get("days_on", entry.data.get("days_on", 5))
        )
        days_off = float(
            entry.options.get("days_off", entry.data.get("days_off", 2))
        )
        cycle_length = days_on + days_off
        if cycle_length <= 0:
            return 1.0
        return days_on / cycle_length

    def _doses_per_day_avg(self) -> float | None:
        """Empirical doses/day from the trailing 7-day dose history."""
        if not self.coordinator.data or not self.coordinator.data.dose_history:
            return None
        now = dt_util.now()
        cutoff = now - timedelta(days=_AVG_WINDOW_DAYS)
        timestamps = [ts for ts, _ in self.coordinator.data.dose_history]
        valid = [ts for ts in timestamps if ts >= cutoff]
        if not valid:
            return None
        return len(valid) / float(_AVG_WINDOW_DAYS)

    def _update_state(self) -> None:
        """Compute days_left = stock / doses_per_day."""
        stock = self._read_stock()
        if self._tracking_type == TRACKING_AS_NEEDED:
            dpd = self._doses_per_day_avg()
        else:
            dpd = self._doses_per_day_scheduled()

        attrs: dict[str, object] = {
            "tracking_type": self._tracking_type,
            "stock": int(stock) if stock.is_integer() else stock,
            "doses_per_day": round(dpd, 2) if dpd is not None else None,
            "estimation": self._tracking_type == TRACKING_AS_NEEDED,
            "window_days": _AVG_WINDOW_DAYS if self._tracking_type == TRACKING_AS_NEEDED else None,
            "role": "days_left",
        }

        if dpd is None or dpd <= 0:
            # No history yet (As Needed) — can't estimate.
            self._attr_native_value = None
            self._attr_extra_state_attributes = attrs
            return

        days = stock / dpd
        if self._tracking_type == TRACKING_AS_NEEDED:
            self._attr_native_value = round(days, 1)
        else:
            self._attr_native_value = float(math.floor(days))
        self._attr_extra_state_attributes = attrs


# =====================================================================
# Granular drink — DrinkDaysLeftSensor
# =====================================================================


class DrinkDaysLeftSensor(RestoreSensor):
    """Est. days left for a single granular drink device.

    Drinks have no schedule, so doses/day is the 7-day average from the
    granular :class:`DrinkCoordinator`.  The stock comes from the matching
    :class:`DrinkStockNumber` (``{entry_id}_drink_stock`` unique_id).
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:calendar-clock"
    _attr_should_poll = False
    _attr_translation_key = "days_left_est"

    def __init__(self, entry: ConfigEntry, coordinator: DrinkCoordinator) -> None:
        self._entry = entry
        self._coordinator = coordinator
        self._substance = entry.data.get("drink_type")
        self._attr_unique_id = f"{entry.entry_id}_drink_days_left"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get("name", entry.title),
            manufacturer="AX Dose Logger",
            model="Drink",
        )
        self._stock_entity_id: str | None = None
        self._unsub_stock: callable | None = None

    async def async_added_to_hass(self) -> None:
        """Restore last value, resolve stock entity, subscribe to updates."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            self._attr_native_value = float(last_state.native_value)

        ent_reg = er.async_get(self.hass)
        self._stock_entity_id = ent_reg.async_get_entity_id("number", DOMAIN, f"{self._entry.entry_id}_drink_stock")
        if self._stock_entity_id:
            self._unsub_stock = async_track_state_change_event(
                self.hass, self._stock_entity_id, self._stock_state_changed
            )
        self._update_state()
        self.async_write_ha_state()
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_coordinator_update))

    async def async_will_remove_from_hass(self) -> None:
        """Clean up the stock state-change subscription."""
        if self._unsub_stock:
            self._unsub_stock()
            self._unsub_stock = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute on dose-history changes (dose/undo/reset + 1-min tick)."""
        self._update_state()
        self.async_write_ha_state()

    @callback
    def _stock_state_changed(self, _event) -> None:
        """Recompute when the drink Inventory number entity changes."""
        self._update_state()
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # State computation
    # ------------------------------------------------------------------

    def _read_stock(self) -> float:
        if not self._stock_entity_id:
            return 0.0
        state = self.hass.states.get(self._stock_entity_id)
        if state is None or state.state in (None, "unknown", "unavailable"):
            return 0.0
        try:
            return max(0.0, float(state.state))
        except ValueError, TypeError:
            return 0.0

    def _doses_per_day_avg(self) -> float | None:
        """Empirical doses/day from the trailing 7-day granular history."""
        if not self._coordinator.data or not self._coordinator.data.dose_history:
            return None
        now = dt_util.now()
        cutoff = now - timedelta(days=_AVG_WINDOW_DAYS)
        timestamps = [ts for ts, _ in self._coordinator.data.dose_history]
        valid = [ts for ts in timestamps if ts >= cutoff]
        if not valid:
            return None
        return len(valid) / float(_AVG_WINDOW_DAYS)

    def _update_state(self) -> None:
        stock = self._read_stock()
        dpd = self._doses_per_day_avg()
        attrs: dict[str, object] = {
            "stock": int(stock) if stock.is_integer() else stock,
            "doses_per_day": round(dpd, 2) if dpd is not None else None,
            "estimation": True,
            "window_days": _AVG_WINDOW_DAYS,
            "substance": self._substance,
            "device_type": "drink",
            "role": "days_left",
        }
        if dpd is None or dpd <= 0:
            self._attr_native_value = None
            self._attr_extra_state_attributes = attrs
            return
        self._attr_native_value = round(stock / dpd, 1)
        self._attr_extra_state_attributes = attrs
