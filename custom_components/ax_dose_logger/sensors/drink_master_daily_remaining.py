"""Master Tracker — daily-limit remaining sensor (caffeine / alcohol).

Companion to :class:`DrinkMasterDailyAmountSensor` exposing the **remaining
daily allowance** as a standalone entity: per-substance ``daily_limit −
amount_24h`` (mg caffeine / g alcohol).  A negative value means the daily
limit is already exceeded (overage shown as e.g. ``-50.0``).

Promoted from the ``remaining`` attribute of the Amount in Last 24h sensor so
automations, dashboards, and history graphs can consume the value directly
without template sensors.  The ``remaining`` attribute stays on the host
sensor (deprecated, not removed) so existing user templates keep working.

Hosted on the virtual Caffeine Tracker / Alcohol Tracker devices created by
the Drink Settings singleton.  Mirrors :class:`DrinkMasterDailyAmountSensor`
but subscribes to the matching :class:`DrinkMasterCoordinator` so it
aggregates **every** drink of that substance across all granular drink
devices.

Per-substance daily limits are configurable in the Drink Settings entry:
* caffeine — ``caffeine_daily_limit_mg`` (FDA default 400 mg).
* alcohol — ``alcohol_daily_limit_g`` (no FDA default; 0 = no limit).

Created only when the per-substance limit > 0 (caffeine's 400 mg default
always qualifies; alcohol is skipped unless a limit is configured) — no dead
entity when no limit is set.  The limit is re-read on every coordinator
update so options-flow edits apply on the next tick without a reload.
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
    master_unique_id,
)
from ..drink_coordinator import DrinkMasterCoordinator
from ._tracker_info import tracker_device_info

# Fixed 24-hour rolling window for this sensor (mirrors the daily-amount sensor).
_WINDOW_HOURS = 24

# Sensor-specific keys per substance (common keys live in MASTER_TRACKERS).
_SENSOR_INFO = {
    DRINK_TYPE_CAFFEINE: {
        "translation_key": "drink_master_daily_remaining_caffeine",
        "icon": "mdi:progress-clock",
        "unit": "mg",
        "limit_key": "caffeine_daily_limit_mg",
        "default_limit": float(CAFFEINE_DEFAULT_LIMIT_MG),
    },
    DRINK_TYPE_ALCOHOL: {
        "translation_key": "drink_master_daily_remaining_alcohol",
        "icon": "mdi:progress-clock",
        "unit": "g",
        "limit_key": "alcohol_daily_limit_g",
        "default_limit": float(ALCOHOL_DEFAULT_LIMIT_G),
    },
}


class DrinkMasterDailyRemainingSensor(RestoreSensor):
    """Remaining daily allowance (limit − amount in last 24h) for a substance.

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
        profile_id: str,
        profile_name: str | None,
    ) -> None:
        """Initialize the substance-aggregate daily-remaining sensor."""
        info = _SENSOR_INFO[coordinator.substance]
        self._coordinator = coordinator
        self._substance = coordinator.substance
        self._profile_id = profile_id
        self._profile_name = profile_name
        self._settings_entry = settings_entry
        self._unit = info["unit"]
        self._limit_key = info["limit_key"]
        self._default_limit = info["default_limit"]
        # Stable unique_id — survives Drink Settings entry recreation.
        # Distinct `_daily_remaining` suffix avoids collision with the
        # body-mass DrinkMasterSensor (owns the bare master_unique_id);
        # mirrors the daily_amount / avg sibling suffix pattern.
        self._attr_unique_id = f"{master_unique_id(profile_id, self._substance)}_daily_remaining"
        self._attr_translation_key = info["translation_key"]
        self._attr_icon = info["icon"]
        self._attr_native_unit_of_measurement = self._unit
        # Stable device identifiers — standalone virtual Master Tracker device,
        # not tied to entry_id (see DrinkMasterSensor for the rationale).
        self._attr_device_info = tracker_device_info(profile_id, self._substance, profile_name=profile_name)
        # Initial state is computed in async_added_to_hass (which also
        # calls async_write_ha_state); computing here is redundant and
        # would be immediately overwritten.

    async def async_added_to_hass(self) -> None:
        """Compute initial state from coordinator data, then subscribe.

        Mirrors :class:`DrinkMasterDailyAmountSensor`: the master coordinator
        has already run ``_async_setup`` + first refresh by the time this
        runs, so ``_update_state`` produces the correct current value from
        live data.  No restore-from-storage (the recompute would immediately
        overwrite it; a stale restored value could briefly show an
        out-of-date remaining allowance).
        """
        await super().async_added_to_hass()
        self._update_state()
        self.async_write_ha_state()
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_coordinator_update))

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute the remaining allowance on coordinator updates (dose + 1-min tick)."""
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
        """Compute limit − amount_24h (negative = overage)."""
        now = dt_util.now()
        cutoff = now - timedelta(hours=_WINDOW_HOURS)
        amount = 0.0
        if self._coordinator.data and self._coordinator.data.dose_history:
            for ts, strength, _t_dur in self._coordinator.data.dose_history:
                if ts >= cutoff:
                    amount += float(strength)

        limit_raw = self._read_daily_limit()
        limit = limit_raw if limit_raw > 0 else None
        remaining = round(limit - amount, 3) if limit is not None else None

        self._attr_native_value = remaining
        self._attr_extra_state_attributes = {
            "window_hours": _WINDOW_HOURS,
            "daily_limit": limit,
            "amount_24h": round(amount, 3),
            "unit_of_measurement": self._unit,
            "substance": self._substance,
            "drink_master": True,  # Frontend filter marker
            "role": "daily_remaining",  # Frontend classifier (survives entity_id renames)
        }
