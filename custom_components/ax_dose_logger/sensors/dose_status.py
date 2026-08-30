"""Dose Status sensor — single-source-of-truth enum for automations + the card.

Reports the medication's current dosing state as one ENUM value, mirroring
the frontend card's button state machine exactly so card and automations can
never disagree:

* ``not_due``      — scheduled med, next slot still in the future
* ``due``          — scheduled slot has arrived (within the first half of the
  adherence grace window)
* ``overdue``      — past half the grace window (latency warning boundary)
* ``limit_reached``— pill-count rolling window is full (lockout; includes
  Cyclic OFF days, where ``compute_safe_to_take`` returns 0)
* ``limit_24h``    — 24h strength limit already exceeded OR the next dose
  would push over it (pharmacological safety limit)
* ``ok``           — As-Needed medication, available to take

Precedence mirrors ``resolveButtonState()`` in the card:
``limit_reached`` → ``limit_24h`` → scheduled states (``not_due``/``due``/
``overdue``) → ``ok`` (As Needed).

HA best practices applied:

* ``SensorDeviceClass.ENUM`` with an explicit ``options`` list — validated
  states, dropdown UI, voice/assistant support.
* **Point-in-time timers** (``async_call_later``) armed at each state
  transition instant (next-dose arrival, half-grace latency boundary,
  window-expiry) so the enum flips at the *exact* moment the state changes,
  not on the next 1-min coordinator tick.
* Recomputed on every coordinator push (dose/undo/skip/reset all re-derive).
* Reuses the shared pure helpers (``compute_safe_to_take``,
  ``get_next_dose_time``, ``compute_slot_assignments``) — no logic
  duplication with the overdue/next_dose/pill_limit sensors.
"""

from datetime import date, timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from ..const import (
    TRACKING_AS_NEEDED,
    TRACKING_CYCLIC,
    TRACKING_REGULAR_INTERVAL,
    TRACKING_TIME_OF_DAY,
    get_dose_times,
    get_pills_per_slot,
    parse_dose_time,
)
from ..entity import AxDoseLoggerSensorEntity
from ..schedule import LATENESS_UNTIL_NEXT_SLOT, compute_slot_assignments, get_next_dose_time
from ..sliding_window import compute_safe_to_take, is_day_covered, is_on_day

# Enum states (order = display order in HA UI dropdowns)
STATUS_NOT_DUE = "not_due"
STATUS_DUE = "due"
STATUS_OVERDUE = "overdue"
STATUS_LIMIT_REACHED = "limit_reached"
STATUS_LIMIT_24H = "limit_24h"
STATUS_OK = "ok"

DOSE_STATUS_OPTIONS = [
    STATUS_NOT_DUE,
    STATUS_DUE,
    STATUS_OVERDUE,
    STATUS_LIMIT_REACHED,
    STATUS_LIMIT_24H,
    STATUS_OK,
]

# Fixed 24-hour rolling window for the strength limit (mirrors
# PillDailyAmountSensor / Pill24hLimitExceededSensor).
_WINDOW_HOURS_24H = 24


