from datetime import timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import callback

from ..const import DOSE_BUFFER_DEFAULT_MIN, TRACKING_CYCLIC, get_pills_per_slot
from ..entity import AxDoseLoggerSensorEntity
from ..sliding_window import effective_dose_buffer_minutes, get_time_window, is_on_day

# Cap for timestamps attribute: prune older than 365 days, keep last 100
_TIMESTAMPS_MAX_DAYS = 365
_TIMESTAMPS_MAX_COUNT = 100


class PillLimitSensor(AxDoseLoggerSensorEntity, RestoreSensor):
    """Pills safe to take — the pharmacological safety gate ONLY.

    Answers one question: *if a dose were taken now, would it exceed
    ``pill_limit`` within ``time_window_hours``?*  It reads the dose history
    and the rolling window and nothing else.

    **Cyclic OFF days no longer force this to 0.**  That clamp conflated a
    schedule fact with a safety measurement: a 48-hour schedule with a
    30-hour window is legitimately available for 30 of every 48 hours, and
    reporting 0 on the intervening OFF day produced a false "LIMIT REACHED"
    (which the card offers to override and the reminder blueprint hard-blocks
    on).  Whether *today* is a dose day is the ``due`` / ``overdue`` /
    ``not_due`` schedule gate's job — see ``schedule.compute_cyclic_slot_assignments``.
    The ``cyclic_off_day`` attribute below still reports the phase for
    informatics, but it never alters the count.

    ``lockout_reason`` makes a 0 self-explanatory: only ``window_full`` can
    produce it, so "limit reached" can never again mean "it's a rest day".
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "pills_safe_to_take"
        self._attr_unique_id = f"{entry.entry_id}_pills_safe_to_take"
        self._attr_icon = "mdi:pill"
        self._tracking_type = entry.data.get("tracking_type")
        self._attr_extra_state_attributes = {"timestamps": []}
        self._attr_native_value = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Legacy restore for smooth UI transition; coordinator is
        # authoritative so _handle_coordinator_update overrides.
        last_state_obj = await self.async_get_last_state()
        if last_state_obj and "timestamps" in last_state_obj.attributes:
            self._update_state()
            self.async_write_ha_state()

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
        max_pills = entry.options.get("pill_limit", entry.data.get("pill_limit", 1))
        time_window = get_time_window(entry, self._tracking_type)
        # Anti-drift dose buffer: relaxes the rolling window so gradual
        # dose-timing drift is bounded (see effective_dose_buffer_minutes).
        buffer_minutes = effective_dose_buffer_minutes(entry, time_window)
        raw_buffer = entry.options.get(
            "dose_buffer_minutes",
            entry.data.get("dose_buffer_minutes", DOSE_BUFFER_DEFAULT_MIN),
        )
        timestamps = self._get_timestamps()

        # ONE unified sliding window for EVERY tracking type — including
        # Cyclic.  The cutoff is moved forward by the buffer so the oldest
        # dose expires `buffer` minutes earlier than the strict window,
        # re-anchoring doses taken a few minutes late.  There is deliberately
        # no calendar/cycle branch here: only an elapsed-time window may
        # report a lockout (see the class docstring).
        cutoff = now - timedelta(hours=time_window) + timedelta(minutes=buffer_minutes)
        valid_timestamps = [ts for ts in timestamps if ts >= cutoff]
        safe_to_take = max(0, max_pills - len(valid_timestamps))
        self._attr_native_value = safe_to_take

        window_expires_at = None
        if max_pills > 0 and len(valid_timestamps) >= max_pills and valid_timestamps:
            # The window expires `buffer` minutes earlier than the strict
            # window (the dose falls out of the count at window - buffer).
            window_expires_at = (
                valid_timestamps[0] + timedelta(hours=time_window) - timedelta(minutes=buffer_minutes)
            ).isoformat()

        # Prune the informational timestamps attribute to the last 365 days
        # and cap it at 100 entries (unchanged budget — the attribute exists
        # for automations/card detail, not for the gate maths above).
        recent_cutoff = now - timedelta(days=_TIMESTAMPS_MAX_DAYS)
        recent = [ts for ts in valid_timestamps if ts >= recent_cutoff][-_TIMESTAMPS_MAX_COUNT:]

        # Phase reported for informatics ONLY — it never alters the count.
        is_off_day = self._tracking_type == TRACKING_CYCLIC and not is_on_day(entry, now.date(), now.date())

        self._attr_extra_state_attributes = {
            "timestamps": [ts.isoformat() for ts in recent],
            "time_window_hours": time_window,
            "window_expires_at": window_expires_at,
            # Only the rolling window can lock out.  Exposing the reason stops
            # "limit reached" from ever again ambiguously meaning "rest day".
            "lockout_reason": "window_full" if safe_to_take <= 0 else None,
            "cyclic_off_day": is_off_day,
            "dose_buffer_minutes": raw_buffer,
            "effective_buffer_minutes": buffer_minutes,
            "pills_per_slot": get_pills_per_slot(entry),
        }
