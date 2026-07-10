"""
Average daily dose-coverage sensor for Pill Logger integration.

The "Day Average" measures **day-level dose coverage** (PDC-aligned):
the fraction of scheduled days in the trailing window on which at least
one dose was taken. This is the pharmacy-claims gold standard
(Proportion of Days Covered — CMS / NCQA / PQA / WHO 80% threshold):
binary per day, no timing gate, no partial credit.

A dose taken at any time on a scheduled day counts that day as covered,
so a late-but-taken dose (e.g. 7h after the scheduled time) does NOT
drag the average below 1.0. Timing quality is reported separately by
the Adherence % sensor, which is a strict ±grace timing metric.

Day bucketing uses the local calendar date, mirroring the frontend
``_toLocalDateKey()`` so the average and the 14-day bar graph can never
disagree on which day a dose belongs to.
"""

from datetime import timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback

from ..const import TRACKING_CYCLIC
from ..entity import AxDoseLoggerSensorEntity
from ..sliding_window import is_on_day, local_date


class PillAvgDosesSensor(AxDoseLoggerSensorEntity, RestoreSensor):
    """
    Rolling day-coverage average sensor (PDC-aligned).

    State = covered scheduled days / scheduled days in the trailing
    window, rounded to 1 decimal. Range 0.0–1.0.
    """

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator, window_days):
        super().__init__(entry, coordinator)
        self._window_days_target = window_days
        self._attr_translation_key = "avg_daily_doses"
        self._attr_translation_placeholders = {"window": str(window_days)}
        self._attr_unique_id = f"{entry.entry_id}_avg_doses_{window_days}"
        self._attr_icon = "mdi:chart-bell-curve"
        self._history_start_date = None
        self._attr_extra_state_attributes = {"timestamps": [], "history_start_date": None}
        self._attr_native_value = 0.0
        self._tracking_type = entry.data.get("tracking_type")
        self._attr_state_class = SensorStateClass.MEASUREMENT

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Legacy restore for history_start_date; coordinator is
        # authoritative for dose_history so _handle_coordinator_update
        # overrides the native_value.
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

    def _get_timestamps(self) -> list:
        """Read dose timestamps from the coordinator."""
        if self.coordinator.data:
            return [ts for ts, _ in self.coordinator.data.dose_history]
        return []

    # ------------------------------------------------------------------
    # Day-coverage counting (PDC-aligned)
    # ------------------------------------------------------------------

    def _count_daily_days(self, cutoff, dose_dates, today):
        """Count scheduled days and covered days for non-cyclic regimens."""
        covered = 0
        total = 0
        d = cutoff.date()
        while d < today:
            total += 1
            if d in dose_dates:
                covered += 1
            d += timedelta(days=1)
        if today in dose_dates:
            total += 1
            covered += 1
        return total, covered

    def _count_cyclic_days(self, now, cutoff, dose_dates, today):
        """Count scheduled (ON) days and covered days for cyclic regimens."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)

        covered = 0
        total = 0
        d = cutoff.date()
        while d < today:
            if is_on_day(entry, d, today):
                total += 1
                if d in dose_dates:
                    covered += 1
            d += timedelta(days=1)
        if today in dose_dates and is_on_day(entry, today, today):
            total += 1
            covered += 1
        return total, covered

    # ------------------------------------------------------------------
    # State computation
    # ------------------------------------------------------------------

    def _update_state(self):
        """Recalculate the day-coverage average over the trailing window."""
        now = dt_util.now()
        if not self._history_start_date:
            self._history_start_date = now
        days_since_start = (now - self._history_start_date).total_seconds() / 86400.0
        days_since_start = max(1.0, days_since_start)
        actual_window_days = min(days_since_start, float(self._window_days_target))

        base_cutoff = now - timedelta(days=actual_window_days)

        timestamps = self._get_timestamps()
        # Prune timestamps outside the window
        valid_timestamps = [ts for ts in timestamps if ts >= base_cutoff]

        dose_dates = {local_date(ts) for ts in valid_timestamps}
        today = local_date(now)

        if self._tracking_type == TRACKING_CYCLIC:
            scheduled_days, covered_days = self._count_cyclic_days(now, base_cutoff, dose_dates, today)
        else:
            scheduled_days, covered_days = self._count_daily_days(base_cutoff, dose_dates, today)

        if scheduled_days > 0:
            self._attr_native_value = round(covered_days / scheduled_days, 1)
        else:
            self._attr_native_value = 0.0

        self._attr_extra_state_attributes = {
            "covered_days": covered_days,
            "scheduled_days": scheduled_days,
            "window_days": self._window_days_target,
            "effective_window_days": round(actual_window_days, 1),
            "timestamps": [ts.isoformat() for ts in valid_timestamps],
            "history_start_date": self._history_start_date.isoformat() if self._history_start_date else None,
        }
