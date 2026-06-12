from datetime import timedelta, date, datetime
from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.core import callback
import homeassistant.util.dt as dt_util
from ..const import DOMAIN

class PillNextDoseSensor(RestoreSensor):
    _attr_has_entity_name = True
    should_poll = False

    def __init__(self, entry):
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._attr_name = "Next Dose"
        self._attr_unique_id = f"{entry.entry_id}_next_dose"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._entry_id = entry.entry_id
        self._tracking_type = entry.data.get("tracking_type")
        self._timestamps = []
        self._attr_extra_state_attributes = {"timestamps": []}
        self._attr_native_value = None

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
            async_track_time_interval(
                self.hass, self._on_interval, timedelta(minutes=1)
            )
        )

        last_state_obj = await self.async_get_last_state()
        if last_state_obj and "timestamps" in last_state_obj.attributes:
            saved_timestamps = last_state_obj.attributes["timestamps"]
            for ts_str in saved_timestamps:
                dt = dt_util.parse_datetime(ts_str)
                if dt:
                    self._timestamps.append(dt)
            self._update_state()
            self.async_write_ha_state()

    @callback
    def _on_interval(self, now):
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
        self._update_state()
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )

    def _get_time_window(self, entry):
        """Get time_window_hours with mode-specific fallbacks."""
        if self._tracking_type == "Regular Interval":
            return entry.options.get(
                "time_window_hours",
                entry.data.get(
                    "time_window_hours",
                    entry.options.get(
                        "hours_between_doses",
                        entry.data.get("hours_between_doses", 8)
                    )
                )
            )
        elif self._tracking_type == "As Needed":
            return entry.options.get(
                "time_window_hours",
                entry.data.get("time_window_hours", 8)
            )
        else:
            # Time of Day and Cyclic default to 24h
            return entry.options.get(
                "time_window_hours",
                entry.data.get("time_window_hours", 24)
            )

    def _compute_safe_to_take(self, entry, now):
        """Compute remaining safe doses using the unified sliding window."""
        max_pills = entry.options.get("safe_doses", entry.data.get("safe_doses", entry.data.get("max_pills_allowed", 1)))
        time_window = self._get_time_window(entry)
        cutoff = now - timedelta(hours=time_window)
        valid_timestamps = [ts for ts in self._timestamps if ts >= cutoff]
        safe_to_take = max(0, max_pills - len(valid_timestamps))

        # Cyclic OFF days: force safe_to_take to 0
        if self._tracking_type == "Cyclic/Calendar Pattern":
            days_on = entry.options.get("days_on", entry.data.get("days_on", 5))
            days_off = entry.options.get("days_off", entry.data.get("days_off", 2))
            anchor_str = entry.options.get("cycle_anchor_date", entry.data.get("cycle_anchor_date"))
            try:
                anchor_date = date.fromisoformat(anchor_str)
            except (ValueError, TypeError):
                anchor_date = now.date()
            cycle_length = days_on + days_off
            if cycle_length <= 0:
                cycle_length = 1
            days_since_anchor = (now.date() - anchor_date).days
            position_in_cycle = days_since_anchor % cycle_length
            if position_in_cycle >= days_on:
                safe_to_take = 0

        return safe_to_take

    def _update_state(self):
        now = dt_util.now()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)

        if self._tracking_type == "Regular Interval":
            hours_between = entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 0))
            if self._timestamps:
                last_ts = self._timestamps[-1]
                self._attr_native_value = last_ts + timedelta(hours=hours_between)
            else:
                self._attr_native_value = now
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
                        self._attr_native_value = target_today + timedelta(days=1)
                    else:
                        self._attr_native_value = target_today
                else:
                    self._attr_native_value = target_today
        elif self._tracking_type == "Cyclic/Calendar Pattern":
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

            if position_in_cycle >= days_on:
                # Currently in an OFF window — next dose is the start of the next ON period
                days_until_next_on = cycle_length - position_in_cycle
                self._attr_native_value = dose_time_today + timedelta(days=days_until_next_on)
            else:
                # Currently in an ON window
                if self._timestamps:
                    last_ts = self._timestamps[-1]
                    if last_ts.date() == now.date() and now >= dose_time_today:
                        # Already took dose today and it's past dose time — next ON day
                        days_until_next_on = cycle_length - position_in_cycle
                        if days_until_next_on == 0:
                            days_until_next_on = cycle_length
                        self._attr_native_value = dose_time_today + timedelta(days=days_until_next_on)
                    elif now < dose_time_today:
                        # Haven't taken dose yet today, and it's before dose time
                        self._attr_native_value = dose_time_today
                    else:
                        # Past dose time, no dose taken today yet — still available today
                        self._attr_native_value = dose_time_today
                else:
                    # No timestamps at all — next dose is the next dose_time
                    if now < dose_time_today:
                        self._attr_native_value = dose_time_today
                    else:
                        # Past dose time today, no history — assume dose was already taken
                        days_until_next_on = cycle_length - position_in_cycle
                        if days_until_next_on == 0:
                            days_until_next_on = cycle_length
                        self._attr_native_value = dose_time_today + timedelta(days=days_until_next_on)

        elif self._tracking_type == "As Needed":
            max_pills = entry.options.get("safe_doses", entry.data.get("safe_doses", entry.data.get("max_pills_allowed", 1)))
            time_window = entry.options.get("time_window_hours", entry.data.get("time_window_hours", 0))
            cutoff_for_safe_doses = now - timedelta(hours=time_window)
            valid_timestamps_for_calc = [ts for ts in self._timestamps if ts >= cutoff_for_safe_doses]
            safe_doses = max(0, max_pills - len(valid_timestamps_for_calc))
            if safe_doses == 0 and valid_timestamps_for_calc:
                self._attr_native_value = valid_timestamps_for_calc[0] + timedelta(hours=time_window)
            elif self._timestamps:
                self._attr_native_value = self._timestamps[-1]
            else:
                self._attr_native_value = None

        # Compute safe_to_take for ALL modes using unified sliding window
        safe_to_take = self._compute_safe_to_take(entry, now)

        self._attr_extra_state_attributes = {
            "timestamps": [ts.isoformat() for ts in self._timestamps],
            "safe_to_take": safe_to_take,
        }
