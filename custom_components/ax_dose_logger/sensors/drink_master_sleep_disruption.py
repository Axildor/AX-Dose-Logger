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
from datetime import datetime

# Per-substance metadata + ordered disruption bands.
#
# Each band is a tuple ``(upper_bound_exclusive, label)``.  The first band
# whose ``upper_bound_exclusive`` is strictly greater than the body-mass
# wins.  The final band uses ``math.inf`` so any value above the previous
# band's upper bound is captured.
#
# Units: caffeine body-mass is in mg; alcohol body-mass is in g (confirmed
# with the user — the original "mg" wording for alcohol was a typo).
import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from ..const import (
    ALCOHOL_TRACKER_ID,
    CAFFEINE_TRACKER_ID,
    DOMAIN,
    DRINK_LOW_THRESHOLD,
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
        "low_hours_until_unique_id": "drink_master_low_hours_until_caffeine",
        "low_hours_until_translation_key": "low_hours_until_caffeine",
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
        # Reads from the shared DRINK_LOW_THRESHOLD constant (single source of
        # truth; the master coordinator's predict_low_time_if_dose uses the
        # same value so the popup prediction matches the sensor's target).
        "low_threshold": DRINK_LOW_THRESHOLD[DRINK_TYPE_CAFFEINE],
    },
    DRINK_TYPE_ALCOHOL: {
        "tracker_id": ALCOHOL_TRACKER_ID,
        "disruption_unique_id": "drink_master_sleep_disruption_alcohol",
        "disruption_translation_key": "sleep_disruption_alcohol",
        "estimated_low_unique_id": "drink_master_estimated_low_time_alcohol",
        "estimated_low_translation_key": "estimated_low_time_alcohol",
        "low_hours_until_unique_id": "drink_master_low_hours_until_alcohol",
        "low_hours_until_translation_key": "low_hours_until_alcohol",
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
        # Reads from the shared DRINK_LOW_THRESHOLD constant (see caffeine note).
        "low_threshold": DRINK_LOW_THRESHOLD[DRINK_TYPE_ALCOHOL],
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
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_coordinator_update))
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
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_coordinator_update))
        # Push the current coordinator state immediately.
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute the estimated Low + None wall-clock times on updates.

        The gate anchors on the **forecasted peak body mass** for caffeine
        (``data.peak_body_mass``), not the instantaneous ``body_mass``.  At
        the moment a caffeine dose is logged the current body mass is still
        ~0 (absorption has not started), so gating on the current mass would
        keep the sensor ``unknown`` for ~30 min until absorption raises the
        mass above the Low threshold.  Anchoring on the forecasted peak
        matches the design of :meth:`estimate_time_to_body_mass` (which
        already anchors at the peak internally) and makes the sensor emit a
        real predicted time the instant a dose is logged — the intended
        behaviour of the predictive Low feature.  For alcohol
        ``peak_body_mass == body_mass`` (instant absorption), so the gate is
        unchanged.
        """
        data = self._coordinator.data
        if data is None:
            return
        mass = float(data.body_mass)
        # Caffeine forecasts the peak; alcohol's peak == current body mass.
        anchor_mass = float(data.peak_body_mass) if data.peak_body_mass else mass

        estimated_low_time: datetime | None = None
        if anchor_mass > self._low_threshold:
            eta_low = self._coordinator.estimate_time_to_body_mass(self._low_threshold)
            if eta_low is not None:
                estimated_low_time = dt_util.now() + eta_low

        estimated_none_time: datetime | None = None
        if anchor_mass > self._none_threshold:
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


class DrinkMasterLowHoursUntilSensor(RestoreSensor):
    """DURATION countdown sensor — hours until the body-mass enters Low.

    A numeric companion to :class:`DrinkMasterEstimatedLowTimeSensor` for users
    who prefer a countdown over a wall-clock timestamp.  The ``native_value``
    is the number of hours remaining until the body-mass decays into the *Low*
    band (the first sleep-relevant improvement milestone), rounded to 1
    decimal.  ``None`` (unknown) when the body-mass is already in the Low band
    or lower — no countdown is needed once you have arrived.

    Carries ``estimated_none_hours`` as an attribute — the longer-horizon
    countdown to the sleep-safe None band (``None`` once in the None band).

    Reuses the per-substance ``low_threshold`` (the upper bound of the Low
    band, already the Estimated Low Time target) + the coordinator's existing
    ``estimate_time_to_body_mass(target)`` method — no coordinator / store /
    migration changes.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 1

    def __init__(self, settings_entry, coordinator: DrinkMasterCoordinator) -> None:
        """Initialize the Low - Hours Until countdown sensor."""
        info = _TRACKER_INFO[coordinator.substance]
        self._coordinator = coordinator
        self._substance = coordinator.substance
        self._low_threshold = info["low_threshold"]
        self._none_threshold = info["none_threshold"]
        self._unit = info["unit"]
        self._attr_unique_id = info["low_hours_until_unique_id"]
        self._attr_translation_key = info["low_hours_until_translation_key"]
        self._attr_icon = "mdi:timer-sand"
        # Stable device identifiers — standalone virtual Master Tracker
        # device, not tied to entry_id (mirrors the sibling sensors).
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info["tracker_id"])},
            manufacturer="AX Dose Logger",
            model="Master Tracker",
        )
        self._attr_extra_state_attributes = {
            "substance": self._substance,
            "drink_master": True,  # Frontend filter marker
            "role": "low_hours_until",  # Frontend classifier (survives entity_id renames)
            "low_threshold": self._low_threshold,
            "low_threshold_unit": self._unit,
            "estimated_none_hours": None,
        }

    async def async_added_to_hass(self) -> None:
        """Restore last state, then subscribe to the master coordinator."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (
            None,
            "unknown",
            "unavailable",
        ):
            try:
                self._attr_native_value = float(last_state.state)
            except TypeError, ValueError:
                # Non-numeric restored state — ignore; the coordinator push
                # below recomputes the correct value immediately.
                pass
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_coordinator_update))
        # Push the current coordinator state immediately.
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute the hours-until-Low + hours-until-None countdowns.

        The gate anchors on the **forecasted peak body mass** for caffeine
        (``data.peak_body_mass``), not the instantaneous ``body_mass`` — see
        the matching note on :class:`DrinkMasterEstimatedLowTimeSensor`.  At
        the moment a caffeine dose is logged the current body mass is still
        ~0 (absorption not started), so gating on the current mass would keep
        the countdown ``unknown`` until absorption raises the mass above the
        threshold.  Anchoring on the forecasted peak emits a real countdown
        the instant a dose is logged.  For alcohol ``peak_body_mass ==
        body_mass``, so the gate is unchanged.
        """
        data = self._coordinator.data
        if data is None:
            return
        mass = float(data.body_mass)
        # Caffeine forecasts the peak; alcohol's peak == current body mass.
        anchor_mass = float(data.peak_body_mass) if data.peak_body_mass else mass

        # Hours until the body-mass decays into the Low band.
        # None when already in Low or below (no countdown needed).
        hours_until_low: float | None = None
        if anchor_mass > self._low_threshold:
            eta_low = self._coordinator.estimate_time_to_body_mass(self._low_threshold)
            if eta_low is not None:
                hours_until_low = round(eta_low.total_seconds() / 3600.0, 1)

        # Longer-horizon countdown to the sleep-safe None band.
        estimated_none_hours: float | None = None
        if anchor_mass > self._none_threshold:
            eta_none = self._coordinator.estimate_time_to_body_mass(self._none_threshold)
            if eta_none is not None:
                estimated_none_hours = round(eta_none.total_seconds() / 3600.0, 1)

        self._attr_native_value = hours_until_low
        self._attr_extra_state_attributes = {
            "substance": self._substance,
            "drink_master": True,
            "role": "low_hours_until",
            "low_threshold": self._low_threshold,
            "low_threshold_unit": self._unit,
            "estimated_none_hours": estimated_none_hours,
        }
        self.async_write_ha_state()
