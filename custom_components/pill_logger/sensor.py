from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN, TRACKING_AS_NEEDED
from .data import PillLoggerConfigEntry
from .sensors.total import PillTotalSensor
from .sensors.last_dose import PillLastDoseSensor
from .sensors.pill_limit import PillLimitSensor
from .sensors.concentration import PillConcentrationSensor
from .sensors.next_dose import PillNextDoseSensor
from .sensors.avg_doses import PillAvgDosesSensor
from .sensors.steady_state import PillSteadyStateSensor
from .sensors.strength import PillStrengthSensor
from .sensors.adherence import PillAdherenceSensor
from .sensors.days_since_first_dose import PillDaysSinceFirstDoseSensor

async def async_setup_entry(
    hass: HomeAssistant,
    entry: PillLoggerConfigEntry,
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
    # Steady state is only meaningful for scheduled medications (requires a fixed dosing interval τ)
    if tracking_type != TRACKING_AS_NEEDED:
        entities.append(PillSteadyStateSensor(entry, coordinator))
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
