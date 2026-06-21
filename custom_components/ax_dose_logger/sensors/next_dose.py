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
)
from ..entity import AxDoseLoggerSensorEntity
from ..sliding_window import compute_safe_to_take, is_on_day

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
        # Legacy restore for smooth UI transition; coordinator is
        # authoritative so _handle_coordinator_update overrides.
        last_state_obj = await self.async_get_last_state()
        if last_state_obj and "timestamps" in last_state_obj.attributes:
            self._update_state()
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator (dose event or 1-min tick)."""
        self._update_state()
        self.async_write_ha_state()

    def _get_timestamps(self) -> list:
        """Read dose timestamps from the coordinator."""
        if self.coordinator.data:
            return [ts for ts, _ in self.coordinator.data.dose_history]
        return []

    def _update_state(self):
        now = dt_util.now()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        timestamps = self._get_timestamps()

        if self._tracking_type == TRACKING_REGULAR_INTERVAL:
            hours_between = entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 0))
            if timestamps:
                last_ts = timestamps[-1]
                self._attr_native_value = last_ts + timedelta(hours=hours_between)
            else:
                self._attr_native_value = now
        elif self._tracking_type == TRACKING_TIME_OF_DAY:
            self._update_state_time_of_day(entry, now, timestamps)
        elif self._tracking_type == TRACKING_CYCLIC:
            days_on = entry.options.get("days_on", entry.data.get("days_on", 5))
            days_off = entry.options.get("days_off", entry.data.get("days_off", 2))
            anchor_str = entry.options.get("cycle_anchor_date", entry.data.get("cycle_anchor_date"))
            dose_time_str = entry.options.get("dose_time", entry.data.get("dose_time", "08:00"))
            try:
                anchor_date = date.fromisoformat(anchor_str)
            except (ValueError, TypeError):
                anchor_date = now.date()
            try:
                dose_hour, dose_minute = map(int, dose_time_str.split(":"))
            except (ValueError, AttributeError):
                dose_hour, dose_minute = 8, 0

            cycle_length = days_on + days_off
            if cycle_length <= 0:
                cycle_length = 1
            days_since_anchor = (now.date() - anchor_date).days
            position_in_cycle = days_since_anchor % cycle_length

            dose_time_today = now.replace(hour=dose_hour, minute=dose_minute, second=0, microsecond=0)

            if not is_on_day(entry, now.date(), now.date()):
                days_until_next_on = cycle_length - position_in_cycle
                self._attr_native_value = dose_time_today + timedelta(days=days_until_next_on)
            elif timestamps:
                last_ts = timestamps[-1]
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
            cutoff_for_pill_limit = now - timedelta(hours=time_window)
            valid_timestamps_for_calc = [ts for ts in timestamps if ts >= cutoff_for_pill_limit]
            pills_remaining = max(0, max_pills - len(valid_timestamps_for_calc))
            if pills_remaining == 0 and valid_timestamps_for_calc:
                self._attr_native_value = valid_timestamps_for_calc[0] + timedelta(hours=time_window)
            elif timestamps:
                self._attr_native_value = timestamps[-1]
            else:
                self._attr_native_value = None

        safe_to_take = compute_safe_to_take(entry, timestamps, now, self._tracking_type)

        # Prune timestamps to last 365 days and cap at 100 entries
        cutoff = now - timedelta(days=_TIMESTAMPS_MAX_DAYS)
        recent = [ts for ts in timestamps if ts >= cutoff][-_TIMESTAMPS_MAX_COUNT:]
        self._attr_extra_state_attributes = {
            "timestamps": [ts.isoformat() for ts in recent],
            "safe_to_take": safe_to_take,
            "tracking_type": self._tracking_type,
        }

    def _update_state_time_of_day(self, entry, now, timestamps):
        """Compute next dose time for Time of Day mode with multi-daily dose support."""
        parsed_times = get_dose_times(entry)

        if not parsed_times:
            self._attr_native_value = now
            return

        today_slots = []
        for hour, minute in parsed_times:
            slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            today_slots.append(slot_time)

        if len(parsed_times) >= 2:
            min_gap_minutes = 24 * 60
            for i in range(len(parsed_times)):
                for j in range(i + 1, len(parsed_times)):
                    gap = (parsed_times[j][0] * 60 + parsed_times[j][1]) - (parsed_times[i][0] * 60 + parsed_times[i][1])
                    min_gap_minutes = min(min_gap_minutes, gap)
            grace_minutes = max(30, min_gap_minutes // 2)
        else:
            grace_minutes = 60

        grace_td = timedelta(minutes=grace_minutes)

        for slot_time in today_slots:
            covered = False
            for ts in timestamps:
                if abs((ts - slot_time).total_seconds()) <= grace_td.total_seconds():
                    covered = True
                    break

            if not covered and slot_time > now:
                self._attr_native_value = slot_time
                return

        first_hour, first_minute = parsed_times[0]
        tomorrow = now + timedelta(days=1)
        self._attr_native_value = tomorrow.replace(
            hour=first_hour, minute=first_minute, second=0, microsecond=0
        )
