"""Master Tracker Sleep Disruption sensors — caffeine / alcohol sleep impact.

Hosted on the virtual Caffeine Tracker / Alcohol Tracker devices (created
by the Drink Settings singleton).  Two sensor classes live here:

* :class:`DrinkMasterSleepDisruptionSensor` — categorical state (``None`` /
  ``Low`` / ``Moderate`` / ``High``) read from the matching
  :class:`DrinkMasterCoordinator` ``body_mass``.  Band labels are bare
  (no unit suffix) so dashboards render cleanly; the thresholds live in the
  README documentation.

* :class:`DrinkMasterEstimatedLowTimeSensor` — a timestamp sensor whose
  state is the ISO-8601 wall-clock time at which the body-mass is expected
  to decay into the *Low* band (the first sleep-relevant improvement
  milestone — more realistic to watch than the asymptotic None band).
  Carries ``estimated_none_time`` as an attribute (the sleep-safe moment).

Both classes subscribe to the shared master coordinator via
``async_add_listener`` (not ``CoordinatorEntity`` — the master coordinator is
shared across all drinks of a substance, not tied to one config entry) and
expose ``drink_master: True`` in extra state attributes so the frontend card
can filter them out.
"""

from __future__ import annotations

import math

# Per-substance metadata + ordered disruption bands.
#
# Each band is a tuple ``(upper_bound_exclusive, label)``.  The first band
# whose ``upper_bound_exclusive`` is strictly greater than the body-mass
# wins.  The final band uses ``math.inf`` so any value above the previous
# band's upper bound is captured.
#
# Units: caffeine body-mass is in mg; alcohol body-mass is in g (confirmed
# with the user — the original "mg" wording for alcohol was a typo).
from datetime import datetime

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from ..const import (
    ALCOHOL_TRACKER_ID,
    CAFFEINE_TRACKER_ID,
    DOMAIN,
    DRINK_TYPE_ALCOHOL,
    DRINK_TYPE_CAFFEINE,
)
from ..drink_coordinator import DrinkMasterCoordinator

_TRACKER_INFO = {
    DRINK_TYPE_CAFFEINE: {
        "tracker_id": CAFFEINE_TRACKER_ID,
        "disruption_unique_id": "drink_master_sleep_disruption_caffeine",
        "disruption_translation_key": "sleep_disruption_caffeine",
        "estimated_low_unique_id": "drink_master_estimated_low_time_caffeine",
        "estimated_low_translation_key": "estimated_low_time_caffeine",
        "icon": "mdi:bed-clock",
        "unit": "mg",
        # (upper_bound_exclusive, label) — bare labels, no unit suffix.
        "bands": [
            (11, "None"),
            (31, "Low"),
            (61, "Moderate"),
            (math.inf, "High"),
        ],
        # Upper bound of the None band (sleep-safe threshold).
        "none_threshold": 10.0,
        # Upper bound of the Low band — the Estimated Low Time target.
        "low_threshold": 11.0,
    },
    DRINK_TYPE_ALCOHOL: {
        "tracker_id": ALCOHOL_TRACKER_ID,
        "disruption_unique_id": "drink_master_sleep_disruption_alcohol",
        "disruption_translation_key": "sleep_disruption_alcohol",
        "estimated_low_unique_id": "drink_master_estimated_low_time_alcohol",
        "estimated_low_translation_key": "estimated_low_time_alcohol",
        "icon": "mdi:glass-wine",
        "unit": "g",
        "bands": [
            (1, "None"),
            (11, "Low"),
            (31, "Moderate"),
            (math.inf, "High"),
        ],
        # None band: 0 g (sleep-safe threshold).
        "none_threshold": 0.0,
        # Upper bound of the Low band — the Estimated Low Time target.
        "low_threshold": 1.0,
    },
}


def _classify(bands: list[tuple[float, str]], mass: float) -> tuple[int, str]:
    """Return ``(band_index, label)`` for ``mass`` against the ordered bands."""
    for idx, (upper, label) in enumerate(bands):
        if mass < upper:
            return idx, label
    # Should be unreachable because the last band uses math.inf, but guard
    # anyway in case the bands list is misconfigured.
    return len(bands) - 1, bands[-1][1]


