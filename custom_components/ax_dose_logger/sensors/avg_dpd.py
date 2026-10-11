"""
Avg Doses per Day sensor for Pill Logger integration.

The true doses-per-day average the "Avg Daily Doses" name always implied:
``doses logged in the trailing window / effective window days``. This
mirrors :class:`DrinkAvgDosesSensor` (the drink path already computed a
plain count/days average) so pills and drinks finally report the same
kind of number.

Every logged dose counts — including multiple doses on the same day and
the pills-per-dose multiplier is NOT applied (one log entry = one dose
event, matching the Total Doses counter and the day bar graph).

Window semantics (shared with the Day Coverage sensor): exactly
``window_days`` calendar days ending today (inclusive), shrunk to the
history anchor (or the Reset Averages anchor) so early history is not
penalized with empty days. Day bucketing uses the local calendar date
via :func:`local_date`, mirroring the frontend ``_toLocalDateKey()``.
"""

from datetime import timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback

from ..entity import AxDoseLoggerSensorEntity
from ..sliding_window import local_date


class PillAvgDosesPerDaySensor(AxDoseLoggerSensorEntity, RestoreSensor):
    """
    Rolling average doses per day over a fixed trailing window.

    State = doses in window / effective window days, rounded to 1 decimal.
    """

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator, window_days):
        super().__init__(entry, coordinator)
        self._window_days_target = window_days
        self._attr_translation_key = "avg_doses_per_day"
        self._attr_translation_placeholders = {"window": str(window_days)}
        self._attr_unique_id = f"{entry.entry_id}_avg_dpd_{window_days}"
        self._attr_icon = "mdi:chart-bell-curve"
        self._attr_suggested_display_precision = 1
        self._history_start_date = None
        self._attr_extra_state_attributes = {"timestamps": [], "history_start_date": None}
        self._attr_native_value = 0.0
        self._attr_state_class = SensorStateClass.MEASUREMENT

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Restore history_start_date; coordinator dose_history is
        # authoritative so _handle_coordinator_update overrides the value.
        last_state_obj = await self.async_get_last_state()
        if last_state_obj:
            if last_state_obj.attributes.get("history_start_date"):
                self._history_start_date = dt_util.parse_datetime(last_state_obj.attributes["history_start_date"])
        # Anchor to earliest dose from coordinator dose_history
        if self.coordinator.data and self.coordinator.data.dose_history:
            earliest = min(ts for ts, _ in self.coordinator.data.dose_history)
            self._history_start_date = earliest
        if self._history_start_date is None:
            self._history_start_date = dt_util.now()
        self._update_state()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Handle updated data from the coordinator (dose event or 1-min tick).

        The 1-min coordinator tick covers midnight rollover — no
        separate ``async_track_time_change`` timer needed.
        """
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self):
        """Compute doses in window / effective window days."""
        now = dt_util.now()
        if not self._history_start_date:
            self._history_start_date = now
        # Averages reset anchor (Reset Averages tool): clamp the effective
        # window start so pre-reset doses stop counting toward the average.
        # The raw history_start_date attribute is NOT modified — the
        # frontend reads it for the days-since reveal logic.
        avg_reset = self.coordinator.data.avg_reset_time if self.coordinator.data else None
        effective_start = self._history_start_date
        if avg_reset is not None and avg_reset > effective_start:
            effective_start = avg_reset

        today = local_date(now)
        # Trailing window: exactly N calendar days ending today (inclusive),
        # shrunk to the history/reset anchor.
        window_start_date = today - timedelta(days=self._window_days_target - 1)
        anchor_date = local_date(effective_start)
        window_start_date = max(window_start_date, anchor_date)
        effective_window_days = (today - window_start_date).days + 1

        timestamps = []
        if self.coordinator.data:
            timestamps = [ts for ts, _ in self.coordinator.data.dose_history]
        # Day-bucket filter (tz-safe local calendar date, mirrors frontend).
        valid_timestamps = [ts for ts in timestamps if local_date(ts) >= window_start_date]

        if effective_window_days > 0:
            self._attr_native_value = round(len(valid_timestamps) / effective_window_days, 1)
        else:
            self._attr_native_value = 0.0

        self._attr_extra_state_attributes = {
            # Machine-readable role: rename-safe card resolution (same
            # pattern as the day_coverage / daily_amount roles).
            "role": "avg_doses_per_day",
            "doses_in_window": len(valid_timestamps),
            "window_days": self._window_days_target,
            "effective_window_days": effective_window_days,
            "timestamps": [ts.isoformat() for ts in valid_timestamps],
            "history_start_date": self._history_start_date.isoformat() if self._history_start_date else None,
            "averages_reset_time": avg_reset.isoformat() if avg_reset else None,
        }
