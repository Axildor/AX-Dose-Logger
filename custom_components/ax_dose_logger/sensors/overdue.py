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
    TRACKING_CYCLIC,
    TRACKING_REGULAR_INTERVAL,
    TRACKING_TIME_OF_DAY,
    get_dose_times,
    get_pills_per_slot,
    parse_dose_time,
)
from ..entity import AxDoseLoggerSensorEntity
from ..schedule import LATENESS_UNTIL_NEXT_SLOT, compute_slot_assignments
from ..sliding_window import is_day_covered, is_on_day


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
            except ValueError, TypeError:
                pass

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator (dose event or 1-min tick)."""
        self._update_state()
        self.async_write_ha_state()

    def _get_timestamps(self) -> list:
        """Read dose timestamps + skipped slots from the coordinator.

        Deliberately-skipped slots are merged in so a skip covers the
        missed scheduled slot and clears the overdue alarm (and advances
        next_dose) WITHOUT logging a real dose. This is the inverse of the
        adherence sensor, which deliberately ignores skipped slots so a
        skip stays penalized (the patient genuinely did not ingest it).
        """
        if not self.coordinator.data:
            return []
        real = [ts for ts, _ in self.coordinator.data.dose_history]
        skipped = list(self.coordinator.data.skipped_slots)
        return real + skipped

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

        # Expose grace_minutes so the frontend card can resolve the on-time
        # window (overdue-at-half-grace latency boundary) WITHOUT requiring
        # the adherence sensors to exist. The Overdue sensor is created for
        # every scheduled medication (guarded by tracking_type != AS_NEEDED
        # in sensor.py), so this is the reliable single source of truth for
        # the card's grace value -- fixing the bug where the card silently
        # fell back to a hardcoded 1.0h when adherence tracking was off,
        # ignoring the user's configured value.
        grace_minutes = entry.options.get(
            "adherence_grace_minutes",
            entry.data.get("adherence_grace_minutes", 60),
        )
        self._attr_extra_state_attributes = {
            "overdue_since": overdue_since.isoformat() if overdue_since else None,
            "tracking_type": self._tracking_type,
            "grace_minutes": grace_minutes,
        }

    # ── Time of Day ────────────────────────────────────────────────────

    def _compute_overdue_time_of_day(self, entry, now, timestamps):
        """Return the most recent missed slot time, or None if all covered.

        Uses the shared greedy slot-assignment model (see
        :func:`compute_slot_assignments`) so a dose taken late for slot A
        but before the next slot B is assigned to A (clearing its
        overdue), not stolen by B.  The timeline spans 2 days back so a
        missed slot from yesterday keeps counting across midnight instead
        of resetting at 00:01.

        ``early_grace`` is ``max(30, min_gap // 2)`` minutes — a dose this
        far before its slot still covers it (genuine early dose).
        Lateness extends until the next scheduled slot, so any dose
        between two slots is the late dose for the earlier one.
        """
        parsed_times = get_dose_times(entry)
        if not parsed_times:
            return None

        min_gap_minutes = 24 * 60
        for i in range(len(parsed_times)):
            for j in range(i + 1, len(parsed_times)):
                gap = (parsed_times[j][0] * 60 + parsed_times[j][1]) - (parsed_times[i][0] * 60 + parsed_times[i][1])
                min_gap_minutes = min(min_gap_minutes, gap)
        early_grace = timedelta(minutes=max(30, min_gap_minutes // 2))

        assignments = compute_slot_assignments(
            parsed_times,
            timestamps,
            now,
            lookback_days=2,
            future_days=0,
            early_grace=early_grace,
            lateness_mode=LATENESS_UNTIL_NEXT_SLOT,
            pills_per_slot=get_pills_per_slot(entry),
        )

        # Latest uncovered slot at or before now → overdue anchor.
        # Slots strictly after now are future and cannot be overdue yet.
        overdue_since = None
        for a in assignments:
            if a.slot_time > now:
                break
            if not a.covered:
                overdue_since = a.slot_time
        return overdue_since

    # ── Regular Interval ────────────────────────────────────────────────

    def _compute_overdue_regular_interval(self, entry, now, timestamps):
        """Return the most recently *reached* chained deadline, or None.

        Chained-deadline model (parity with the Time of Day slot model):
        with ``anchor = max(timestamps)`` and ``n = floor((now - anchor) /
        interval)``, the overdue anchor is ``anchor + n * interval`` — the
        latest deadline that has actually arrived.  This prevents overdue
        from *stacking*: when the next dose time is reached, the anchor
        advances to it and the counter resets to ~0 instead of continuing
        to accumulate past the previous missed deadline (the old fixed
        ``last_dose + interval`` anchor grew 8h → 16h → 24h… across
        multiple missed dose times).  The value is therefore naturally
        capped below one interval.

        Adherence is deliberately unaffected: it counts every missed
        interval as an expected dose independently, so re-anchoring this
        display sensor does not forgive the miss.
        """
        hours_between = entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 0))
        if not timestamps or hours_between <= 0:
            return None

        interval = timedelta(hours=hours_between)
        # max() not [-1]: adherence-override / undo flows can leave the
        # merged dose+skip list unsorted, so the last element is not
        # guaranteed to be the latest instant.
        anchor = max(timestamps)
        elapsed = now - anchor
        if elapsed <= timedelta(0):
            return None
        # Number of deadlines fully reached since the anchor dose.
        n = int(elapsed.total_seconds() // interval.total_seconds())
        if n <= 0:
            return None
        return anchor + n * interval

    # ── Cyclic / Calendar Pattern ───────────────────────────────────────

    def _compute_overdue_cyclic(self, entry, now, timestamps):
        """Return today's dose_time if on an ON day and dose missed, else None."""
        days_on = entry.options.get("days_on", entry.data.get("days_on", 5))
        days_off = entry.options.get("days_off", entry.data.get("days_off", 2))
        anchor_str = entry.options.get("cycle_anchor_date", entry.data.get("cycle_anchor_date"))
        dose_time_str = entry.options.get("dose_time", entry.data.get("dose_time", "08:00"))

        try:
            anchor_date = date.fromisoformat(anchor_str)
        except ValueError, TypeError:
            anchor_date = now.date()

        dose_hour, dose_minute = parse_dose_time(dose_time_str)

        # Not on an ON day → not overdue
        if not is_on_day(entry, now.date(), now.date()):
            return None

        dose_time_today = now.replace(hour=dose_hour, minute=dose_minute, second=0, microsecond=0)

        # Dose time hasn't arrived yet today → not overdue
        if now < dose_time_today:
            return None

        # Day-level coverage: any dose taken on this ON calendar day
        # covers the slot.  This aligns with pill_limit (24h rolling
        # window), avg_doses (PDC day coverage), and next_dose, so a
        # late-but-taken dose clears overdue instead of producing the
        # impossible "LIMIT REACHED + OVERDUE" state.  Timing quality
        # is still tracked separately by the adherence sensor (±grace).
        if is_day_covered(now.date(), timestamps):
            return None

        # On an ON day, dose time has passed, no dose today → overdue
        return dose_time_today
