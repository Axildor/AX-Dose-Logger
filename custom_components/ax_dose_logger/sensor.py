from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEVICE_CATEGORY_DRINK_SETTINGS,
    DEVICE_CATEGORY_DRINKS,
    DOMAIN,
    DRINK_TYPE_ALCOHOL,
    DRINK_TYPE_CAFFEINE,
    TRACKING_AS_NEEDED,
)
from .data import AxDoseLoggerConfigEntry
from .drink_coordinator import DrinkCoordinator, DrinkMasterCoordinator
from .sensors.adherence import PillAdherenceSensor
from .sensors.avg_doses import PillAvgDosesSensor
from .sensors.concentration import PillConcentrationSensor
from .sensors.daily_remaining import PillDailyRemainingSensor
from .sensors.days_left import (
    DrinkDaysLeftSensor,
    PillDaysLeftSensor,
)
from .sensors.days_since_first_dose import PillDaysSinceFirstDoseSensor
from .sensors.dose_status import PillDoseStatusSensor
from .sensors.drink_avg_doses import DrinkAvgDosesSensor
from .sensors.drink_cooldown import DrinkCooldownSensor
from .sensors.drink_last_dose import DrinkLastDoseSensor
from .sensors.drink_master import DrinkMasterSensor
from .sensors.drink_master_avg import DrinkMasterAvgDosesSensor
from .sensors.drink_master_daily_amount import DrinkMasterDailyAmountSensor
from .sensors.drink_master_daily_remaining import DrinkMasterDailyRemainingSensor
from .sensors.drink_master_last_dose import DrinkMasterLastDoseSensor
from .sensors.drink_master_sleep_disruption import (
    DrinkMasterEstimatedLowTimeSensor,
    DrinkMasterEstimatedNoneTimeSensor,
    DrinkMasterLowHoursUntilSensor,
    DrinkMasterNextBandSensor,
    DrinkMasterSleepDisruptionSensor,
)
from .sensors.drink_total import DrinkTotalSensor
from .sensors.last_dose import PillLastDoseSensor
from .sensors.limit_exceeded import Pill24hLimitExceededSensor
from .sensors.next_dose import PillNextDoseSensor
from .sensors.overdue import PillOverdueSensor
from .sensors.pill_daily_amount import PillDailyAmountSensor
from .sensors.pill_limit import PillLimitSensor
from .sensors.steady_state import PillSteadyStateSensor
from .sensors.strength import PillStrengthSensor
from .sensors.total import PillTotalSensor


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AxDoseLoggerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    category = entry.data.get("device_category")

    if category == DEVICE_CATEGORY_DRINK_SETTINGS:
        await _setup_drink_settings_sensors(hass, entry, async_add_entities)
        return

    if category == DEVICE_CATEGORY_DRINKS:
        await _setup_drink_sensors(hass, entry, async_add_entities)
        return

    # --- Medicine (legacy) ---
    await _setup_medicine_sensors(hass, entry, async_add_entities)


