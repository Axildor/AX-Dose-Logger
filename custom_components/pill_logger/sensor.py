from datetime import timedelta
from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval, async_track_time_change, async_call_later
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
import homeassistant.util.dt as dt_util
import math
from .const import DOMAIN  

SCAN_INTERVAL = timedelta(minutes=2)

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    med_name = entry.data["medication_name"]
    entities = [PillTotalSensor(med_name, entry.entry_id)]
    entities.append(PillLastDoseSensor(med_name, entry.entry_id))
    entities.append(PillSafeDosesSensor(entry))
    entities.append(PillConcentrationSensor(entry))
    entities.append(PillNextDoseSensor(entry))
    entities.append(PillAvgDosesSensor(entry, 7, "Avg Daily Doses (7 Days)"))
    entities.append(PillAvgDosesSensor(entry, 30, "Avg Daily Doses (30 Days)"))
    entities.append(PillAvgDosesSensor(entry, 365, "Avg Daily Doses (Yearly)"))
    entities.append(PillSteadyStateSensor(entry))
    entities.append(PillStrengthSensor(entry))
    async_add_entities(entities)  

class PillTotalSensor(RestoreSensor):
    def __init__(self, name, entry_id):
        self._med_name = name
        self._attr_name = f"{name} Total Doses"
        self._attr_unique_id = f"{entry_id}_total"
        self._attr_icon = "mdi:chart-line"
        self._entry_id = entry_id
        self._state = 0  

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )  

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_taken_{self._entry_id}", self.increment)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_reset_{self._entry_id}", self.reset_data)
        )
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            self._state = int(last_state.native_value)  

    @property
    def native_value(self):
        return self._state  

    @callback
    def increment(self, *args, **kwargs):
        self._state += 1
        self.async_write_ha_state()

    @callback
    def reset_data(self, *args, **kwargs):
        self._state = 0
        self.async_write_ha_state()  


class PillSafeDosesSensor(RestoreSensor):
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
            async_track_time_change(
                self.hass, self._on_midnight, hour=0, minute=0, second=0
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
    def _on_midnight(self, now):
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
            
        self._attr_extra_state_attributes = {
            "timestamps": [ts.isoformat() for ts in self._timestamps]
        }  

    @property
    def native_value(self):
        return self._attr_native_value  


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


class PillLastDoseSensor(RestoreSensor):
    def __init__(self, name, entry_id):
        self._med_name = name
        self._attr_name = f"{name} Last Dose"
        self._attr_unique_id = f"{entry_id}_last_dose"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._entry_id = entry_id
        self._state = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_taken_{self._entry_id}", self._update_last_dose)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_reset_{self._entry_id}", self._reset_data)
        )
        last_state = await self.async_get_last_sensor_data()
        if last_state and last_state.native_value is not None:
            self._state = last_state.native_value

    @property
    def native_value(self):
        return self._state

    @callback
    def _update_last_dose(self, *args, **kwargs):
        self._state = dt_util.now()
        self.async_write_ha_state()

    @callback
    def _reset_data(self, *args, **kwargs):
        self._state = None
        self.async_write_ha_state()


class PillAvgDosesSensor(RestoreSensor):
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

    @callback
    def _on_midnight(self, now):
        self._update_state()
        self.async_write_ha_state()

    @callback
    def pill_taken(self, *args, **kwargs):
        self._timestamps.append(dt_util.now())
        if self._tracking_type in ("Time of Day", "Regular Interval"):
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

    @property
    def native_value(self):
        return self._attr_native_value


