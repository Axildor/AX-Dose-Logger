"""Calendar platform for the Pill Logger integration.

Generates calendar events representing expected dose times based on the
medication's tracking type configuration:
  - Time of Day:  One or more daily events at the configured times.
  - Regular Interval: Events every N hours anchored to midnight.
  - Cyclic/Calendar Pattern: Events on ON days at the configured dose time.
  - As Needed (PRN): No future events (unpredictable).
"""

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, TRACKING_TIME_OF_DAY, TRACKING_REGULAR_INTERVAL, TRACKING_CYCLIC, get_dose_times
from .coordinator import PillLoggerCoordinator
from .data import PillLoggerConfigEntry
from .entity import PillLoggerEntity
from .sliding_window import is_on_day

EVENT_DURATION = timedelta(hours=1)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PillLoggerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Pill Logger calendar entity from a config entry."""
    enable_calendar = entry.options.get(
        "enable_calendar", entry.data.get("enable_calendar", True)
    )
    if not enable_calendar:
        return

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([PillCalendarEntity(entry, coordinator)])


class PillCalendarEntity(PillLoggerEntity, CalendarEntity):
    """Calendar entity that plots expected dose times for a medication."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry: PillLoggerConfigEntry, coordinator: PillLoggerCoordinator) -> None:
        """Initialize the calendar entity."""
        super().__init__(entry, coordinator)
        self._tracking_type = entry.data.get("tracking_type")
        self._attr_unique_id = f"{entry.entry_id}_calendar"
        self._attr_translation_key = "calendar"

    async def async_added_to_hass(self) -> None:
        """Set up when entity is added to HA."""
        await super().async_added_to_hass()
        # Trigger initial state evaluation so the CalendarEntity base class
        # can set up start/end alarms for the current event.
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator.

        The 1-min coordinator tick covers midnight rollover — no
        separate ``async_track_time_change`` timer needed.
        """
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
        if tracking_type == TRACKING_TIME_OF_DAY:
            return self._generate_time_of_day_events(entry, start_date, end_date)
        if tracking_type == TRACKING_REGULAR_INTERVAL:
            return self._generate_regular_interval_events(entry, start_date, end_date)
        if tracking_type == TRACKING_CYCLIC:
            return self._generate_cyclic_events(entry, start_date, end_date)
        # As Needed — cannot predict future doses
        return []

    # ------------------------------------------------------------------
    # Time of Day (supports multiple daily dose times)
    # ------------------------------------------------------------------

    def _generate_time_of_day_events(
        self, entry: ConfigEntry, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """One or more events per day at the configured dose times."""
        parsed_times = get_dose_times(entry)

        events: list[CalendarEvent] = []
        tz = dt_util.now().tzinfo
        current = start_date.date()
        while current <= end_date.date():
            for hour, minute in parsed_times:
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
        dose_time_str = entry.options.get(
            "dose_time", entry.data.get("dose_time", "08:00")
        )

        try:
            dose_hour, dose_minute = int(dose_time_str.split(":")[0]), int(
                dose_time_str.split(":")[1]
            )
        except (ValueError, AttributeError):
            dose_hour, dose_minute = 8, 0

        events: list[CalendarEvent] = []
        tz = dt_util.now().tzinfo
        current = start_date.date() - timedelta(days=1)
        end = end_date.date() + timedelta(days=1)
        while current <= end:
            if is_on_day(entry, current, date.today()):  # ON day
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