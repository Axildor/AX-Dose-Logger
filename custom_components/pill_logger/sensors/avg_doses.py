from datetime import timedelta, datetime, time, date
from homeassistant.components.sensor import RestoreSensor
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_change, async_call_later
from homeassistant.core import callback
import homeassistant.util.dt as dt_util
from ..const import DOMAIN

class PillAvgDosesSensor(RestoreSensor):
    _attr_has_entity_name = True
    should_poll = False

    def __init__(self, entry, window_days, sensor_name):
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._window_days_target = window_days
        self._attr_name = sensor_name
        self._attr_unique_id = f"{entry.entry_id}_avg_doses_{window_days}"
        self._attr_icon = "mdi:chart-bell-curve"
        self._entry_id = entry.entry_id
        self._timestamps = []
        self._history_start_date = None
        self._attr_extra_state_attributes = {"timestamps": [], "history_start_date": None}
        self._attr_native_value = 0.0
        self._tracking_type = entry.data.get("tracking_type")
        self._attr_state_class = "measurement"
        self._next_dose_timeout_unsub = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_taken_{self._entry_id}", self.pill_taken)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_reset_{self._entry_id}", self.reset_data)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_undone_{self._entry_id}", self.pill_undone)
        )
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._on_midnight, hour=0, minute=0, second=0
            )
        )
        last_state_obj = await self.async_get_last_state()
        if last_state_obj:
            if "timestamps" in last_state_obj.attributes:
                saved_timestamps = last_state_obj.attributes["timestamps"]
                for ts_str in saved_timestamps:
                    dt = dt_util.parse_datetime(ts_str)
                    if dt:
                        self._timestamps.append(dt)
            if "history_start_date" in last_state_obj.attributes and last_state_obj.attributes["history_start_date"]:
                self._history_start_date = dt_util.parse_datetime(last_state_obj.attributes["history_start_date"])
        if self._history_start_date is None:
            self._history_start_date = dt_util.now()
        self._update_state()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        if self._next_dose_timeout_unsub:
            self._next_dose_timeout_unsub()
            self._next_dose_timeout_unsub = None

    @callback
    def _on_midnight(self, now):
        self._update_state()
        self.async_write_ha_state()

    @callback
    def pill_taken(self, timestamp, *args, **kwargs):
        """Handle pill_taken signal with synchronized timestamp payload."""
        self._timestamps.append(timestamp)
        self._update_state()
        self.async_write_ha_state()

    @callback
    def pill_undone(self, *args, **kwargs):
        """Handle pill_undone signal: remove the most recent timestamp."""
        if self._timestamps:
            self._timestamps.pop()
        self._update_state()
        self.async_write_ha_state()

    @callback
    def reset_data(self, *args, **kwargs):
        self._timestamps = []
        self._history_start_date = dt_util.now()
        self._update_state()
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )

    def _get_grace_hours(self):
        """Calculate grace period based on tracking type and schedule.

        Returns hours of grace period. For scheduled modes, this is
        25% of the dosing interval, clamped to [1, 6] hours.
        For As Needed, returns 0 (no schedule to align to).
        """
        if self._tracking_type == "As Needed":
            return 0.0
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if self._tracking_type == "Regular Interval":
            hours_between = entry.options.get(
                "hours_between_doses", entry.data.get("hours_between_doses", 24)
            )
        else:
            # Time of Day and Cyclic: daily dosing = 24h interval
            hours_between = 24
        # 25% of interval, clamped to [1, 6] hours
        return max(1.0, min(6.0, hours_between * 0.25))

    def _count_covered_slots_time_of_day(self, now, cutoff, grace_td):
        """Count covered dose slots for Time of Day tracking.

        For each day in the window, check if any dose was taken
        within ±grace of the target time on that day. Days where
        the dose window is still open and no dose covers the slot
        are skipped (not counted as covered or uncovered).
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        time_of_day = entry.options.get(
            "time_of_day", entry.data.get("time_of_day", "08:00")
        )
        try:
            target_hour, target_minute = map(int, time_of_day.split(":"))
        except (ValueError, AttributeError):
            target_hour, target_minute = 8, 0

        covered = 0
        total_days = 0
        day_offset = 0
        while True:
            check_date = (now - timedelta(days=day_offset)).date()
            expected_time = datetime.combine(
                check_date, time(target_hour, target_minute),
                tzinfo=now.tzinfo
            )
            if expected_time < cutoff:
                break

            # Check if any dose covers this slot
            dose_covers = any(
                abs((ts - expected_time).total_seconds()) <= grace_td.total_seconds()
                for ts in self._timestamps
            )

            # Skip today if the dose window is still open and no dose covers it
            if day_offset == 0 and now < expected_time + grace_td and not dose_covers:
                day_offset += 1
                continue

            total_days += 1
            if dose_covers:
                covered += 1
            day_offset += 1

        return covered, float(total_days) if total_days > 0 else 0.0

    def _count_covered_slots_regular_interval(self, now, cutoff, grace_td):
        """Count covered dose slots for Regular Interval tracking.

        Generate expected dose times from the most recent dose going backward
        at hours_between_doses intervals. For each expected time, check if
        any actual dose falls within ±grace.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        hours_between = entry.options.get(
            "hours_between_doses", entry.data.get("hours_between_doses", 24)
        )
        interval_td = timedelta(hours=hours_between)

        if not self._timestamps:
            return 0, 0.0

        # Use most recent dose as anchor
        anchor = self._timestamps[-1]
        covered = 0
        total_slots = 0
        slot_time = anchor

        while slot_time >= cutoff:
            total_slots += 1
            # Check if any actual dose is within ±grace of this slot
            for ts in self._timestamps:
                if abs((ts - slot_time).total_seconds()) <= grace_td.total_seconds():
                    covered += 1
                    break
            slot_time -= interval_td

        # Calculate window days for the denominator
        window_days = max(1.0, (now - cutoff).total_seconds() / 86400.0)
        return covered, window_days

    def _count_covered_slots_cyclic(self, now, cutoff, grace_td):
        """Count covered dose slots for Cyclic/Calendar Pattern tracking.

        For each ON day in the window, check if any dose was taken
        within ±grace of the dose time on that day. OFF days are
        included in the denominator but never count as covered.
        Days where the dose window is still open and no dose covers
        the slot are skipped.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        days_on = entry.options.get("days_on", entry.data.get("days_on", 5))
        days_off = entry.options.get("days_off", entry.data.get("days_off", 2))
        anchor_str = entry.options.get(
            "cycle_anchor_date", entry.data.get("cycle_anchor_date")
        )
        dose_time_str = entry.options.get(
            "dose_time", entry.data.get("dose_time", "08:00")
        )

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

        covered = 0
        total_days = 0
        day_offset = 0
        while True:
            check_date = (now - timedelta(days=day_offset)).date()
            expected_time = datetime.combine(
                check_date, time(dose_hour, dose_minute),
                tzinfo=now.tzinfo
            )
            if expected_time < cutoff:
                break

            # Determine if this is an ON day
            days_since_anchor = (check_date - anchor_date).days
            position_in_cycle = days_since_anchor % cycle_length
            is_on_day = position_in_cycle < days_on

            # Check if any dose covers this slot
            dose_covers = any(
                abs((ts - expected_time).total_seconds()) <= grace_td.total_seconds()
                for ts in self._timestamps
            )

            # Skip today if the dose window is still open and no dose covers it
            if day_offset == 0 and now < expected_time + grace_td and not dose_covers:
                day_offset += 1
                continue

            total_days += 1
            if is_on_day and dose_covers:
                covered += 1
            day_offset += 1

        return covered, float(total_days) if total_days > 0 else 0.0

    def _get_next_dose_time(self):
        now = dt_util.now()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if self._tracking_type == "Regular Interval":
            hours_between = entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 0))
            if self._timestamps:
                return self._timestamps[-1] + timedelta(hours=hours_between)
            else:
                return now
        elif self._tracking_type == "Time of Day":
            time_of_day = entry.options.get("time_of_day", entry.data.get("time_of_day"))
            if time_of_day:
                try:
                    target_hour, target_minute = map(int, time_of_day.split(":"))
                except (ValueError, AttributeError):
                    target_hour, target_minute = 8, 0
                target_today = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
                if self._timestamps:
                    last_ts = self._timestamps[-1]
                    if last_ts.date() == now.date():
                        return target_today + timedelta(days=1)
                    else:
                        return target_today
        return None

    def _update_state(self):
        now = dt_util.now()
        if not self._history_start_date:
            self._history_start_date = now
        days_since_start = (now - self._history_start_date).total_seconds() / 86400.0
        days_since_start = max(1.0, days_since_start)
        actual_window_days = min(days_since_start, float(self._window_days_target))

        grace_hours = self._get_grace_hours()
        grace_td = timedelta(hours=grace_hours)

        # Base cutoff for the window
        base_cutoff = now - timedelta(days=actual_window_days)

        # For scheduled modes, extend the cutoff by grace period to preserve
        # doses near the window boundary for slot coverage checks
        if self._tracking_type != "As Needed" and grace_hours > 0:
            extended_cutoff = base_cutoff - grace_td
        else:
            extended_cutoff = base_cutoff

        # Prune timestamps that are outside the extended window
        self._timestamps = [ts for ts in self._timestamps if ts >= extended_cutoff]

        # Calculate average based on tracking type
        if self._tracking_type == "As Needed" or grace_hours == 0:
            # As Needed: use simple sliding window (no schedule to align to)
            if actual_window_days > 0:
                doses_in_window = [ts for ts in self._timestamps if ts >= base_cutoff]
                avg = len(doses_in_window) / actual_window_days
                self._attr_native_value = round(avg, 1)
            else:
                self._attr_native_value = 0.0
        elif self._tracking_type == "Time of Day":
            covered, total_days = self._count_covered_slots_time_of_day(
                now, base_cutoff, grace_td
            )
            if total_days > 0:
                self._attr_native_value = round(covered / total_days, 1)
            else:
                self._attr_native_value = 0.0
        elif self._tracking_type == "Regular Interval":
            covered, window_days = self._count_covered_slots_regular_interval(
                now, base_cutoff, grace_td
            )
            if window_days > 0:
                self._attr_native_value = round(covered / window_days, 1)
            else:
                self._attr_native_value = 0.0
        elif self._tracking_type == "Cyclic/Calendar Pattern":
            covered, total_days = self._count_covered_slots_cyclic(
                now, base_cutoff, grace_td
            )
            if total_days > 0:
                self._attr_native_value = round(covered / total_days, 1)
            else:
                self._attr_native_value = 0.0
        else:
            # Fallback: simple sliding window
            if actual_window_days > 0:
                doses_in_window = [ts for ts in self._timestamps if ts >= base_cutoff]
                avg = len(doses_in_window) / actual_window_days
                self._attr_native_value = round(avg, 1)
            else:
                self._attr_native_value = 0.0

        self._attr_extra_state_attributes = {
            "timestamps": [ts.isoformat() for ts in self._timestamps],
            "history_start_date": self._history_start_date.isoformat() if self._history_start_date else None,
            "grace_hours": grace_hours,
        }

        # Schedule a recalculation when the next dose's grace period expires
        if self._tracking_type in ("Time of Day", "Regular Interval"):
            next_dose = self._get_next_dose_time()
            if next_dose:
                grace_expiry = next_dose + grace_td
                if grace_expiry > now:
                    if self._next_dose_timeout_unsub:
                        self._next_dose_timeout_unsub()
                    delta_seconds = (grace_expiry - now).total_seconds()
                    self._next_dose_timeout_unsub = async_call_later(
                        self.hass, delta_seconds, self._on_next_dose_timeout
                    )

    @callback
    def _on_next_dose_timeout(self, now):
        self._update_state()
        self.async_write_ha_state()
        self._next_dose_timeout_unsub = None