class PillConcentrationSensor(RestoreSensor):
    def __init__(self, entry):
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._attr_name = f"{med_name} Concentration"
        self._attr_unique_id = f"{entry.entry_id}_concentration"
        self._attr_icon = "mdi:chart-bell-curve"
        self._entry_id = entry.entry_id
        self._strength = entry.data.get("strength", 0)
        self._half_life = entry.data.get("half_life", 0)
        self._hours_to_peak = entry.data.get("hours_to_peak", 0.0)
        self._current_mass = 0.0
        self._gut_mass = 0.0
        self._last_updated = None
        self._ka = 0.0
        self._attr_state_class = "measurement"
        self._attr_native_unit_of_measurement = "mg"
        self._attr_suggested_display_precision = 1
        self._attr_native_value = 0.0
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_extra_state_attributes = {"last_updated": None}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_taken_{self._entry_id}", self.handle_pill_taken)
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self.update_decay, timedelta(minutes=20)
            )
        )
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            try:
                old_mass = float(last_state.state)
            except (ValueError, TypeError):
                old_mass = 0.0
            last_ts_str = last_state.attributes.get("last_updated")
            gut_mass = last_state.attributes.get("gut_mass", 0.0)
            if last_ts_str:
                try:
                    last_ts = dt_util.parse_datetime(last_ts_str)
                    now = dt_util.now()
                    elapsed_hours = (now - last_ts).total_seconds() / 3600.0
                    if self._half_life > 0:
                        self._current_mass = old_mass * (0.5 ** (elapsed_hours / self._half_life))
                    else:
                        self._current_mass = old_mass
                    self._last_updated = last_ts
                except (ValueError, TypeError):
                    self._current_mass = old_mass
                    self._last_updated = dt_util.now()
            else:
                self._current_mass = old_mass
                self._gut_mass = gut_mass
                self._last_updated = dt_util.now()
        else:
            self._current_mass = 0.0
            self._gut_mass = 0.0
            self._last_updated = dt_util.now()
        self.update_state()

    @callback
    def handle_pill_taken(self, *args, **kwargs):
        now = dt_util.now()
        if self._last_updated:
            elapsed_hours = (now - self._last_updated).total_seconds() / 3600.0
            if self._half_life > 0:
                self._current_mass *= (0.5 ** (elapsed_hours / self._half_life))
        k_e = math.log(2) / self._half_life if self._half_life > 0 else 0
        if self._hours_to_peak > 0:
            self._ka = self._solve_ka(self._hours_to_peak, k_e)
        else:
            abs_delay = 1.0 # Default
            self._ka = 1 / abs_delay
        self._gut_mass += float(self._strength)
        self._last_updated = now
        self.update_state()
        self.async_write_ha_state()

    def update_state(self):
        now = dt_util.now()
        if self._last_updated and self._half_life > 0:
            elapsed_hours = (now - self._last_updated).total_seconds() / 3600.0
            self._current_mass *= (0.5 ** (elapsed_hours / self._half_life))
        self._attr_native_value = self._current_mass
        self._attr_extra_state_attributes = {
            "last_updated": self._last_updated.isoformat() if self._last_updated else None,
            "gut_mass": self._gut_mass,
            "ka": self._ka
        }
        self.async_write_ha_state()

    @callback
    def update_decay(self, now):
        if not self._last_updated or self._half_life <= 0:
            return
        now = dt_util.now()
        elapsed_hours = (now - self._last_updated).total_seconds() / 3600.0
        k_e = math.log(2) / self._half_life
        k_a = getattr(self, '_ka', 0)
        if k_a == 0 and self._hours_to_peak > 0:
            k_a = 1 / self._hours_to_peak
        if k_a == k_e and k_a != 0:
            k_a *= 1.0001
        if self._hours_to_peak <= 0:
            self._gut_mass = 0
            self._current_mass *= math.exp(-k_e * elapsed_hours)
        else:
            new_gut = self._gut_mass * math.exp(-k_a * elapsed_hours)
            new_body = (self._current_mass * math.exp(-k_e * elapsed_hours) + 
                        (self._gut_mass * k_a / (k_a - k_e)) * 
                        (math.exp(-k_e * elapsed_hours) - math.exp(-k_a * elapsed_hours)))
            self._gut_mass = new_gut
            self._current_mass = new_body
        self._last_updated = now
        self.update_state()

    def _solve_ka(self, t_max, k_e):
        low, high = 0.0001, 20.0
        for _ in range(50):
            mid_ka = (low + high) / 2
            if mid_ka == k_e: mid_ka += 0.0001
            try:
                calc_t_max = (math.log(mid_ka) - math.log(k_e)) / (mid_ka - k_e)
                if calc_t_max < t_max: low = mid_ka
                else: high = mid_ka
            except (ValueError, ZeroDivisionError):
                low = mid_ka
        return low

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )

class PillStrengthSensor(RestoreSensor):
    def __init__(self, entry):
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._attr_name = f"{med_name} Strength"
        self._attr_unique_id = f"{entry.entry_id}_strength"
        self._attr_icon = "mdi:pill"
        self._entry_id = entry.entry_id
        self._attr_native_unit_of_measurement = "mg"
        self._attr_device_class = SensorDeviceClass.WEIGHT
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_value = float(entry.data.get("strength", 0))

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )

class PillSteadyStateSensor(RestoreSensor):
    def __init__(self, entry):
        med_name = entry.data["medication_name"]
        self._med_name = med_name
        self._attr_name = f"{med_name} Days to Steady State"
        self._attr_unique_id = f"{entry.entry_id}_steady_state"
        self._attr_icon = "mdi:chart-bell-curve"
        self._entry_id = entry.entry_id
        self._attr_suggested_display_precision = 1
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._last_dose_timestamp = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_taken_{self._entry_id}", self._handle_pill_taken)
        )
        last_state = await self.async_get_last_state()
        if last_state and "last_dose_timestamp" in last_state.attributes:
            try:
                self._last_dose_timestamp = dt_util.parse_datetime(last_state.attributes["last_dose_timestamp"])
            except (ValueError, TypeError):
                pass
        self.update_state()

    @callback
    def _handle_pill_taken(self, *args, **kwargs):
        self._last_dose_timestamp = dt_util.now()
        self.update_state()

    def update_state(self):
        now = dt_util.now()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        hours_to_peak = entry.data.get("hours_to_peak", 0.0)
        if not self._last_dose_timestamp:
            self._attr_native_value = "N/A"
            return
        elapsed_time = now - self._last_dose_timestamp
        missed_dose = elapsed_time > timedelta(hours=24)
        half_life = entry.data.get("half_life", 0)
        if missed_dose:
            if half_life > 0:
                # Degradation flag: recover from missed dose
                self._attr_native_value = 3.0 * (half_life / 24)
            else:
                self._attr_native_value = elapsed_time.total_seconds() / 86400.0
        else:
            if half_life > 0:
                # Standard window to hit accumulation equilibrium (~5 half-lives)
                total_ss_hours = 5 * half_life
                remaining_hours = max(0, total_ss_hours - elapsed_time.total_seconds() / 3600.0)
                self._attr_native_value = remaining_hours / 24.0
            else:
                self._attr_native_value = 0.0
        self._attr_extra_state_attributes = {
            "last_dose_timestamp": self._last_dose_timestamp.isoformat() if self._last_dose_timestamp else None
        }
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )  

    @property
    def native_value(self):
        return self._attr_native_value