class PillDoseStatusSensor(AxDoseLoggerSensorEntity, RestoreSensor):
    """ENUM sensor: the medication's current dosing state.

    One entity that answers "can/should I take it right now?" for automations
    (``trigger: state`` on ``to: due`` etc.) and powers the card's button
    state as its primary source.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = DOSE_STATUS_OPTIONS
    _attr_icon = "mdi:checkbox-marked-circle-outline"

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "dose_status"
        self._attr_unique_id = f"{entry.entry_id}_dose_status"
        self._tracking_type = entry.data.get("tracking_type")
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}
        self._status_timer_unsub = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Compute immediately so the enum is real on first-ever setup instead
        # of sitting at `unknown` until the first coordinator tick.
        self._update_state()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        """Clean up the point-in-time transition timer."""
        self._cancel_status_timer()
        await super().async_will_remove_from_hass()

    def _cancel_status_timer(self):
        if self._status_timer_unsub:
            self._status_timer_unsub()
            self._status_timer_unsub = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator (dose event or 1-min tick)."""
        self._update_state()
        self.async_write_ha_state()

    @callback
    def _on_status_timer(self, _now):
        """A state-transition instant was reached — recompute immediately."""
        self._status_timer_unsub = None
        self._update_state()
        self.async_write_ha_state()

    # ── Input collectors (mirror the sibling sensors) ───────────────────

    def _get_dose_timestamps(self) -> list:
        """Doses only — used for the pill-count gate (skips never consume)."""
        if self.coordinator.data:
            return [ts for ts, _ in self.coordinator.data.dose_history]
        return []

    def _get_schedule_timestamps(self) -> list:
        """Doses + skipped slots — used for schedule advancement/coverage."""
        if not self.coordinator.data:
            return []
        real = [ts for ts, _ in self.coordinator.data.dose_history]
        skipped = list(self.coordinator.data.skipped_slots)
        return real + skipped

    # ── State machine ───────────────────────────────────────────────────

    def _update_state(self):
        now = dt_util.now()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            # The config entry can be gone while a point-in-time timer is
            # still armed (entry removal race) — bail out instead of raising.
            return
        dose_timestamps = self._get_dose_timestamps()
        schedule_timestamps = self._get_schedule_timestamps()
        pills_per_slot = get_pills_per_slot(entry)

        # Gate 1 — pill-count lockout (includes Cyclic OFF days, where
        # compute_safe_to_take returns 0). Mirrors the card's isLockedOut.
        safe_count = compute_safe_to_take(entry, dose_timestamps, now, self._tracking_type)

        # Gate 2 — 24h strength limit (already exceeded OR next dose would
        # exceed). Mirrors the card's is24hLimitReached / the binary sensor.
        limit_24h_on, amount_24h, daily_limit = self._compute_24h_limit(entry, dose_timestamps, now)

        # Grace window (latency boundary = half grace), same source as the
        # overdue sensor exposes for the card.
        grace_minutes = entry.options.get(
            "adherence_grace_minutes",
            entry.data.get("adherence_grace_minutes", 60),
        )

        status = None
        next_dose_at = None
        overdue_since = None
        next_transition = None  # earliest instant the status may change

        if safe_count <= 0:
            status = STATUS_LIMIT_REACHED
            # Transition back when the oldest in-window dose expires
            # (buffered cutoff — mirrors PillLimitSensor.window_expires_at).
            next_transition = self._window_expires_at(entry, dose_timestamps, now)
        elif limit_24h_on:
            status = STATUS_LIMIT_24H
        elif self._tracking_type == TRACKING_AS_NEEDED:
            status = STATUS_OK
        else:
            # Scheduled medication — derive schedule position.
            next_dose_at = self._compute_next_dose(entry, now, schedule_timestamps)
            overdue_since = self._compute_overdue(entry, now, schedule_timestamps)

            if overdue_since is not None:
                status = STATUS_OVERDUE if self._is_past_half_grace(now, overdue_since, grace_minutes) else STATUS_DUE
                # due → overdue at slot + grace/2
                next_transition = overdue_since + timedelta(minutes=grace_minutes / 2)
            elif next_dose_at is not None and next_dose_at <= now:
                status = STATUS_DUE
                # due → overdue at slot + grace/2
                next_transition = next_dose_at + timedelta(minutes=grace_minutes / 2)
            else:
                status = STATUS_NOT_DUE
                # not_due → due when the slot arrives
                next_transition = next_dose_at

        slot_remaining = None
        if self._tracking_type in (TRACKING_TIME_OF_DAY, TRACKING_REGULAR_INTERVAL):
            slot_remaining = self._compute_slot_remaining(entry, now, schedule_timestamps, pills_per_slot)

        self._attr_native_value = status
        self._attr_extra_state_attributes = {
            "role": "dose_status",
            "tracking_type": self._tracking_type,
            "next_dose_at": next_dose_at.isoformat() if next_dose_at else None,
            "overdue_since": overdue_since.isoformat() if overdue_since else None,
            "grace_minutes": grace_minutes,
            "safe_count": safe_count,
            "amount_24h": round(amount_24h, 3),
            "daily_limit": daily_limit,
            "pills_per_slot": pills_per_slot,
            "slot_remaining": slot_remaining,
        }

        self._arm_status_timer(next_transition, now)

    # ── Gates ───────────────────────────────────────────────────────────

    def _compute_24h_limit(self, entry, dose_timestamps, now):
        """Return (limit_reached, amount_24h, daily_limit).

        Mirrors Pill24hLimitExceededSensor: on when the 24h strength sum
        already exceeds ``daily_limit`` OR the next configured dose would
        push it over (pre-warning, no grace — safety limit).
        """
        daily_limit = float(entry.options.get("daily_limit", entry.data.get("daily_limit", 0)))
        strength = float(entry.options.get("strength", entry.data.get("strength", 0)))
        if daily_limit <= 0:
            return False, 0.0, daily_limit

        cutoff = now - timedelta(hours=_WINDOW_HOURS_24H)
        amount = sum(float(s) for ts, s in self.coordinator.data.dose_history if ts >= cutoff) if self.coordinator.data else 0.0
        already = amount > daily_limit
        would = (amount + strength) > daily_limit and not already
        return (already or would), amount, daily_limit

    def _window_expires_at(self, entry, dose_timestamps, now):
        """When the pill-count lockout releases (oldest dose exits the window).

        Buffered cutoff parity with PillLimitSensor / compute_safe_to_take.
        Returns None when it cannot be determined (next coordinator tick
        will re-evaluate).
        """
        from ..sliding_window import effective_dose_buffer_minutes, get_time_window

        time_window = get_time_window(entry, self._tracking_type)
        if time_window <= 0 or not dose_timestamps:
            return None
        buffer_minutes = effective_dose_buffer_minutes(entry, time_window)
        cutoff = now - timedelta(hours=time_window) + timedelta(minutes=buffer_minutes)
        valid = [ts for ts in dose_timestamps if ts >= cutoff]
        if not valid:
            return None
        return valid[0] + timedelta(hours=time_window) - timedelta(minutes=buffer_minutes)

    # ── Schedule position (scheduled tracking types) ────────────────────

    def _compute_next_dose(self, entry, now, schedule_timestamps):
        """Next scheduled slot datetime, or None when indeterminate.

        Regular Interval + Time of Day reuse the shared
        :func:`get_next_dose_time` (chained-deadline / slot-assignment
        parity with the next_dose sensor). Cyclic mirrors the next_dose
        sensor's inline branch (day-level coverage model).
        """
        if self._tracking_type in (TRACKING_REGULAR_INTERVAL, TRACKING_TIME_OF_DAY):
            return get_next_dose_time(entry, schedule_timestamps, now, self._tracking_type)

        if self._tracking_type == TRACKING_CYCLIC:
            return self._compute_next_dose_cyclic(entry, now, schedule_timestamps)

        return None  # As Needed handled earlier

    def _compute_next_dose_cyclic(self, entry, now, schedule_timestamps):
        """Cyclic next-dose — mirrors PillNextDoseSensor's cyclic branch."""
        days_on = int(entry.options.get("days_on", entry.data.get("days_on", 5)))
        days_off = int(entry.options.get("days_off", entry.data.get("days_off", 2)))
        anchor_str = entry.options.get("cycle_anchor_date", entry.data.get("cycle_anchor_date"))
        dose_time_str = entry.options.get("dose_time", entry.data.get("dose_time", "08:00"))
        try:
            anchor_date = date.fromisoformat(anchor_str)
        except (ValueError, TypeError):
            anchor_date = now.date()
        dose_hour, dose_minute = parse_dose_time(dose_time_str)

        cycle_length = days_on + days_off
        if cycle_length <= 0:
            cycle_length = 1
        days_since_anchor = (now.date() - anchor_date).days
        position_in_cycle = days_since_anchor % cycle_length
        dose_time_today = now.replace(hour=dose_hour, minute=dose_minute, second=0, microsecond=0)

        if not is_on_day(entry, now.date(), now.date()):
            days_until_next_on = cycle_length - position_in_cycle
            return dose_time_today + timedelta(days=days_until_next_on)
        if schedule_timestamps:
            last_ts = schedule_timestamps[-1]
            if last_ts.date() == now.date() and now >= dose_time_today:
                days_until_next_on = cycle_length - position_in_cycle
                if days_until_next_on == 0:
                    days_until_next_on = cycle_length
                return dose_time_today + timedelta(days=days_until_next_on)
            if now < dose_time_today:
                return dose_time_today
            return dose_time_today
        if now < dose_time_today:
            return dose_time_today
        days_until_next_on = cycle_length - position_in_cycle
        if days_until_next_on == 0:
            days_until_next_on = cycle_length
        return dose_time_today + timedelta(days=days_until_next_on)

    def _compute_overdue(self, entry, now, schedule_timestamps):
        """Most recent missed scheduled slot, or None.

        Mirrors PillOverdueSensor: shared slot-assignment model for Time of
        Day, chained-deadline model for Regular Interval, day-level
        coverage for Cyclic.
        """
        if self._tracking_type == TRACKING_TIME_OF_DAY:
            return self._compute_overdue_time_of_day(entry, now, schedule_timestamps)
        if self._tracking_type == TRACKING_REGULAR_INTERVAL:
            return self._compute_overdue_regular_interval(entry, now, schedule_timestamps)
        if self._tracking_type == TRACKING_CYCLIC:
            return self._compute_overdue_cyclic(entry, now, schedule_timestamps)
        return None

    def _compute_overdue_time_of_day(self, entry, now, timestamps):
        """Latest uncovered slot at or before now (parity with overdue.py)."""
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
        )

        overdue_since = None
        for a in assignments:
            if a.slot_time > now:
                break
            if not a.covered:
                overdue_since = a.slot_time
        return overdue_since

    def _compute_overdue_regular_interval(self, entry, now, timestamps):
        """Most recently *reached* chained deadline (parity with overdue.py)."""
        hours_between = entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 0))
        if not timestamps or hours_between <= 0:
            return None
        interval = timedelta(hours=hours_between)
        # max() not [-1]: the merged dose+skip list is not guaranteed sorted.
        anchor = max(timestamps)
        elapsed = now - anchor
        if elapsed <= timedelta(0):
            return None
        n = int(elapsed.total_seconds() // interval.total_seconds())
        if n <= 0:
            return None
        return anchor + n * interval

    def _compute_overdue_cyclic(self, entry, now, timestamps):
        """Today's dose_time if on an ON day and the day is uncovered."""
        dose_time_str = entry.options.get("dose_time", entry.data.get("dose_time", "08:00"))
        dose_hour, dose_minute = parse_dose_time(dose_time_str)
        if not is_on_day(entry, now.date(), now.date()):
            return None
        dose_time_today = now.replace(hour=dose_hour, minute=dose_minute, second=0, microsecond=0)
        if now < dose_time_today:
            return None
        if is_day_covered(now.date(), timestamps):
            return None
        return dose_time_today

    def _compute_slot_remaining(self, entry, now, schedule_timestamps, pills_per_slot):
        """Pills still owed in the current (most recently reached) slot.

        Time of Day: the latest slot at/before ``now`` from the shared
        slot-assignment model; remaining = pills_per_slot - assigned_count.
        Regular Interval: the latest chained deadline; remaining counts
        doses taken since that deadline.  Returns ``None`` when there is
        no active slot (nothing reached yet, or everything in the current
        slot is already covered).
        """
        if pills_per_slot <= 1:
            return None  # single-pill model: nothing meaningful to expose

        if self._tracking_type == TRACKING_TIME_OF_DAY:
            parsed_times = get_dose_times(entry)
            if not parsed_times:
                return None
            min_gap_minutes = 24 * 60
            for i in range(len(parsed_times)):
                for j in range(i + 1, len(parsed_times)):
                    gap = (parsed_times[j][0] * 60 + parsed_times[j][1]) - (
                        parsed_times[i][0] * 60 + parsed_times[i][1]
                    )
                    min_gap_minutes = min(min_gap_minutes, gap)
            early_grace = timedelta(minutes=max(30, min_gap_minutes // 2))
            assignments = compute_slot_assignments(
                parsed_times,
                schedule_timestamps,
                now,
                lookback_days=2,
                future_days=0,
                early_grace=early_grace,
                lateness_mode=LATENESS_UNTIL_NEXT_SLOT,
                pills_per_slot=pills_per_slot,
            )
            current = None
            for a in assignments:
                if a.slot_time > now:
                    break
                current = a
            if current is None:
                return None
            return max(0, pills_per_slot - current.assigned_count)

        if self._tracking_type == TRACKING_REGULAR_INTERVAL:
            hours_between = entry.options.get("hours_between_doses", entry.data.get("hours_between_doses", 0))
            if not schedule_timestamps or hours_between <= 0:
                return None
            interval = timedelta(hours=hours_between)
            anchor = max(schedule_timestamps)
            elapsed = now - anchor
            if elapsed <= timedelta(0):
                return None
            n = int(elapsed.total_seconds() // interval.total_seconds())
            if n <= 0:
                return None
            slot_start = anchor + n * interval
            taken_in_slot = sum(1 for ts in schedule_timestamps if slot_start <= ts < slot_start + interval)
            return max(0, pills_per_slot - taken_in_slot)

        return None

    # ── Latency boundary + timers ───────────────────────────────────────

    def _is_past_half_grace(self, now, overdue_since, grace_minutes) -> bool:
        """True when past half the grace window (card's latency boundary).

        The card computes ``overdueSeconds > (graceHours * 3600) / 2`` —
        i.e. half of grace_minutes in seconds. grace/2 minutes == the same
        instant expressed as a timedelta.
        """
        boundary = overdue_since + timedelta(seconds=(grace_minutes * 60) / 2)
        return now > boundary

    def _arm_status_timer(self, next_transition, now):
        """Arm a point-in-time callback at the next state-transition instant.

        Keeps the enum exact between coordinator ticks (HA best practice for
        time-derived states). Only future instants are armed; the 1-min tick
        remains the fallback recompute path.
        """
        self._cancel_status_timer()
        if next_transition is None:
            return
        delta = (next_transition - now).total_seconds()
        if delta <= 0:
            return
        self._status_timer_unsub = async_call_later(self.hass, delta, self._on_status_timer)