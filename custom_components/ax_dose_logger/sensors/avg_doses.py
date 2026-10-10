"""
Day-coverage (PDC) sensor for Pill Logger integration.

The "Day Coverage" sensor measures **day-level dose coverage** (PDC-aligned):
the percentage of scheduled days in the trailing window on which at least
one dose was taken. This is the pharmacy-claims gold standard
(Proportion of Days Covered — CMS / NCQA / PQA / WHO 80% threshold):
binary per day, no timing gate, no partial credit.

A dose taken at any time on a scheduled day counts that day as covered,
so a late-but-taken dose (e.g. 7h after the scheduled time) does NOT
drag the coverage below 100%. Timing quality is reported separately by
the Adherence % sensor, which is a strict ±grace timing metric. The raw
doses-per-day count is reported separately by the Avg Doses per Day
sensor (``avg_dpd.py``).

Day bucketing uses the local calendar date, mirroring the frontend
``_toLocalDateKey()`` so the coverage and the 14-day bar graph can never
disagree on which day a dose belongs to.

Window semantics (v2): the window is exactly ``window_days`` **calendar
days ending today, today included and always in the denominator**. The
pre-v2 implementation counted ``cutoff.date() .. yesterday`` plus a
conditionally-added today, which yielded 8 calendar days for a "7-day"
window and made the denominator jump mid-day when the first dose of the
day was logged. Early history is still handled gracefully: the window
shrinks to the days elapsed since the history anchor (or the Reset
Averages anchor) so a medication set up today is not penalized with 6
empty days.
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
    Rolling day-coverage sensor (PDC-aligned).

    State = 100 × covered scheduled days / scheduled days in the trailing
    window, in percent. Range 0–100.
    """

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator, window_days):
        super().__init__(entry, coordinator)
        self._window_days_target = window_days
        self._attr_translation_key = "day_coverage"
        self._attr_translation_placeholders = {"window": str(window_days)}
        # unique_id unchanged from the pre-rename "avg doses" era so the
        # entity registry keeps the same entities (no migration, no
        # orphaned old ids, state history preserved).
        self._attr_unique_id = f"{entry.entry_id}_avg_doses_{window_days}"
        self._attr_icon = "mdi:chart-bell-curve"
        self._history_start_date = None
        self._attr_native_unit_of_measurement = "%"
        self._attr_suggested_display_precision = 0
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

    # ------------------------------------------------------------------
    # Day-coverage counting (PDC-aligned)
    # ------------------------------------------------------------------

    def _count_daily_days(self, window_start_date, dose_dates, today):
        """Count scheduled days and covered days for non-cyclic regimens.

        Every calendar day from *window_start_date* through *today*
        (inclusive) is a scheduled day — today is ALWAYS in the
        denominator so the basis does not change when the first dose of
        the day is logged.
        """
        covered = 0
        total = 0
        d = window_start_date
        while d <= today:
            total += 1
            if d in dose_dates:
                covered += 1
            d += timedelta(days=1)
        return total, covered

    def _count_cyclic_days(self, window_start_date, dose_dates, today):
        """Count scheduled (ON) days and covered days for cyclic regimens."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)

        covered = 0
        total = 0
        d = window_start_date
        while d <= today:
            if is_on_day(entry, d, today):
                total += 1
                if d in dose_dates:
                    covered += 1
            d += timedelta(days=1)
        return total, covered

    # ------------------------------------------------------------------
    # State computation
    # ------------------------------------------------------------------

    def _update_state(self):
        """Recalculate the day-coverage percentage over the trailing window."""
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
        # Trailing window: exactly N calendar days ending today (inclusive).
        # Shrunk to the history/reset anchor so early history is not
        # penalized with empty days before the first dose.
        window_start_date = today - timedelta(days=self._window_days_target - 1)
        anchor_date = local_date(effective_start)
        window_start_date = max(window_start_date, anchor_date)
        scheduled_days = (today - window_start_date).days + 1

        timestamps = []
        if self.coordinator.data:
            timestamps = [ts for ts, _ in self.coordinator.data.dose_history]
        # Day-bucket filter: keep doses whose LOCAL calendar date falls in
        # the window (tz-safe — a UTC-stored timestamp maps to the user's
        # local day, mirroring the frontend ``_toLocalDateKey()``).
        valid_timestamps = [ts for ts in timestamps if local_date(ts) >= window_start_date]

        dose_dates = {local_date(ts) for ts in valid_timestamps}

        if self._tracking_type == TRACKING_CYCLIC:
            scheduled_days, covered_days = self._count_cyclic_days(window_start_date, dose_dates, today)
        else:
            scheduled_days, covered_days = self._count_daily_days(window_start_date, dose_dates, today)

        if scheduled_days > 0:
            self._attr_native_value = round(covered_days / scheduled_days * 100, 1)
        else:
            self._attr_native_value = 0.0

        self._attr_extra_state_attributes = {
            # Machine-readable role: the card resolves this sensor by role
            # (rename-safe) because HA does not re-slug entity_id when the
            # friendly name changes — the entity keeps its legacy
            # `_avg_daily_doses_N_days` entity_id after the Day Coverage rename.
            "role": "day_coverage",
            "covered_days": covered_days,
            "scheduled_days": scheduled_days,
            "window_days": self._window_days_target,
            "effective_window_days": scheduled_days,
            "timestamps": [ts.isoformat() for ts in valid_timestamps],
            "history_start_date": self._history_start_date.isoformat() if self._history_start_date else None,
            "averages_reset_time": avg_reset.isoformat() if avg_reset else None,
        }
