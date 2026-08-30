"""
Calendar platform for the AX Dose Logger integration.

Generates calendar events representing expected dose times based on the
medication's tracking type configuration:
  - Time of Day:  One or more daily events at the configured times.
  - Regular Interval: Events every N hours anchored to the last dose's
    absolute instant (elapsed-time semantics, matching the Next Dose /
    Overdue sensors).  Falls back to a midnight grid before the first
    dose establishes an anchor.
  - Cyclic/Calendar Pattern: Events on ON days at the configured dose time.
  - As Needed (PRN): No future events (unpredictable).
"""

import math
from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    TRACKING_CYCLIC,
    TRACKING_REGULAR_INTERVAL,
    TRACKING_TIME_OF_DAY,
    get_dose_times,
    get_pills_per_slot,
    parse_dose_time,
)
from .coordinator import AxDoseLoggerCoordinator
from .data import AxDoseLoggerConfigEntry
from .entity import AxDoseLoggerEntity
from .sliding_window import is_on_day

EVENT_DURATION = timedelta(hours=1)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AxDoseLoggerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the AX Dose Logger calendar entity from a config entry."""
    enable_calendar = entry.options.get("enable_calendar", entry.data.get("enable_calendar", True))
    if not enable_calendar:
        return

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([PillCalendarEntity(entry, coordinator)])


class PillCalendarEntity(AxDoseLoggerEntity, CalendarEntity):
    """Calendar entity that plots expected dose times for a medication."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry: AxDoseLoggerConfigEntry, coordinator: AxDoseLoggerCoordinator) -> None:
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
        """
        Handle updated data from the coordinator.

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

    def _generate_events(self, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
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
        """One or more events per day at the configured dose times.

        When ``pills_per_slot`` > 1 the description states the quantity so
        the calendar matches the slot-coverage model (a dose is complete
        only after all pills for the slot are logged).
        """
        parsed_times = get_dose_times(entry)
        pills_per_slot = get_pills_per_slot(entry)
        summary = f"{self._med_name} Dose" if pills_per_slot <= 1 else f"{self._med_name} Dose x{pills_per_slot}"

        events: list[CalendarEvent] = []
        tz = dt_util.now().tzinfo
        current = start_date.date()
        while current <= end_date.date():
            for hour, minute in parsed_times:
                event_start = datetime(current.year, current.month, current.day, hour, minute, tzinfo=tz)
                event_end = event_start + EVENT_DURATION
                if event_end > start_date and event_start < end_date:
                    events.append(
                        CalendarEvent(
                            summary=summary,
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
        """Events every N hours anchored to the last dose's absolute instant.

        Anchoring to the last dose (or skipped slot) — not local midnight —
        keeps the calendar in lockstep with the Next Dose / Overdue sensors,
        which compute ``last_ts + timedelta(hours=N)`` (elapsed-time
        semantics).  This resolves the DST-day divergence where a
        midnight-anchored wall-clock grid would disagree with the
        elapsed-time-anchored sensors by one hour, and the late-dose case
        where the next prescribed slot is N real hours after the actual
        dose, not the next wall-clock grid mark.  The minimum-spacing
        safety floor (next dose is always >= hours_between real hours
        after the last) is preserved because both code paths use the same
        anchor + k * interval model.

        The anchor is the latest of (real doses + skipped slots),
        matching the schedule_timestamps merge used by the Next Dose
        and Overdue sensors so the calendar and sensors agree on which
        instant the grid starts from.

        When no dose has been logged yet (no anchor), events fall back to
        a midnight-anchored grid — the pre-first-dose default.  The first
        dose establishes the anchor and the grid shifts to match.
        """
        hours_between = int(entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 8)))
        if hours_between <= 0:
            hours_between = 1

        interval = timedelta(hours=hours_between)
        events: list[CalendarEvent] = []
        tz = dt_util.now().tzinfo

        # Anchor = latest of (real doses + skipped slots), matching the
        # schedule_timestamps merge used by the Next Dose / Overdue sensors
        # so the calendar and sensors agree on which instant the grid starts.
        anchor: datetime | None = None
        if self.coordinator.data:
            schedule_ts: list[datetime] = [ts for ts, _ in self.coordinator.data.dose_history] + list(
                self.coordinator.data.skipped_slots
            )
            if schedule_ts:
                anchor = max(schedule_ts)

        if anchor is None:
            # No anchor yet — fall back to a midnight grid (pre-first-dose
            # default).  The first dose establishes the anchor and the
            # grid shifts to the elapsed-time model above.
            current = start_date.date() - timedelta(days=1)
            end = end_date.date() + timedelta(days=1)
            while current <= end:
                hour = 0
                while hour < 24:
                    event_start = datetime(current.year, current.month, current.day, hour, 0, tzinfo=tz)
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

        # Anchor exists — generate the event grid as anchor + k*interval,
        # matching the sensors' last_ts + timedelta(hours=N) model.
        # The anchor's original tzinfo (fixed offset from parse_datetime or
        # zoneinfo from dt_util.now()) is preserved so the elapsed-time
        # semantics match the sensors exactly; HA converts to the local
        # zone for display.  Events for both past (k < 0) and future (k > 0)
        # prescribed slots are generated so range queries show the full
        # schedule, not just upcoming doses.
        interval_s = interval.total_seconds()
        # First k whose event could overlap the window (event_end > start):
        #   anchor + k*interval + EVENT_DURATION > start_date
        k_start = math.floor((start_date - EVENT_DURATION - anchor).total_seconds() / interval_s) + 1
        # Last k whose event starts before end_date:
        #   anchor + k*interval < end_date
        k_end = math.ceil((end_date - anchor).total_seconds() / interval_s) - 1

        for k in range(k_start, k_end + 1):
            event_start = anchor + k * interval
            event_end = event_start + EVENT_DURATION
            events.append(
                CalendarEvent(
                    summary=f"{self._med_name} Dose",
                    start=event_start,
                    end=event_end,
                )
            )

        return events

    # ------------------------------------------------------------------
    # Cyclic / Calendar Pattern
    # ------------------------------------------------------------------

    def _generate_cyclic_events(
        self, entry: ConfigEntry, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Events on ON days at the configured dose_time."""
        dose_time_str = entry.options.get("dose_time", entry.data.get("dose_time", "08:00"))

        dose_hour, dose_minute = parse_dose_time(dose_time_str)

        events: list[CalendarEvent] = []
        tz = dt_util.now().tzinfo
        current = start_date.date() - timedelta(days=1)
        end = end_date.date() + timedelta(days=1)
        while current <= end:
            if is_on_day(entry, current, dt_util.now().date()):  # ON day
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
