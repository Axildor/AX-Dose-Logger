"""Overdue sensor — seconds past the most recent missed scheduled dose time.

For scheduled medications (Regular Interval, Time of Day, Cyclic), this sensor
reports how many seconds the user is overdue for their next dose.  Returns 0
when not overdue (or for As Needed medications where overdue is undefined).

The sensor also exposes an ``overdue_since`` attribute with the ISO timestamp
of the missed slot, enabling automations and custom cards to display absolute
times without doing math.
"""

from datetime import date, timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTime
from homeassistant.core import callback

from ..const import (
    TRACKING_AS_NEEDED,
    TRACKING_CYCLIC,
    TRACKING_REGULAR_INTERVAL,
    TRACKING_TIME_OF_DAY,
    get_dose_times,
)
from ..entity import AxDoseLoggerSensorEntity
from ..sliding_window import is_on_day


class PillOverdueSensor(AxDoseLoggerSensorEntity, RestoreSensor):
    """Seconds past the most recent missed scheduled dose time.

    State is 0 when not overdue (or As Needed).
    State is seconds overdue when a scheduled dose has been missed.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:clock-alert"

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "overdue"
        self._attr_unique_id = f"{entry.entry_id}_overdue"
        self._tracking_type = entry.data.get("tracking_type")
        self._attr_native_value = 0
        self._attr_extra_state_attributes = {
            "overdue_since": None,
            "tracking_type": self._tracking_type,
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Legacy restore for smooth UI transition; coordinator is
        # authoritative so _handle_coordinator_update overrides.
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            try:
                self._attr_native_value = int(float(last_state.state))
            except (ValueError, TypeError):
                pass

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

        overdue_since = None  # datetime or None

        if self._tracking_type == TRACKING_TIME_OF_DAY:
            overdue_since = self._compute_overdue_time_of_day(entry, now, timestamps)
        elif self._tracking_type == TRACKING_REGULAR_INTERVAL:
            overdue_since = self._compute_overdue_regular_interval(entry, now, timestamps)
        elif self._tracking_type == TRACKING_CYCLIC:
            overdue_since = self._compute_overdue_cyclic(entry, now, timestamps)
        # As Needed: overdue_since stays None (no schedule → undefined)

        if overdue_since is not None:
            self._attr_native_value = max(0, int((now - overdue_since).total_seconds()))
        else:
            self._attr_native_value = 0

        self._attr_extra_state_attributes = {
            "overdue_since": overdue_since.isoformat() if overdue_since else None,
            "tracking_type": self._tracking_type,
        }

    # ── Time of Day ────────────────────────────────────────────────────

    def _compute_overdue_time_of_day(self, entry, now, timestamps):
        """Return the most recent missed slot time, or None if all covered."""
        parsed_times = get_dose_times(entry)
        if not parsed_times:
            return None

        # Compute grace period (same algorithm as next_dose.py)
        if len(parsed_times) >= 2:
            min_gap_minutes = 24 * 60
            for i in range(len(parsed_times)):
                for j in range(i + 1, len(parsed_times)):
                    gap = (parsed_times[j][0] * 60 + parsed_times[j][1]) - (
                        parsed_times[i][0] * 60 + parsed_times[i][1]
                    )
                    min_gap_minutes = min(min_gap_minutes, gap)
            grace_minutes = max(30, min_gap_minutes // 2)
        else:
            grace_minutes = 60

        grace_td = timedelta(minutes=grace_minutes)

        # Check today's slots that have already passed
        overdue_since = None
        for hour, minute in parsed_times:
            slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if slot_time > now:
                # Future slot — can't be overdue yet
                continue

            # Check if this slot is covered by any dose within grace
            covered = False
            for ts in timestamps:
                if abs((ts - slot_time).total_seconds()) <= grace_td.total_seconds():
                    covered = True
                    break

            if not covered:
                # This slot was missed and has passed — record it
                # (last missed slot in iteration order wins = latest missed slot)
                overdue_since = slot_time

        return overdue_since

    # ── Regular Interval ────────────────────────────────────────────────

    def _compute_overdue_regular_interval(self, entry, now, timestamps):
        """Return last_dose + hours_between if overdue, else None."""
        hours_between = entry.options.get(
            "hours_between_doses", entry.data.get("hours_between_doses", 0)
        )
        if not timestamps or hours_between <= 0:
            return None

        last_ts = timestamps[-1]
        next_dose_time = last_ts + timedelta(hours=hours_between)
        if next_dose_time <= now:
            return next_dose_time
        return None

    # ── Cyclic / Calendar Pattern ───────────────────────────────────────

    def _compute_overdue_cyclic(self, entry, now, timestamps):
        """Return today's dose_time if on an ON day and dose missed, else None."""
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

        # Not on an ON day → not overdue
        if not is_on_day(entry, now.date(), now.date()):
            return None

        dose_time_today = now.replace(hour=dose_hour, minute=dose_minute, second=0, microsecond=0)

        # Dose time hasn't arrived yet today → not overdue
        if now < dose_time_today:
            return None

        # Check if a dose was taken within grace (1 hour for cyclic)
        grace_td = timedelta(hours=1)
        for ts in timestamps:
            if abs((ts - dose_time_today).total_seconds()) <= grace_td.total_seconds():
                # Covered — not overdue
                return None

        # On an ON day, dose time has passed, no dose within grace → overdue
        return dose_time_today