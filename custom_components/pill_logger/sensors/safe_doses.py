from datetime import timedelta, date
from homeassistant.components.sensor import RestoreSensor
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.core import callback
import homeassistant.util.dt as dt_util
from ..const import DOMAIN

class PillSafeDosesSensor(RestoreSensor):
    should_poll = False

    def __init__(self, entry):
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._attr_name = f"{med_name} Safe Doses"
        self._attr_unique_id = f"{entry.entry_id}_safe_doses"
        self._attr_icon = "mdi:pill"
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
    def pill_taken(self, *args, **kwargs):
        self._timestamps.append(dt_util.now())
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

    @callback
    def _on_interval(self, now):
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self):
        now = dt_util.now()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        max_pills = entry.options.get("safe_doses", entry.data.get("safe_doses", entry.data.get("max_pills_allowed", 1)))

        if self._tracking_type == "As Needed":
            time_window = entry.options.get("time_window_hours", entry.data.get("time_window_hours", 0))
            cutoff = now - timedelta(hours=time_window)
            self._timestamps = [ts for ts in self._timestamps if ts >= cutoff]
            self._attr_native_value = max(0, max_pills - len(self._timestamps))
        elif self._tracking_type == "Regular Interval":
            hours_between = entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 0))
            if self._timestamps:
                last_ts = self._timestamps[-1]
                if now - last_ts < timedelta(hours=hours_between):
                    self._attr_native_value = 0
                else:
                    self._attr_native_value = max_pills
            else:
                self._attr_native_value = max_pills
        elif self._tracking_type == "Time of Day":
            time_of_day = entry.options.get("time_of_day", entry.data.get("time_of_day"))
            if time_of_day:
                try:
                    target_hour, target_minute = map(int, time_of_day.split(":"))
                except (ValueError, AttributeError):
                    target_hour, target_minute = 8, 0
                if self._timestamps:
                    last_ts = self._timestamps[-1]
                    release_time = last_ts.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0) + timedelta(days=1)
                    if now >= release_time:
                        self._attr_native_value = max_pills
                    else:
                        self._attr_native_value = 0
                else:
                    self._attr_native_value = max_pills
            else:
                self._attr_native_value = max_pills
        elif self._tracking_type == "Cyclic/Calendar Pattern":
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
            if position_in_cycle < days_on:
                self._attr_native_value = max_pills
            else:
                self._attr_native_value = 0

        self._attr_extra_state_attributes = {
            "timestamps": [ts.isoformat() for ts in self._timestamps]
        }
