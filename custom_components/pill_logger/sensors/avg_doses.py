from datetime import timedelta
from homeassistant.components.sensor import RestoreSensor
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_change, async_call_later
from homeassistant.core import callback
import homeassistant.util.dt as dt_util
from ..const import DOMAIN

class PillAvgDosesSensor(RestoreSensor):
    should_poll = False

    def __init__(self, entry, window_days, sensor_name):
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._window_days_target = window_days
        self._attr_name = f"{med_name} {sensor_name}"
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
    def pill_taken(self, *args, **kwargs):
        self._timestamps.append(dt_util.now())
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
        cutoff = now - timedelta(days=actual_window_days)
        self._timestamps = [ts for ts in self._timestamps if ts >= cutoff]
        if actual_window_days > 0:
            avg = len(self._timestamps) / actual_window_days
            self._attr_native_value = round(avg, 1)
        else:
            self._attr_native_value = 0.0
        self._attr_extra_state_attributes = {
            "timestamps": [ts.isoformat() for ts in self._timestamps],
            "history_start_date": self._history_start_date.isoformat() if self._history_start_date else None
        }
        if self._tracking_type in ("Time of Day", "Regular Interval"):
            next_dose = self._get_next_dose_time()
            if next_dose:
                one_hour_after = next_dose + timedelta(hours=1)
                if one_hour_after > now:
                    if self._next_dose_timeout_unsub:
                        self._next_dose_timeout_unsub()
                    delta_seconds = (one_hour_after - now).total_seconds()
                    self._next_dose_timeout_unsub = async_call_later(
                        self.hass, delta_seconds, self._on_next_dose_timeout
                    )

    @callback
    def _on_next_dose_timeout(self, now):
        self._update_state()
        self.async_write_ha_state()
        self._next_dose_timeout_unsub = None
