from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEVICE_CATEGORY_DRINKS,
    DEVICE_CATEGORY_DRINK_SETTINGS,
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
from .sensors.days_since_first_dose import PillDaysSinceFirstDoseSensor
from .sensors.drink_avg_doses import DrinkAvgDosesSensor
from .sensors.drink_last_dose import DrinkLastDoseSensor
from .sensors.drink_master_avg import DrinkMasterAvgDosesSensor
from .sensors.drink_master import DrinkMasterSensor
from .sensors.drink_total import DrinkTotalSensor
from .sensors.last_dose import PillLastDoseSensor
from .sensors.next_dose import PillNextDoseSensor
from .sensors.overdue import PillOverdueSensor
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
    entities.append(PillDaysSinceFirstDoseSensor(entry, coordinator))
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
    async_add_entities(entities)


async def _setup_drink_settings_sensors(
    hass: HomeAssistant,
    entry: AxDoseLoggerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Drink Settings singleton — instantiate the Master Tracker sensors.

    The master coordinators are created in ``async_setup_entry`` (in
    ``__init__.py``) and stored in ``hass.data[DOMAIN]["_drink_masters"]``.
    The Master Tracker devices use stable identifiers (not entry_id) so
    they survive Drink Settings entry recreation.

    Per substance, two sensor types are created on the Master Tracker device:
      * ``DrinkMasterSensor`` — global PK body mass (mg/g).
      * ``DrinkMasterAvgDosesSensor`` ×4 — rolling avg daily drink count over
        7/14/30/365-day windows, aggregating *every* drink of that substance
        across all granular drink devices (reads the master coordinator's
        aggregated dose_history, which is the union of all granular drinks).
    """
    masters: dict[str, DrinkMasterCoordinator] = hass.data[DOMAIN].get("_drink_masters", {})
    entities = []
    if DRINK_TYPE_CAFFEINE in masters:
        master = masters[DRINK_TYPE_CAFFEINE]
        entities.append(DrinkMasterSensor(entry, master))
        for window in (7, 14, 30, 365):
            entities.append(DrinkMasterAvgDosesSensor(entry, master, window))
    if DRINK_TYPE_ALCOHOL in masters:
        master = masters[DRINK_TYPE_ALCOHOL]
        entities.append(DrinkMasterSensor(entry, master))
        for window in (7, 14, 30, 365):
            entities.append(DrinkMasterAvgDosesSensor(entry, master, window))
    async_add_entities(entities)
