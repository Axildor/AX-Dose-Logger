"""Days-left inventory-burn sensors.

A "Days left" sensor answers: *at the current consumption rate, how many
days will the current inventory last?*  The raw Pills Left / Inventory count
only equals days left when exactly one dose is taken per day.  For multi-dose
schedules, As-Needed medications, and drinks, the count must be divided by the
doses taken per day.

Three variants exist because the integration has three device families, each
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

* :class:`DrinkMasterDaysLeftSensor` (Master Tracker devices — Caffeine /
  Alcohol)
    - The Master Tracker aggregates every granular drink of a substance, so
      doses/day is the 7-day average from the aggregated
      ``DrinkMasterCoordinator``.  There is no single stock number on the
      Master Tracker, so the sensor **sums every granular drink inventory of
      that substance** resolved via the config/entity registries.  Named
      **"Est. days left"**.

All variants read the live stock state from the HA state machine (single
source of truth = the ``RestoreNumber`` inventory entity) and refresh on two
push sources: the coordinator (dose-history changes that move the average)
and ``async_track_state_change_event`` on the stock entity/entities (dose /
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event

from ..const import (
    ALCOHOL_TRACKER_ID,
    CAFFEINE_TRACKER_ID,
    DEVICE_CATEGORY_DRINKS,
    DOMAIN,
    DRINK_TYPE_ALCOHOL,
    DRINK_TYPE_CAFFEINE,
    TRACKING_AS_NEEDED,
    TRACKING_REGULAR_INTERVAL,
    TRACKING_TIME_OF_DAY,
    get_dose_times,
)
from ..coordinator import AxDoseLoggerCoordinator
from ..drink_coordinator import DrinkCoordinator, DrinkMasterCoordinator
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
        self._stock_entity_id = ent_reg.async_get_entity_id(
            "number", DOMAIN, f"{self._entry_id}_stock"
        )
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
        except (ValueError, TypeError):
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
        # Cyclic — one dose per ON-day.  OFF-days consume nothing, so the
        # count pauses during them; dividing by 1 is the honest representation.
        return 1.0

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

    def __init__(
        self, entry: ConfigEntry, coordinator: DrinkCoordinator
    ) -> None:
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
        self._stock_entity_id = ent_reg.async_get_entity_id(
            "number", DOMAIN, f"{self._entry.entry_id}_drink_stock"
        )
        if self._stock_entity_id:
            self._unsub_stock = async_track_state_change_event(
                self.hass, self._stock_entity_id, self._stock_state_changed
            )
        self._update_state()
        self.async_write_ha_state()
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

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
        except (ValueError, TypeError):
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


# =====================================================================
# Master Tracker — DrinkMasterDaysLeftSensor
# =====================================================================

# Stable device identifiers + per-substance translation key + unique-id stem.
_MASTER_TRACKER_INFO = {
    DRINK_TYPE_CAFFEINE: {
        "tracker_id": CAFFEINE_TRACKER_ID,
        "unique_id_stem": "drink_master_days_left_caffeine",
        "translation_key": "days_left_est_master_caffeine",
        "icon": "mdi:calendar-clock",
    },
    DRINK_TYPE_ALCOHOL: {
        "tracker_id": ALCOHOL_TRACKER_ID,
        "unique_id_stem": "drink_master_days_left_alcohol",
        "translation_key": "days_left_est_master_alcohol",
        "icon": "mdi:calendar-clock",
    },
}


class DrinkMasterDaysLeftSensor(RestoreSensor):
    """Est. days left for a Master Tracker (Caffeine / Alcohol).

    The Master Tracker has no single stock number — it aggregates every
    granular drink inventory of its substance.  This sensor resolves all
    granular drink config entries of the substance via the config/entity
    registries, sums their ``DrinkStockNumber`` states, and listens for state
    changes on **all** of them.  The avg comes from the aggregated
    :class:`DrinkMasterCoordinator` dose history.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_suggested_display_precision = 1
    _attr_should_poll = False

    def __init__(
        self,
        settings_entry: ConfigEntry,
        coordinator: DrinkMasterCoordinator,
        hass: HomeAssistant,
    ) -> None:
        info = _MASTER_TRACKER_INFO[coordinator.substance]
        self._coordinator = coordinator
        self._substance = coordinator.substance
        self._attr_unique_id = info["unique_id_stem"]
        self._attr_translation_key = info["translation_key"]
        self._attr_icon = info["icon"]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info["tracker_id"])},
            manufacturer="AX Dose Logger",
            model="Master Tracker",
        )
        self._stock_entity_ids: list[str] = []
        self._unsub_stock: callable | None = None
        self._update_state()

    async def async_added_to_hass(self) -> None:
        """Restore last value, resolve stock entities, subscribe to updates."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            self._attr_native_value = float(last_state.native_value)

        self._resolve_stock_entities()
        if self._stock_entity_ids:
            self._unsub_stock = async_track_state_change_event(
                self.hass, self._stock_entity_ids, self._stock_state_changed
            )
        self._update_state()
        self.async_write_ha_state()
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up the stock state-change subscription."""
        if self._unsub_stock:
            self._unsub_stock()
            self._unsub_stock = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute on master dose-history changes (dose/undo/reset + tick)."""
        self._update_state()
        self.async_write_ha_state()

    @callback
    def _stock_state_changed(self, _event) -> None:
        """Recompute when any tracked drink inventory changes."""
        self._update_state()
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Stock-entity resolution
    # ------------------------------------------------------------------

    def _resolve_stock_entities(self) -> None:
        """Resolve every DrinkStockNumber of this substance via the registries.

        Iterates all ax_dose_logger config entries, selects drink entries whose
        ``drink_type`` matches this substance, and resolves their
        ``{entry_id}_drink_stock`` unique_id to a number entity_id.
        """
        ids: list[str] = []
        ent_reg = er.async_get(self.hass)
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.data.get("device_category") != DEVICE_CATEGORY_DRINKS:
                continue
            if entry.data.get("drink_type") != self._substance:
                continue
            eid = ent_reg.async_get_entity_id(
                "number", DOMAIN, f"{entry.entry_id}_drink_stock"
            )
            if eid:
                ids.append(eid)
        self._stock_entity_ids = ids

    # ------------------------------------------------------------------
    # State computation
    # ------------------------------------------------------------------

    def _read_stock(self) -> float:
        """Sum the live state of every tracked drink inventory entity."""
        total = 0.0
        for eid in self._stock_entity_ids:
            state = self.hass.states.get(eid)
            if state is None or state.state in (None, "unknown", "unavailable"):
                continue
            try:
                total += max(0.0, float(state.state))
            except (ValueError, TypeError):
                continue
        return total

    def _doses_per_day_avg(self) -> float | None:
        """Empirical doses/day from the trailing 7-day aggregated history."""
        if not self._coordinator.data or not self._coordinator.data.dose_history:
            return None
        now = dt_util.now()
        cutoff = now - timedelta(days=_AVG_WINDOW_DAYS)
        timestamps = [ts for ts, _, _ in self._coordinator.data.dose_history]
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
            "drink_master": True,
            "stock_entities": len(self._stock_entity_ids),
            "role": "days_left",
        }
        if dpd is None or dpd <= 0:
            self._attr_native_value = None
            self._attr_extra_state_attributes = attrs
            return
        self._attr_native_value = round(stock / dpd, 1)
        self._attr_extra_state_attributes = attrs
