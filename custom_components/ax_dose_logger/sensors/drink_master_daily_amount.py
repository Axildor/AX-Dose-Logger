"""Master Tracker — 24-hour intake amount sensor (caffeine / alcohol).

Hosted on the virtual Caffeine Tracker / Alcohol Tracker devices created
by the Drink Settings singleton.  Mirrors :class:`PillDailyAmountSensor`
but subscribes to the matching :class:`DrinkMasterCoordinator` so it
aggregates **every** drink of that substance across all granular drink
devices, not just one drink type.

The aggregated ``dose_history`` entries are 3-tuples
``(datetime, strength, t_dur_hours)`` — the timestamp is index 0 and the
strength is index 1.

Per-substance daily limits are configurable in the Drink Settings entry:
* caffeine — ``caffeine_daily_limit_mg`` (FDA default 400 mg).
* alcohol — ``alcohol_daily_limit_g`` (no FDA default; 0 = no limit).

Both are re-read on every coordinator update so options-flow edits apply
on the next 1-min tick without a reload.
"""

from datetime import timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback

from ..const import (
    ALCOHOL_DEFAULT_LIMIT_G,
    CAFFEINE_DEFAULT_LIMIT_MG,
    DRINK_TYPE_ALCOHOL,
    DRINK_TYPE_CAFFEINE,
)
from ..drink_coordinator import DrinkMasterCoordinator
from ._tracker_info import tracker_device_info

# Fixed 24-hour rolling window for this sensor.
_WINDOW_HOURS = 24

# Sensor-specific keys per substance (common keys live in MASTER_TRACKERS).
# ``unit`` is retained here for the limit-lookup context (read alongside
# limit_key/default_limit in _read_daily_limit + _update_state).
_SENSOR_INFO = {
    DRINK_TYPE_CAFFEINE: {
        "unique_id_stem": "drink_master_daily_amount_caffeine",
        "translation_key": "drink_master_daily_amount_caffeine",
        "icon": "mdi:calendar-clock",
        "unit": "mg",
        "limit_key": "caffeine_daily_limit_mg",
        "default_limit": float(CAFFEINE_DEFAULT_LIMIT_MG),
    },
    DRINK_TYPE_ALCOHOL: {
        "unique_id_stem": "drink_master_daily_amount_alcohol",
        "translation_key": "drink_master_daily_amount_alcohol",
        "icon": "mdi:calendar-clock",
        "unit": "g",
        "limit_key": "alcohol_daily_limit_g",
        "default_limit": float(ALCOHOL_DEFAULT_LIMIT_G),
    },
}


class DrinkMasterDailyAmountSensor(RestoreSensor):
    """Total strength of one substance consumed in the last 24 hours.

    Subscribes to the shared :class:`DrinkMasterCoordinator` (one per
    substance) so it aggregates every logged drink across all granular
    drink devices.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_should_poll = False

    def __init__(
        self,
        settings_entry,
        coordinator: DrinkMasterCoordinator,
    ) -> None:
        """Initialize the substance-aggregate 24h amount sensor.

        ``settings_entry`` is the Drink Settings config entry that owns the
        per-substance daily limit fields; it is read on every coordinator
        update so options-flow edits apply without a reload.
        """
        info = _SENSOR_INFO[coordinator.substance]
        self._coordinator = coordinator
        self._substance = coordinator.substance
        self._settings_entry = settings_entry
        self._unit = info["unit"]
        self._limit_key = info["limit_key"]
        self._default_limit = info["default_limit"]
        # Stable unique_id — survives Drink Settings entry recreation, mirrors
        # the master PK sensor's drink_master_{substance} pattern.
        self._attr_unique_id = info["unique_id_stem"]
        self._attr_translation_key = info["translation_key"]
        self._attr_icon = info["icon"]
        self._attr_native_unit_of_measurement = self._unit
        # Stable device identifiers — standalone virtual Master Tracker device,
        # not tied to entry_id (see DrinkMasterSensor for the rationale).
        self._attr_device_info = tracker_device_info(self._substance)
        self._update_state()

    async def async_added_to_hass(self) -> None:
        """Restore last value, then subscribe to the master coordinator."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            self._attr_native_value = float(last_state.native_value)
        self._update_state()
        self.async_write_ha_state()
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_coordinator_update))

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute the 24h sum on coordinator updates (dose + 1-min tick)."""
        self._update_state()
        self.async_write_ha_state()

    def _read_daily_limit(self) -> float:
        """Read the per-substance daily limit from Drink Settings options/data.

        Falls back to the documented default (400 mg caffeine / 0 g alcohol).
        """
        opts = self._settings_entry.options
        data = self._settings_entry.data
        return float(
            opts.get(
                self._limit_key,
                data.get(self._limit_key, self._default_limit),
            )
        )

    def _update_state(self) -> None:
        """Sum dose strengths whose timestamp falls in the last 24h."""
        now = dt_util.now()
        cutoff = now - timedelta(hours=_WINDOW_HOURS)
        amount = 0.0
        doses_in_window = 0
        if self._coordinator.data and self._coordinator.data.dose_history:
            for ts, strength, _t_dur in self._coordinator.data.dose_history:
                if ts >= cutoff:
                    amount += float(strength)
                    doses_in_window += 1

        limit_raw = self._read_daily_limit()
        limit = limit_raw if limit_raw > 0 else None
        remaining = round(limit - amount, 3) if limit is not None else None

        self._attr_native_value = round(amount, 3)
        self._attr_extra_state_attributes = {
            "window_hours": _WINDOW_HOURS,
            "doses_in_window": doses_in_window,
            "daily_limit": limit,
            "remaining": remaining,
            "unit_of_measurement": self._unit,
            "substance": self._substance,
            "drink_master": True,  # Frontend filter marker
            "role": "daily_amount",  # Frontend classifier (survives entity_id renames)
        }