class DrinkMasterSleepDisruptionSensor(RestoreSensor):
    """Categorical sleep-disruption band sensor on a Master Tracker device."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # Categorical string sensor — no state_class / native unit.

    def __init__(self, settings_entry, coordinator: DrinkMasterCoordinator) -> None:
        """Initialize the sleep-disruption band sensor."""
        info = _TRACKER_INFO[coordinator.substance]
        self._coordinator = coordinator
        self._substance = coordinator.substance
        self._unit = info["unit"]
        self._bands = info["bands"]
        self._none_threshold = info["none_threshold"]
        self._attr_unique_id = info["disruption_unique_id"]
        self._attr_translation_key = info["disruption_translation_key"]
        self._attr_icon = info["icon"]
        # Stable device identifiers — standalone virtual Master Tracker
        # device, not tied to entry_id (mirrors DrinkMasterSensor).
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info["tracker_id"])},
            manufacturer="AX Dose Logger",
            model="Master Tracker",
        )
        self._attr_extra_state_attributes = {
            "substance": self._substance,
            "drink_master": True,  # Frontend filter marker
            "role": "sleep_disruption",  # Frontend classifier (survives entity_id renames)
            "body_mass": 0.0,
            "body_mass_unit": self._unit,
            "current_band": None,
            "next_band": None,
            "minutes_until_next_band": None,
        }

    async def async_added_to_hass(self) -> None:
        """Restore last state, then subscribe to the master coordinator."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (None, "unknown", "unavailable"):
            # Restore the textual band; attributes are recomputed below.
            self._attr_native_value = last_state.state
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )
        # Push the current coordinator state immediately.
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute the disruption band + predictive attributes on updates."""
        data = self._coordinator.data
        if data is None:
            return
        mass = float(data.body_mass)
        idx, label = _classify(self._bands, mass)
        self._attr_native_value = label

        # Predictive attributes (the Estimated Low / None timestamps live
        # on the dedicated DrinkMasterEstimatedLowTimeSensor — kept here
        # only as the minutes_until_next_band hint for dashboards).
        next_band_label: str | None = None
        minutes_until_next: int | None = None
        if idx + 1 < len(self._bands):
            next_band_label = self._bands[idx + 1][1]
            # Boundary between the current band and the next-lower band is
            # the lower edge of the current band == upper edge of the next
            # band.  The next-lower band's ``upper_bound_exclusive`` is the
            # boundary body-mass must decay below to drop into it.
            next_boundary = self._bands[idx][0]
            if next_boundary != math.inf:
                eta = self._coordinator.estimate_time_to_body_mass(next_boundary)
                if eta is not None:
                    minutes_until_next = int(round(eta.total_seconds() / 60.0))

        self._attr_extra_state_attributes = {
            "substance": self._substance,
            "drink_master": True,
            "role": "sleep_disruption",
            "body_mass": round(mass, 2),
            "body_mass_unit": self._unit,
            "current_band": label,
            "next_band": next_band_label,
            "minutes_until_next_band": minutes_until_next,
        }
        self.async_write_ha_state()


class DrinkMasterEstimatedLowTimeSensor(RestoreSensor):
    """Timestamp sensor — predicted wall-clock time the body-mass enters Low.

    The Low band is the first sleep-relevant improvement milestone and is
    more realistic to track than the asymptotic None band (caffeine decays
    exponentially toward zero; alcohol's None target is exactly 0 g which
    only occurs once the liver has fully cleared the ethanol).  The state
    is an ISO-8601 datetime; ``estimated_none_time`` is carried as an
    attribute for the sleep-safe moment.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, settings_entry, coordinator: DrinkMasterCoordinator) -> None:
        """Initialize the Estimated Low Time timestamp sensor."""
        info = _TRACKER_INFO[coordinator.substance]
        self._coordinator = coordinator
        self._substance = coordinator.substance
        self._low_threshold = info["low_threshold"]
        self._none_threshold = info["none_threshold"]
        self._attr_unique_id = info["estimated_low_unique_id"]
        self._attr_translation_key = info["estimated_low_translation_key"]
        self._attr_icon = "mdi:bed-clock"
        # Stable device identifiers — standalone virtual Master Tracker
        # device, not tied to entry_id (mirrors DrinkMasterSensor).
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info["tracker_id"])},
            manufacturer="AX Dose Logger",
            model="Master Tracker",
        )
        self._attr_extra_state_attributes = {
            "substance": self._substance,
            "drink_master": True,  # Frontend filter marker
            "role": "estimated_low_time",  # Frontend classifier (survives entity_id renames)
            "low_threshold": self._low_threshold,
            "estimated_none_time": None,
        }

    async def async_added_to_hass(self) -> None:
        """Restore last state, then subscribe to the master coordinator."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (None, "unknown", "unavailable"):
            # Timestamp sensors store ISO strings in the state; parse back to
            # a datetime so HA's TIMESTAMP device class accepts it.
            restored = dt_util.parse_datetime(last_state.state)
            if restored is not None:
                self._attr_native_value = restored
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )
        # Push the current coordinator state immediately.
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute the estimated Low + None wall-clock times on updates."""
        data = self._coordinator.data
        if data is None:
            return
        mass = float(data.body_mass)

        estimated_low_time: datetime | None = None
        if mass > self._low_threshold:
            eta_low = self._coordinator.estimate_time_to_body_mass(self._low_threshold)
            if eta_low is not None:
                estimated_low_time = dt_util.now() + eta_low

        estimated_none_time: datetime | None = None
        if mass > self._none_threshold:
            eta_none = self._coordinator.estimate_time_to_body_mass(self._none_threshold)
            if eta_none is not None:
                estimated_none_time = dt_util.now() + eta_none

        # TIMESTAMP device class requires a datetime object (or None), not an
        # ISO string — HA serializes the datetime to ISO for the state API.
        self._attr_native_value = estimated_low_time
        self._attr_extra_state_attributes = {
            "substance": self._substance,
            "drink_master": True,
            "role": "estimated_low_time",
            "low_threshold": self._low_threshold,
            "estimated_none_time": estimated_none_time,
        }
        self.async_write_ha_state()
