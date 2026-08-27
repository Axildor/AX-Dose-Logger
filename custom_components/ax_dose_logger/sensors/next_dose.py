from datetime import date, timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass
from homeassistant.core import callback

from ..const import (
    TRACKING_AS_NEEDED,
    TRACKING_CYCLIC,
    TRACKING_REGULAR_INTERVAL,
    TRACKING_TIME_OF_DAY,
    get_dose_times,
    parse_dose_time,
)
from ..entity import AxDoseLoggerSensorEntity
from ..schedule import LATENESS_UNTIL_NEXT_SLOT, compute_slot_assignments
from ..sliding_window import compute_safe_to_take, effective_dose_buffer_minutes, is_on_day

# Cap for timestamps attribute: prune older than 365 days, keep last 100
_TIMESTAMPS_MAX_DAYS = 365
_TIMESTAMPS_MAX_COUNT = 100


class PillNextDoseSensor(AxDoseLoggerSensorEntity, RestoreSensor):
    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "next_dose"
        self._attr_unique_id = f"{entry.entry_id}_next_dose"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._tracking_type = entry.data.get("tracking_type")
        self._attr_extra_state_attributes = {"timestamps": []}
        self._attr_native_value = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Coordinator is authoritative - compute initial state from live
        # data so the sensor shows a real next-dose time immediately on
        # first-ever setup, instead of sitting at `unknown` until the
        # first 1-min coordinator tick. A stale restored timestamp
        # could show an already-passed dose time, so we deliberately do
        # not restore from last_state here.
        self._update_state()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator (dose event or 1-min tick)."""
        self._update_state()
        self.async_write_ha_state()

    def _get_timestamps(self) -> list:
        """Read dose timestamps from the coordinator (doses only, no skips).

        Used for the ``safe_to_take`` attribute and the As Needed branch,
        where a skip must NOT consume a pill-limit slot. The scheduled
        branches use :meth:`_get_schedule_timestamps` which merges skips
        so a skip advances the next-dose schedule.
        """
        if self.coordinator.data:
            return [ts for ts, _ in self.coordinator.data.dose_history]
        return []

    def _get_schedule_timestamps(self) -> list:
        """Read dose timestamps + skipped slots for schedule calculations.

        Deliberately-skipped slots are merged in so a skip covers the
        missed scheduled slot and advances next_dose WITHOUT logging a
        real dose. This mirrors the overdue sensor. Adherence deliberately
        ignores skips (stays penalized) — see the adherence sensor.
        """
        if not self.coordinator.data:
            return []
        real = [ts for ts, _ in self.coordinator.data.dose_history]
        skipped = list(self.coordinator.data.skipped_slots)
        return real + skipped

    def _update_state(self):
        now = dt_util.now()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        # Dose-only timestamps for safe_to_take + As Needed (a skip must
        # not consume a pill-limit slot) and for the exposed timestamps
        # attribute (informational; doses only).
        dose_timestamps = self._get_timestamps()
        # Merged timestamps (doses + skips) for schedule advancement.
        schedule_timestamps = self._get_schedule_timestamps()

        if self._tracking_type == TRACKING_REGULAR_INTERVAL:
            hours_between = entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 0))
            if schedule_timestamps:
                # Chained-deadline model (parity with the overdue sensor and
                # the Time of Day slot model): the next dose is the next
                # *future* chained deadline ``anchor + (n+1) * interval``,
                # not the stale fixed ``anchor + interval``.  When a dose
                # time is missed, next_dose advances to the following
                # deadline so the reminder blueprint re-arms at each missed
                # dose time instead of looping on one past timestamp.
                interval = timedelta(hours=hours_between)
                # max() not [-1]: adherence-override / undo flows can leave
                # the merged dose+skip list unsorted.
                anchor = max(schedule_timestamps)
                elapsed = now - anchor
                if elapsed <= timedelta(0):
                    self._attr_native_value = anchor + interval
                else:
                    n = int(elapsed.total_seconds() // interval.total_seconds())
                    self._attr_native_value = anchor + (n + 1) * interval
            else:
                self._attr_native_value = now
        elif self._tracking_type == TRACKING_TIME_OF_DAY:
            self._update_state_time_of_day(entry, now, schedule_timestamps)
        elif self._tracking_type == TRACKING_CYCLIC:
            # HA's NumberSelector stores these as floats; coerce to int
            # for correct modulo/cycle arithmetic.
            days_on = int(entry.options.get("days_on", entry.data.get("days_on", 5)))
            days_off = int(entry.options.get("days_off", entry.data.get("days_off", 2)))
            anchor_str = entry.options.get("cycle_anchor_date", entry.data.get("cycle_anchor_date"))
            dose_time_str = entry.options.get("dose_time", entry.data.get("dose_time", "08:00"))
            try:
                anchor_date = date.fromisoformat(anchor_str)
            except ValueError, TypeError:
                anchor_date = now.date()
            dose_hour, dose_minute = parse_dose_time(dose_time_str)

            cycle_length = days_on + days_off
            if cycle_length <= 0:
                cycle_length = 1
            days_since_anchor = (now.date() - anchor_date).days
            position_in_cycle = days_since_anchor % cycle_length

            dose_time_today = now.replace(hour=dose_hour, minute=dose_minute, second=0, microsecond=0)

            if not is_on_day(entry, now.date(), now.date()):
                days_until_next_on = cycle_length - position_in_cycle
                self._attr_native_value = dose_time_today + timedelta(days=days_until_next_on)
            elif schedule_timestamps:
                last_ts = schedule_timestamps[-1]
                if last_ts.date() == now.date() and now >= dose_time_today:
                    days_until_next_on = cycle_length - position_in_cycle
                    if days_until_next_on == 0:
                        days_until_next_on = cycle_length
                    self._attr_native_value = dose_time_today + timedelta(days=days_until_next_on)
                elif now < dose_time_today:
                    self._attr_native_value = dose_time_today
                else:
                    self._attr_native_value = dose_time_today
            elif now < dose_time_today:
                self._attr_native_value = dose_time_today
            else:
                days_until_next_on = cycle_length - position_in_cycle
                if days_until_next_on == 0:
                    days_until_next_on = cycle_length
                self._attr_native_value = dose_time_today + timedelta(days=days_until_next_on)

        elif self._tracking_type == TRACKING_AS_NEEDED:
            max_pills = entry.options.get("pill_limit", entry.data.get("pill_limit", 1))
            time_window = entry.options.get("time_window_hours", entry.data.get("time_window_hours", 0))
            # Anti-drift buffer: keep the As-Needed next-dose timestamp in
            # sync with pill_limit's buffered gate (the oldest dose expires
            # `buffer` minutes earlier, so the next-available moment is
            # window - buffer after the oldest dose). Mirrors the buffered
            # cutoff in compute_safe_to_take / PillLimitSensor.
            buffer_minutes = effective_dose_buffer_minutes(entry, float(time_window))
            cutoff_for_pill_limit = now - timedelta(hours=time_window) + timedelta(minutes=buffer_minutes)
            valid_timestamps_for_calc = [ts for ts in dose_timestamps if ts >= cutoff_for_pill_limit]
            pills_remaining = max(0, max_pills - len(valid_timestamps_for_calc))
            if pills_remaining == 0 and valid_timestamps_for_calc:
                self._attr_native_value = (
                    valid_timestamps_for_calc[0]
                    + timedelta(hours=time_window)
                    - timedelta(minutes=buffer_minutes)
                )
            elif dose_timestamps:
                self._attr_native_value = dose_timestamps[-1]
            else:
                self._attr_native_value = None

        safe_to_take = compute_safe_to_take(entry, dose_timestamps, now, self._tracking_type)

        # Prune timestamps to last 365 days and cap at 100 entries
        cutoff = now - timedelta(days=_TIMESTAMPS_MAX_DAYS)
        recent = [ts for ts in dose_timestamps if ts >= cutoff][-_TIMESTAMPS_MAX_COUNT:]
        self._attr_extra_state_attributes = {
            "timestamps": [ts.isoformat() for ts in recent],
            "safe_to_take": safe_to_take,
            "tracking_type": self._tracking_type,
        }

    def _update_state_time_of_day(self, entry, now, timestamps):
        """Compute next dose time for Time of Day mode with multi-daily dose support.

        Uses the shared greedy slot-assignment model so next_dose agrees
        with the overdue sensor on which slot a dose belongs to: a dose
        taken late for slot A but before the next slot B is the late A
        dose, so B remains the next *uncovered* dose.
        """
        parsed_times = get_dose_times(entry)

        if not parsed_times:
            self._attr_native_value = now
            return

        min_gap_minutes = 24 * 60
        if len(parsed_times) >= 2:
            for i in range(len(parsed_times)):
                for j in range(i + 1, len(parsed_times)):
                    gap = (parsed_times[j][0] * 60 + parsed_times[j][1]) - (
                        parsed_times[i][0] * 60 + parsed_times[i][1]
                    )
                    min_gap_minutes = min(min_gap_minutes, gap)
        else:
            min_gap_minutes = 24 * 60
        early_grace = timedelta(minutes=max(30, min_gap_minutes // 2))

        assignments = compute_slot_assignments(
            parsed_times,
            timestamps,
            now,
            lookback_days=1,
            future_days=1,
            early_grace=early_grace,
            lateness_mode=LATENESS_UNTIL_NEXT_SLOT,
        )

        for a in assignments:
            if not a.covered and a.slot_time > now:
                self._attr_native_value = a.slot_time
                return

        first_hour, first_minute = parsed_times[0]
        tomorrow = now + timedelta(days=1)
        self._attr_native_value = tomorrow.replace(hour=first_hour, minute=first_minute, second=0, microsecond=0)
