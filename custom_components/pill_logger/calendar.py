"""Calendar platform for the Pill Logger integration.

Generates calendar events representing expected dose times based on the
medication's tracking type configuration:
  - Time of Day:  One daily event at the configured time.
  - Regular Interval: Events every N hours anchored to midnight.
  - Cyclic/Calendar Pattern: Events on ON days at the configured dose time.
  - As Needed (PRN): No future events (unpredictable).
"""

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import DOMAIN

EVENT_DURATION = timedelta(hours=1)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Pill Logger calendar entity from a config entry."""
    enable_calendar = entry.options.get(
        "enable_calendar", entry.data.get("enable_calendar", True)
    )
    if not enable_calendar:
        return

    async_add_entities([PillCalendarEntity(entry)])


class PillCalendarEntity(CalendarEntity):
    """Calendar entity that plots expected dose times for a medication."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the calendar entity."""
        self._entry_id = entry.entry_id
        self._med_name = entry.data["medication_name"]
        self._tracking_type = entry.data.get("tracking_type")
        self._attr_unique_id = f"{entry.entry_id}_calendar"
        self._attr_name = "Calendar"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )

    async def async_added_to_hass(self) -> None:
        """Set up alarms and daily refresh when entity is added to HA."""
        await super().async_added_to_hass()
        # Trigger initial state evaluation so the CalendarEntity base class
        # can set up start/end alarms for the current event.
        self.async_write_ha_state()
        # Refresh at midnight so new daily events are picked up.
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._midnight_update, hour=0, minute=0, second=0
            )
        )

    @callback
    def _midnight_update(self, now: datetime) -> None:
        """Refresh state at midnight to pick up new daily events."""
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # CalendarEntity interface
    # ------------------------------------------------------------------

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming event (or the currently active one)."""
        now = dt_util.now()
        events = self._generate_events(now, now + timedelta(days=2))
        for ev in events:
            if ev.end > now:
                return ev
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return calendar events within the requested datetime range."""
        return self._generate_events(start_date, end_date)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_entry(self) -> ConfigEntry | None:
        """Return the current config entry, or None if removed."""
        return self.hass.config_entries.async_get_entry(self._entry_id)

    def _generate_events(
        self, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Dispatch event generation based on tracking type."""
        entry = self._get_entry()
        if entry is None:
            return []

        tracking_type = entry.data.get("tracking_type")
        if tracking_type == "Time of Day":
            return self._generate_time_of_day_events(entry, start_date, end_date)
        if tracking_type == "Regular Interval":
            return self._generate_regular_interval_events(entry, start_date, end_date)
        if tracking_type == "Cyclic/Calendar Pattern":
            return self._generate_cyclic_events(entry, start_date, end_date)
        # As Needed — cannot predict future doses
        return []

    # ------------------------------------------------------------------
    # Time of Day
    # ------------------------------------------------------------------

    def _generate_time_of_day_events(
        self, entry: ConfigEntry, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """One event per day at the configured time_of_day."""
        time_of_day = entry.options.get(
            "time_of_day", entry.data.get("time_of_day", "08:00")
        )
        try:
            hour, minute = int(time_of_day.split(":")[0]), int(
                time_of_day.split(":")[1]
            )
        except (ValueError, AttributeError):
            hour, minute = 8, 0

        events: list[CalendarEvent] = []
        tz = dt_util.now().tzinfo
        current = start_date.date()
        while current <= end_date.date():
            event_start = datetime(
                current.year, current.month, current.day, hour, minute, tzinfo=tz
            )
            event_end = event_start + EVENT_DURATION
            if event_end > start_date and event_start < end_date:
                events.append(
                    CalendarEvent(
                        summary=f"{self._med_name} Dose",
                        start=event_start,
                        end=event_end,
                    )
                )
            current += timedelta(days=1)
        return events

    # ------------------------------------------------------------------
    # Regular Interval
    # ------------------------------------------------------------------

    def _generate_regular_interval_events(
        self, entry: ConfigEntry, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Events every N hours anchored to midnight each day."""
        hours_between = int(
            entry.options.get(
                "hours_between_doses", entry.data.get("hours_between_doses", 8)
            )
        )
        if hours_between <= 0:
            hours_between = 1

        events: list[CalendarEvent] = []
        tz = dt_util.now().tzinfo
        # Include the day before start_date to catch events that span midnight
        current = start_date.date() - timedelta(days=1)
        end = end_date.date() + timedelta(days=1)
        while current <= end:
            hour = 0
            while hour < 24:
                event_start = datetime(
                    current.year, current.month, current.day, hour, 0, tzinfo=tz
                )
                event_end = event_start + EVENT_DURATION
                if event_end > start_date and event_start < end_date:
                    events.append(
                        CalendarEvent(
                            summary=f"{self._med_name} Dose",
                            start=event_start,
                            end=event_end,
                        )
                    )
                hour += hours_between
            current += timedelta(days=1)
        return events

    # ------------------------------------------------------------------
    # Cyclic / Calendar Pattern
    # ------------------------------------------------------------------

    def _generate_cyclic_events(
        self, entry: ConfigEntry, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Events on ON days at the configured dose_time."""
        days_on = int(
            entry.options.get("days_on", entry.data.get("days_on", 5))
        )
        days_off = int(
            entry.options.get("days_off", entry.data.get("days_off", 2))
        )
        anchor_str = entry.options.get(
            "cycle_anchor_date", entry.data.get("cycle_anchor_date")
        )
        dose_time_str = entry.options.get(
            "dose_time", entry.data.get("dose_time", "08:00")
        )

        try:
            anchor_date = date.fromisoformat(anchor_str)
        except (ValueError, TypeError):
            anchor_date = date.today()

        try:
            dose_hour, dose_minute = int(dose_time_str.split(":")[0]), int(
                dose_time_str.split(":")[1]
            )
        except (ValueError, AttributeError):
            dose_hour, dose_minute = 8, 0

        cycle_length = days_on + days_off
        if cycle_length <= 0:
            cycle_length = 1

        events: list[CalendarEvent] = []
        tz = dt_util.now().tzinfo
        # Include surrounding days for overlap safety
        current = start_date.date() - timedelta(days=1)
        end = end_date.date() + timedelta(days=1)
        while current <= end:
            days_since_anchor = (current - anchor_date).days
            position_in_cycle = days_since_anchor % cycle_length
            if position_in_cycle < days_on:  # ON day
                event_start = datetime(
                    current.year,
                    current.month,
                    current.day,
                    dose_hour,
                    dose_minute,
                    tzinfo=tz,
                )
                event_end = event_start + EVENT_DURATION
                if event_end > start_date and event_start < end_date:
                    events.append(
                        CalendarEvent(
                            summary=f"{self._med_name} Dose",
                            start=event_start,
                            end=event_end,
                        )
                    )
            current += timedelta(days=1)
        return events