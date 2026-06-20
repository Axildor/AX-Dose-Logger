"""Adherence percentage sensor for Pill Logger integration."""

from datetime import date, datetime, time, timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from ..const import (
    TRACKING_AS_NEEDED,
    TRACKING_CYCLIC,
    TRACKING_REGULAR_INTERVAL,
    TRACKING_TIME_OF_DAY,
    get_dose_times,
)
from ..entity import PillLoggerSensorEntity
from ..schedule import get_next_dose_time
from ..sliding_window import is_on_day

# Cap for timestamps attribute: prune older than 365 days, keep last 100
_TIMESTAMPS_MAX_DAYS = 365
_TIMESTAMPS_MAX_COUNT = 100


class PillAdherenceSensor(PillLoggerSensorEntity, RestoreSensor):
    """
    Sensor that calculates rolling adherence percentage over a configurable window.

    Adherence % = min(actual_doses / expected_doses * 100, 100)

    The grace period is user-configurable via `adherence_grace_hours` in the
    config entry (default: 1 hour). A dose is considered "on time" if it falls
    within ±grace of the expected slot time.

    For As Needed (PRN) medications, adherence is undefined — the sensor
    returns None with a descriptive reason attribute.
    """

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator, window_days):
        """
        Initialize the adherence sensor.

        Args:
            entry: The config entry object.
            coordinator: The PillLoggerCoordinator (single source of truth).
            window_days: Fixed trailing window size (7, 14, 30, or 365).

        """
        super().__init__(entry, coordinator)
        self._window_days = window_days
        self._attr_translation_key = "adherence"
        self._attr_translation_placeholders = {"window": str(window_days)}
        self._attr_unique_id = f"{entry.entry_id}_adherence_{window_days}"
        self._attr_icon = "mdi:check-decagram"
        self._tracking_type = entry.data.get("tracking_type")
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

    def _get_timestamps(self) -> list:
        """
        Combine real doses + adherence overrides from the coordinator.

        Adherence overrides are synthetic timestamps representing missed
        slots that the user marked as taken via the 'Mark Last Adherence
        Taken' button. They raise the adherence percentage without
        affecting the PK model, dose count, or any other sensor.
        """
        if not self.coordinator.data:
            return []
        real = [ts for ts, _ in self.coordinator.data.dose_history]
        overrides = list(self.coordinator.data.adherence_overrides)
        return real + overrides

    async def async_added_to_hass(self):
        """Set up and restore state."""
        await super().async_added_to_hass()

        # Restore history_start_date from last state
        last_state_obj = await self.async_get_last_state()
        if last_state_obj:
            if (
                last_state_obj.attributes.get("history_start_date")
            ):
                self._history_start_date = dt_util.parse_datetime(
                    last_state_obj.attributes["history_start_date"]
                )
        # Anchor to earliest dose from coordinator dose_history
        if self.coordinator.data and self.coordinator.data.dose_history:
            earliest = min(ts for ts, _ in self.coordinator.data.dose_history)
            self._history_start_date = earliest
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
    def _handle_coordinator_update(self) -> None:
        """
        Handle updated data from the coordinator.

        Covers dose events, adherence reset/override, and the 1-min tick
        (which handles midnight rollover). No separate midnight timer needed.
        """
        self._update_state()
        self.async_write_ha_state()

    def _find_last_missed_slot(self):
        """
        Find the most recent expected dose slot not covered by any timestamp.

        Dispatches to a tracking-type-specific helper. Returns the expected
        slot datetime, or None if there are no missed slots in the window.
        """
        now = dt_util.now()

        if self._tracking_type == TRACKING_AS_NEEDED:
            return None

        if not self._history_start_date:
            self._history_start_date = now

        days_since_start = (now - self._history_start_date).total_seconds() / 86400.0
        days_since_start = max(1.0, days_since_start)
        effective_window_days = min(days_since_start, float(self._window_days))

        grace_hours = self._get_grace_hours()
        grace_td = timedelta(hours=grace_hours)

        base_cutoff = now - timedelta(days=effective_window_days)
        if grace_hours > 0:
            extended_cutoff = base_cutoff - grace_td
        else:
            extended_cutoff = base_cutoff

        timestamps = self._get_timestamps()
        pruned = [ts for ts in timestamps if ts >= extended_cutoff]

        if self._tracking_type == TRACKING_TIME_OF_DAY:
            return self._find_last_missed_time_of_day(now, base_cutoff, grace_td, pruned)
        if self._tracking_type == TRACKING_REGULAR_INTERVAL:
            return self._find_last_missed_regular_interval(now, base_cutoff, grace_td, pruned)
        if self._tracking_type == TRACKING_CYCLIC:
            return self._find_last_missed_cyclic(now, base_cutoff, grace_td, pruned)
        return None

    def _find_last_missed_time_of_day(self, now, cutoff, grace_td, timestamps):
        """Find most recent missed slot for Time of Day tracking."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        parsed_times = get_dose_times(entry)
        if not parsed_times:
            return None

        day_offset = 0
        while True:
            check_date = (now - timedelta(days=day_offset)).date()
            for target_hour, target_minute in reversed(parsed_times):
                expected_time = datetime.combine(
                    check_date, time(target_hour, target_minute),
                    tzinfo=now.tzinfo,
                )
                if expected_time < cutoff:
                    return None

                dose_covers = any(
                    abs((ts - expected_time).total_seconds()) <= grace_td.total_seconds()
                    for ts in timestamps
                )

                if day_offset == 0 and now < expected_time + grace_td and not dose_covers:
                    continue

                if not dose_covers:
                    return expected_time
            day_offset += 1

    def _find_last_missed_regular_interval(self, now, cutoff, grace_td, timestamps):
        """Find most recent missed slot for Regular Interval tracking."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        hours_between = entry.options.get(
            "hours_between_doses", entry.data.get("hours_between_doses", 24)
        )
        interval_td = timedelta(hours=hours_between)

        if not timestamps:
            slot_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            while slot_time >= cutoff:
                day_slot = slot_time
                day_slots = []
                while day_slot <= now:
                    if day_slot >= cutoff and day_slot + grace_td <= now:
                        day_slots.append(day_slot)
                    day_slot += interval_td
                if day_slots:
                    return day_slots[-1]
                slot_time -= timedelta(days=1)
            return None

        anchor = timestamps[-1]

        forward_time = anchor + interval_td
        latest_missed = None
        while forward_time <= now:
            if forward_time + grace_td > now:
                break
            latest_missed = forward_time
            forward_time += interval_td
        if latest_missed is not None:
            return latest_missed

        slot_time = anchor
        while slot_time >= cutoff:
            dose_covers = any(
                abs((ts - slot_time).total_seconds()) <= grace_td.total_seconds()
                for ts in timestamps
            )
            if not dose_covers:
                return slot_time
            slot_time -= interval_td
        return None

    def _find_last_missed_cyclic(self, now, cutoff, grace_td, timestamps):
        """Find most recent missed slot for Cyclic/Calendar Pattern tracking."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        dose_time_str = entry.options.get(
            "dose_time", entry.data.get("dose_time", "08:00")
        )

        try:
            dose_hour, dose_minute = map(int, dose_time_str.split(":"))
        except (ValueError, AttributeError):
            dose_hour, dose_minute = 8, 0

        day_offset = 0
        while True:
            check_date = (now - timedelta(days=day_offset)).date()
            expected_time = datetime.combine(
                check_date, time(dose_hour, dose_minute),
                tzinfo=now.tzinfo,
            )
            if expected_time < cutoff:
                return None

            dose_covers = any(
                abs((ts - expected_time).total_seconds()) <= grace_td.total_seconds()
                for ts in timestamps
            )

            if day_offset == 0 and now < expected_time + grace_td and not dose_covers:
                day_offset += 1
                continue

            if is_on_day(entry, check_date, now.date()) and not dose_covers:
                return expected_time
            day_offset += 1

    def _get_grace_hours(self):
        """Get the user-configured adherence grace period."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return entry.options.get(
            "adherence_grace_hours",
            entry.data.get("adherence_grace_hours", 1.0),
        )

    # ------------------------------------------------------------------
    # Slot-counting methods per tracking type
    # ------------------------------------------------------------------

    def _count_slots_time_of_day(self, now, cutoff, grace_td, timestamps):
        """Count actual and expected dose slots for Time of Day tracking."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        parsed_times = get_dose_times(entry)

        if not parsed_times:
            return 0, 0

        actual = 0
        expected = 0
        day_offset = 0
        while True:
            check_date = (now - timedelta(days=day_offset)).date()

            for target_hour, target_minute in reversed(parsed_times):
                expected_time = datetime.combine(
                    check_date, time(target_hour, target_minute),
                    tzinfo=now.tzinfo,
                )
                if expected_time < cutoff:
                    return actual, expected

                dose_covers = any(
                    abs((ts - expected_time).total_seconds()) <= grace_td.total_seconds()
                    for ts in timestamps
                )

                if day_offset == 0 and now < expected_time + grace_td and not dose_covers:
                    continue

                expected += 1
                if dose_covers:
                    actual += 1

            day_offset += 1

        return actual, expected

    def _count_slots_regular_interval(self, now, cutoff, grace_td, timestamps):
        """Count actual and expected dose slots for Regular Interval tracking."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        hours_between = entry.options.get(
            "hours_between_doses", entry.data.get("hours_between_doses", 24)
        )
        interval_td = timedelta(hours=hours_between)

        if not timestamps:
            expected = 0
            slot_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            while slot_time >= cutoff:
                day_slot = slot_time
                while day_slot <= now:
                    if day_slot >= cutoff and day_slot + grace_td <= now:
                        expected += 1
                    day_slot += interval_td
                slot_time -= timedelta(days=1)
            return 0, expected

        anchor = timestamps[-1]
        actual = 0
        backward_expected = 0

        slot_time = anchor
        while slot_time >= cutoff:
            backward_expected += 1
            for ts in timestamps:
                if abs((ts - slot_time).total_seconds()) <= grace_td.total_seconds():
                    actual += 1
                    break
            slot_time -= interval_td

        forward_expected = 0
        forward_time = anchor + interval_td
        while forward_time <= now:
            if forward_time + grace_td > now:
                break
            forward_expected += 1
            forward_time += interval_td

        expected = backward_expected + forward_expected
        return actual, expected

    def _count_slots_cyclic(self, now, cutoff, grace_td, timestamps):
        """Count actual and expected dose slots for Cyclic/Calendar Pattern."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        dose_time_str = entry.options.get(
            "dose_time", entry.data.get("dose_time", "08:00")
        )

        try:
            dose_hour, dose_minute = map(int, dose_time_str.split(":"))
        except (ValueError, AttributeError):
            dose_hour, dose_minute = 8, 0

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

            dose_covers = any(
                abs((ts - expected_time).total_seconds()) <= grace_td.total_seconds()
                for ts in timestamps
            )

            if day_offset == 0 and now < expected_time + grace_td and not dose_covers:
                day_offset += 1
                continue

            if is_on_day(entry, check_date, now.date()):
                expected += 1
                if dose_covers:
                    actual += 1
            day_offset += 1

        return actual, expected

    def _get_next_dose_time(self):
        """Get the next expected dose time for scheduling grace expiry."""
        now = dt_util.now()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        timestamps = self._get_timestamps()

        # Regular Interval and Time of Day are handled by the shared helper.
        shared = get_next_dose_time(entry, timestamps, now, self._tracking_type)
        if shared is not None:
            return shared

        if self._tracking_type == TRACKING_CYCLIC:
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
                days_until_next_on = cycle_length - position_in_cycle
                return dose_time_today + timedelta(days=days_until_next_on)
            if now < dose_time_today:
                return dose_time_today
            days_until_next_on = cycle_length - position_in_cycle
            if days_until_next_on == 0:
                days_until_next_on = cycle_length
            return dose_time_today + timedelta(days=days_until_next_on)
        return None

    def _update_state(self):
        """Recalculate adherence percentage based on tracking type."""
        now = dt_util.now()
        timestamps = self._get_timestamps()

        # PRN medications: adherence is undefined
        if self._tracking_type == TRACKING_AS_NEEDED:
            self._attr_native_value = None
            # Prune timestamps to last 365 days and cap at 100 entries
            cutoff = now - timedelta(days=_TIMESTAMPS_MAX_DAYS)
            recent = [ts for ts in timestamps if ts >= cutoff][-_TIMESTAMPS_MAX_COUNT:]
            self._attr_extra_state_attributes = {
                "reason": "PRN medications do not track adherence",
                "window_days": self._window_days,
                "timestamps": [ts.isoformat() for ts in recent],
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

        base_cutoff = now - timedelta(days=effective_window_days)

        if grace_hours > 0:
            extended_cutoff = base_cutoff - grace_td
        else:
            extended_cutoff = base_cutoff

        # Prune timestamps outside the extended window
        valid_timestamps = [ts for ts in timestamps if ts >= extended_cutoff]

        if self._tracking_type == TRACKING_TIME_OF_DAY:
            actual, expected = self._count_slots_time_of_day(
                now, base_cutoff, grace_td, valid_timestamps
            )
        elif self._tracking_type == TRACKING_REGULAR_INTERVAL:
            actual, expected = self._count_slots_regular_interval(
                now, base_cutoff, grace_td, valid_timestamps
            )
        elif self._tracking_type == TRACKING_CYCLIC:
            actual, expected = self._count_slots_cyclic(
                now, base_cutoff, grace_td, valid_timestamps
            )
        else:
            actual, expected = 0, 0

        if expected > 0:
            raw_pct = (actual / expected) * 100
            self._attr_native_value = min(round(raw_pct), 100)
        else:
            self._attr_native_value = None

        attrs = {
            "actual_doses": actual,
            "expected_doses": expected,
            "window_days": self._window_days,
            "effective_window_days": round(effective_window_days, 1),
            "grace_hours": grace_hours,
            "timestamps": [ts.isoformat() for ts in valid_timestamps],
            "history_start_date": (
                self._history_start_date.isoformat()
                if self._history_start_date
                else None
            ),
        }
        if expected == 0 and self._tracking_type != TRACKING_AS_NEEDED:
            attrs["reason"] = "No scheduled doses in window"

        self._attr_extra_state_attributes = attrs

        # Schedule a recalculation when the next dose's grace period expires
        if self._tracking_type in (TRACKING_TIME_OF_DAY, TRACKING_REGULAR_INTERVAL, TRACKING_CYCLIC):
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
