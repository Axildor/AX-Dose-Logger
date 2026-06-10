from datetime import timedelta
from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.core import callback
import homeassistant.util.dt as dt_util
from ..const import DOMAIN

class PillNextDoseSensor(RestoreSensor):
    def __init__(self, entry):
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._attr_name = f"{med_name} Next Dose"
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

    @callback
    def _on_interval(self, now):
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
            self._attr_extra_state_attributes = {
                "timestamps": [ts.isoformat() for ts in self._timestamps],
                "safe_doses_calculated": safe_doses
            }
        if self._tracking_type != "As Needed":
            self._attr_extra_state_attributes = {
                "timestamps": [ts.isoformat() for ts in self._timestamps]
            }

    @property
    def native_value(self):
        return self._attr_native_value