async def _setup_medicine_sensors(
    hass: HomeAssistant,
    entry: AxDoseLoggerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    tracking_type = entry.data.get("tracking_type")
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = [PillTotalSensor(entry, coordinator)]
    entities.append(PillDailyAmountSensor(entry, coordinator))
    # 24h strength limit exceeded binary sensor — only created when a
    # daily_limit is configured (0 = no limit → no entity). Fires when
    # the current 24h sum already exceeds the limit OR when the next
    # configured dose would push it over (pre-warning). Toggling
    # daily_limit between 0 and >0 triggers entity recreation via
    # _STRUCTURAL_KEYS in __init__.py.
    daily_limit = float(entry.options.get("daily_limit", entry.data.get("daily_limit", 0)))
    if daily_limit > 0:
        entities.append(Pill24hLimitExceededSensor(entry, coordinator))
        # Daily Remaining — daily_limit − amount_24h as a standalone entity
        # (promoted from the daily-amount sensor's `remaining` attribute).
        # Same guard as the limit-exceeded binary sensor: no dead entity
        # when no limit is configured.
        entities.append(PillDailyRemainingSensor(entry, coordinator))
    entities.append(PillLastDoseSensor(entry, coordinator))
    entities.append(PillLimitSensor(entry, coordinator))
    entities.append(PillConcentrationSensor(entry, coordinator))
    entities.append(PillNextDoseSensor(entry, coordinator))
    entities.append(PillAvgDosesSensor(entry, coordinator, 7))
    entities.append(PillAvgDosesSensor(entry, coordinator, 14))
    entities.append(PillAvgDosesSensor(entry, coordinator, 30))
    entities.append(PillAvgDosesSensor(entry, coordinator, 365))
    # Steady state and overdue are only meaningful for scheduled medications
    # (steady state requires a fixed dosing interval τ; overdue requires a schedule)
    if tracking_type != TRACKING_AS_NEEDED:
        entities.append(PillSteadyStateSensor(entry, coordinator))
        entities.append(PillOverdueSensor(entry, coordinator))
    entities.append(PillStrengthSensor(entry, coordinator))
    # Dose Status enum sensor — single-source-of-truth state for automations
    # + the card (not_due/due/overdue/limit_reached/limit_24h/ok). Created
    # for ALL tracking types: As-Needed meds report ok/limit_reached/limit_24h.
    entities.append(PillDoseStatusSensor(entry, coordinator))
    entities.append(PillDaysSinceFirstDoseSensor(entry, coordinator))
    # Days-left inventory-burn indicator.  Scheduled medications show
    # "Days left" (config-derived doses/day); As-Needed medications show
    # "Est. days left" (7-day average doses/day).  Created for all tracking
    # types since it reads the Pills Left stock number entity directly.
    entities.append(PillDaysLeftSensor(entry, coordinator))
    enable_adherence = entry.options.get(
        "enable_adherence", entry.data.get("enable_adherence", tracking_type != TRACKING_AS_NEEDED)
    )
    if enable_adherence:
        entities.append(PillAdherenceSensor(entry, coordinator, 7))
        entities.append(PillAdherenceSensor(entry, coordinator, 14))
        entities.append(PillAdherenceSensor(entry, coordinator, 30))
        entities.append(PillAdherenceSensor(entry, coordinator, 365))
    async_add_entities(entities)


async def _setup_drink_sensors(
    hass: HomeAssistant,
    entry: AxDoseLoggerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Granular drink device sensors — replicate medicine local stats.

    Drinks have no schedule, so they use the as_needed avg path (simple
    count / days).  No steady_state / overdue / pill_limit / next_dose /
    strength / days_since_first_dose / adherence (those are medicine-only).
    """
    coordinator: DrinkCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = [
        DrinkTotalSensor(entry, coordinator),
        DrinkLastDoseSensor(entry, coordinator),
        DrinkAvgDosesSensor(entry, coordinator, 7),
        DrinkAvgDosesSensor(entry, coordinator, 14),
        DrinkAvgDosesSensor(entry, coordinator, 30),
        DrinkAvgDosesSensor(entry, coordinator, 365),
    ]
    # Est. days left — inventory burn from 7-day average doses/day.  Reads
    # the matching DrinkStockNumber entity, so it's created for every drink
    # regardless of cooldown configuration.
    entities.append(DrinkDaysLeftSensor(entry, coordinator))
    # Cooldown sensor is only created when a cooldown window is configured,
    # so drinks without a cooldown have no dead sensor entity. The card reads
    # this entity (native 0/1 + cooldown_ends_at) to soft-disable the Log
    # button + show a Last/Next countdown, mirroring the medicine pill_limit
    # sensor. The backend never blocks a log — override is always available.
    cooldown_window = float(
        entry.options.get(
            "cooldown_window",
            entry.data.get("cooldown_window", 0),
        )
    )
    if cooldown_window > 0:
        entities.append(DrinkCooldownSensor(entry, coordinator))
    async_add_entities(entities)


async def _setup_drink_settings_sensors(
    hass: HomeAssistant,
    entry: AxDoseLoggerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Drink Settings entry (a profile) -- instantiate THIS profile's Master Tracker sensors.

    The master coordinators are created in ``async_setup_entry`` (in
    ``__init__.py``) and stored in ``hass.data[DOMAIN]["_drink_masters"]``
    keyed by ``(profile_id, substance)``.  The Master Tracker devices use
    profile-scoped stable identifiers (the immutable profile_id, not the
    entry_id) so they survive Drink Settings entry recreation and each
    profile gets distinct devices.

    Per substance, the master PK sensor + last-dose + sleep-disruption +
    estimated-low-time + low-hours-until + daily-amount + 4 avg-doses sensors
    are created on this profile's Master Tracker device, reading the
    profile's master coordinator (which aggregates only the doses routed to
    this profile).
    """
    from .const import DEFAULT_PROFILE_ID

    masters: dict[tuple[str, str], DrinkMasterCoordinator] = hass.data[DOMAIN].get("_drink_masters", {})
    # Resolve the immutable profile_id + mutable display name for this entry.
    if entry.unique_id == "drink_settings":
        profile_id = DEFAULT_PROFILE_ID
    else:
        profile_id = entry.data.get("profile_id", DEFAULT_PROFILE_ID)
    profile_name = entry.data.get("profile_name")
    entities = []
    for substance in (DRINK_TYPE_CAFFEINE, DRINK_TYPE_ALCOHOL):
        master = masters.get((profile_id, substance))
        if master is None:
            continue
        entities.append(DrinkMasterSensor(entry, master, profile_id, profile_name))
        entities.append(DrinkMasterLastDoseSensor(entry, master, profile_id, profile_name))
        entities.append(DrinkMasterSleepDisruptionSensor(entry, master, profile_id, profile_name))
        entities.append(DrinkMasterNextBandSensor(entry, master, profile_id, profile_name))
        entities.append(DrinkMasterEstimatedLowTimeSensor(entry, master, profile_id, profile_name))
        entities.append(DrinkMasterEstimatedNoneTimeSensor(entry, master, profile_id, profile_name))
        entities.append(DrinkMasterLowHoursUntilSensor(entry, master, profile_id, profile_name))
        entities.append(DrinkMasterDailyAmountSensor(entry, master, profile_id, profile_name))
        # Daily Remaining — per-substance limit − amount_24h as a standalone
        # entity (promoted from the daily-amount sensor's `remaining`
        # attribute). Created only when the per-substance limit > 0
        # (caffeine's 400 mg default always qualifies; alcohol is skipped
        # unless a limit is configured) — no dead entity when no limit is set.
        limit_key = "caffeine_daily_limit_mg" if substance == DRINK_TYPE_CAFFEINE else "alcohol_daily_limit_g"
        default_limit = 400.0 if substance == DRINK_TYPE_CAFFEINE else 0.0
        limit_val = float(entry.options.get(limit_key, entry.data.get(limit_key, default_limit)))
        if limit_val > 0:
            entities.append(DrinkMasterDailyRemainingSensor(entry, master, profile_id, profile_name))
        for window in (7, 14, 30, 365):
            entities.append(DrinkMasterAvgDosesSensor(entry, master, window, profile_id, profile_name))
    async_add_entities(entities)
