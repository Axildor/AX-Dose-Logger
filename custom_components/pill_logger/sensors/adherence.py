"""Adherence percentage sensor for Pill Logger integration."""

from datetime import timedelta, datetime, time, date
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_change, async_call_later
from homeassistant.core import callback
from homeassistant.const import STATE_UNKNOWN
import homeassistant.util.dt as dt_util
from ..const import DOMAIN


class PillAdherenceSensor(RestoreSensor):
    """Sensor that calculates rolling adherence percentage over a configurable window.

    Adherence % = min(actual_doses / expected_doses * 100, 100)

    The grace period is user-configurable via `adherence_grace_hours` in the
    config entry (default: 1 hour). A dose is considered "on time" if it falls
    within ±grace of the expected slot time.

    For As Needed (PRN) medications, adherence is undefined — the sensor
    returns None with a descriptive reason attribute.
    """

    should_poll = False

    def __init__(self, entry, window_days, sensor_name):
        """Initialize the adherence sensor.

        Args:
            entry: The config entry object.
            window_days: Fixed trailing window size (7, 14, 30, or 365).
            sensor_name: Display name suffix (e.g. "Adherence (7 Days)").
        """
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._window_days = window_days
        self._attr_name = f"{med_name} {sensor_name}"
        self._attr_unique_id = f"{entry.entry_id}_adherence_{window_days}"
        self._attr_icon = "mdi:check-decagram"
        self._entry_id = entry.entry_id
        self._tracking_type = entry.data.get("tracking_type")
        self._timestamps = []
        self._history_start_date = None
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "%"
        self._attr_suggested_display_precision = 0
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "timestamps": [],
            "history_start_date": None,
        }
        self._next_dose_timeout_unsub = None

    async def async_added_to_hass(self):
        """Set up dispatcher listeners and restore state."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"pill_taken_{self._entry_id}", self.pill_taken
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"pill_reset_{self._entry_id}", self.reset_data
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"pill_undone_{self._entry_id}", self.pill_undone
            )
        )
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._on_midnight, hour=0, minute=0, second=0
            )
        )

        # Restore timestamps and history_start_date from last state
        last_state_obj = await self.async_get_last_state()
        if last_state_obj:
            if "timestamps" in last_state_obj.attributes:
                saved_timestamps = last_state_obj.attributes["timestamps"]
                for ts_str in saved_timestamps:
                    dt = dt_util.parse_datetime(ts_str)
                    if dt:
                        self._timestamps.append(dt)
            if (
                "history_start_date" in last_state_obj.attributes
                and last_state_obj.attributes["history_start_date"]
            ):
                self._history_start_date = dt_util.parse_datetime(
                    last_state_obj.attributes["history_start_date"]
                )
        if self._history_start_date is None:
            self._history_start_date = dt_util.now()

        self._update_state()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        """Clean up scheduled timeouts."""
        if self._next_dose_timeout_unsub:
            self._next_dose_timeout_unsub()
            self._next_dose_timeout_unsub = None

    @callback
    def _on_midnight(self, now):
        """Recalculate at midnight (day boundary changes expected doses)."""
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
        """Clear all timestamps and reset history start date."""
        self._timestamps = []
        self._history_start_date = dt_util.now()
        self._update_state()
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for grouping under the medication device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )

    def _get_grace_hours(self):
        """Get the user-configured adherence grace period.

        Reads `adherence_grace_hours` from the config entry with a default
        of 1 hour. This is the only configurable parameter for adherence.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return entry.options.get(
            "adherence_grace_hours",
            entry.data.get("adherence_grace_hours", 1.0),
        )

    # ------------------------------------------------------------------
    # Slot-counting methods per tracking type
    # ------------------------------------------------------------------

    def _count_slots_time_of_day(self, now, cutoff, grace_td):
        """Count actual and expected dose slots for Time of Day tracking.

        For each day in the window, check if any dose was taken within
        ±grace of the target time. Days where the dose window is still
        open and no dose covers the slot are skipped (pending).

        Returns:
            (actual_doses, expected_doses)
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        time_of_day = entry.options.get(
            "time_of_day", entry.data.get("time_of_day", "08:00")
        )
        try:
            target_hour, target_minute = map(int, time_of_day.split(":"))
        except (ValueError, AttributeError):
            target_hour, target_minute = 8, 0

        actual = 0
        expected = 0
        day_offset = 0
        while True:
            check_date = (now - timedelta(days=day_offset)).date()
            expected_time = datetime.combine(
                check_date, time(target_hour, target_minute),
                tzinfo=now.tzinfo,
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

            expected += 1
            if dose_covers:
                actual += 1
            day_offset += 1

        return actual, expected

    def _count_slots_regular_interval(self, now, cutoff, grace_td):
        """Count actual and expected dose slots for Regular Interval tracking.

        Uses last-dose-anchored backward chain + forward gap filling.
        The backward chain generates expected slots from the most recent
        dose stepping backward by hours_between_doses. The forward gap
        generates expected slots after the last dose that were missed.

        Returns:
            (actual_doses, expected_doses)
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        hours_between = entry.options.get(
            "hours_between_doses", entry.data.get("hours_between_doses", 24)
        )
        interval_td = timedelta(hours=hours_between)

        if not self._timestamps:
            # No doses at all — count expected slots from cutoff to now
            # anchored at midnight boundaries
            expected = 0
            slot_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            while slot_time >= cutoff:
                # Generate all slots for this day
                day_slot = slot_time
                while day_slot <= now:
                    if day_slot >= cutoff and day_slot + grace_td <= now:
                        expected += 1
                    day_slot += interval_td
                slot_time -= timedelta(days=1)
            return 0, expected

        # Use most recent dose as anchor
        anchor = self._timestamps[-1]
        actual = 0
        backward_expected = 0

        # Backward chain from last dose
        slot_time = anchor
        while slot_time >= cutoff:
            backward_expected += 1
            # Check if any actual dose is within ±grace of this slot
            for ts in self._timestamps:
                if abs((ts - slot_time).total_seconds()) <= grace_td.total_seconds():
                    actual += 1
                    break
            slot_time -= interval_td

        # Forward gap: expected slots after the last dose that were missed
        forward_expected = 0
        forward_time = anchor + interval_td
        while forward_time <= now:
            # Skip if this slot's grace window is still open (pending)
            if forward_time + grace_td > now:
                break
            forward_expected += 1
            # Forward slots are by definition uncovered (no dose after anchor
            # covers them, otherwise the anchor would be later)
            forward_time += interval_td

        expected = backward_expected + forward_expected
        return actual, expected

    def _count_slots_cyclic(self, now, cutoff, grace_td):
        """Count actual and expected dose slots for Cyclic/Calendar Pattern.

        For each ON day in the window, check if any dose was taken within
        ±grace of the dose time. OFF days are excluded from both numerator
        and denominator. Days where the dose window is still open are skipped.

        Returns:
            (actual_doses, expected_doses)
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

        actual = 0
        expected = 0
        day_offset = 0
        while True:
            check_date = (now - timedelta(days=day_offset)).date()
            expected_time = datetime.combine(
                check_date, time(dose_hour, dose_minute),
                tzinfo=now.tzinfo,
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

            # Only count ON days in the denominator
            if is_on_day:
                expected += 1
                if dose_covers:
                    actual += 1
            day_offset += 1

        return actual, expected

    def _get_next_dose_time(self):
        """Get the next expected dose time for scheduling grace expiry.

        Used to proactively schedule a recalculation when a slot transitions
        from pending to missed.
        """
        now = dt_util.now()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if self._tracking_type == "Regular Interval":
            hours_between = entry.options.get(
                "hours_between_doses", entry.data.get("hours_between_doses", 0)
            )
            if self._timestamps:
                return self._timestamps[-1] + timedelta(hours=hours_between)
            else:
                return now
        elif self._tracking_type == "Time of Day":
            time_of_day = entry.options.get(
                "time_of_day", entry.data.get("time_of_day")
            )
            if time_of_day:
                try:
                    target_hour, target_minute = map(int, time_of_day.split(":"))
                except (ValueError, AttributeError):
                    target_hour, target_minute = 8, 0
                target_today = now.replace(
                    hour=target_hour, minute=target_minute, second=0, microsecond=0
                )
                if self._timestamps:
                    last_ts = self._timestamps[-1]
                    if last_ts.date() == now.date():
                        return target_today + timedelta(days=1)
                    else:
                        return target_today
                else:
                    return target_today
        elif self._tracking_type == "Cyclic/Calendar Pattern":
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
            days_since_anchor = (now.date() - anchor_date).days
            position_in_cycle = days_since_anchor % cycle_length
            dose_time_today = now.replace(
                hour=dose_hour, minute=dose_minute, second=0, microsecond=0
            )
            if position_in_cycle >= days_on:
                # OFF day — next dose is start of next ON period
                days_until_next_on = cycle_length - position_in_cycle
                return dose_time_today + timedelta(days=days_until_next_on)
            else:
                # ON day
                if now < dose_time_today:
                    return dose_time_today
                else:
                    days_until_next_on = cycle_length - position_in_cycle
                    if days_until_next_on == 0:
                        days_until_next_on = cycle_length
                    return dose_time_today + timedelta(days=days_until_next_on)
        return None

    def _update_state(self):
        """Recalculate adherence percentage based on tracking type."""
        now = dt_util.now()

        # PRN medications: adherence is undefined
        if self._tracking_type == "As Needed":
            self._attr_native_value = None
            self._attr_extra_state_attributes = {
                "reason": "PRN medications do not track adherence",
                "window_days": self._window_days,
                "timestamps": [ts.isoformat() for ts in self._timestamps],
                "history_start_date": (
                    self._history_start_date.isoformat()
                    if self._history_start_date
                    else None
                ),
            }
            return

        if not self._history_start_date:
            self._history_start_date = now

        days_since_start = (now - self._history_start_date).total_seconds() / 86400.0
        days_since_start = max(1.0, days_since_start)
        effective_window_days = min(days_since_start, float(self._window_days))

        grace_hours = self._get_grace_hours()
        grace_td = timedelta(hours=grace_hours)

        # Base cutoff for the window
        base_cutoff = now - timedelta(days=effective_window_days)

        # For scheduled modes, extend the cutoff by grace period to preserve
        # doses near the window boundary for slot coverage checks
        if grace_hours > 0:
            extended_cutoff = base_cutoff - grace_td
        else:
            extended_cutoff = base_cutoff

        # Prune timestamps that are outside the extended window
        self._timestamps = [ts for ts in self._timestamps if ts >= extended_cutoff]

        # Calculate adherence based on tracking type
        if self._tracking_type == "Time of Day":
            actual, expected = self._count_slots_time_of_day(
                now, base_cutoff, grace_td
            )
        elif self._tracking_type == "Regular Interval":
            actual, expected = self._count_slots_regular_interval(
                now, base_cutoff, grace_td
            )
        elif self._tracking_type == "Cyclic/Calendar Pattern":
            actual, expected = self._count_slots_cyclic(
                now, base_cutoff, grace_td
            )
        else:
            # Fallback for unknown tracking types
            actual, expected = 0, 0

        # Calculate percentage
        if expected > 0:
            raw_pct = (actual / expected) * 100
            # Clamp at 100% (over-dosing should not show > 100%)
            self._attr_native_value = min(round(raw_pct), 100)
        else:
            # No expected doses in window
            self._attr_native_value = None

        # Build attributes
        attrs = {
            "actual_doses": actual,
            "expected_doses": expected,
            "window_days": self._window_days,
            "effective_window_days": round(effective_window_days, 1),
            "grace_hours": grace_hours,
            "timestamps": [ts.isoformat() for ts in self._timestamps],
            "history_start_date": (
                self._history_start_date.isoformat()
                if self._history_start_date
                else None
            ),
        }
        if expected == 0 and self._tracking_type != "As Needed":
            attrs["reason"] = "No scheduled doses in window"

        self._attr_extra_state_attributes = attrs

        # Schedule a recalculation when the next dose's grace period expires
        if self._tracking_type in ("Time of Day", "Regular Interval", "Cyclic/Calendar Pattern"):
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
        """Recalculate when a dose slot transitions from pending to missed."""
        self._update_state()
        self.async_write_ha_state()
        self._next_dose_timeout_unsub = None