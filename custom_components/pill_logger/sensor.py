from homeassistant.core import HomeAssistant
from .sensors.total import PillTotalSensor
from .sensors.last_dose import PillLastDoseSensor
from .sensors.pill_limit import PillLimitSensor
from .sensors.concentration import PillConcentrationSensor
from .sensors.next_dose import PillNextDoseSensor
from .sensors.avg_doses import PillAvgDosesSensor
from .sensors.steady_state import PillSteadyStateSensor
from .sensors.strength import PillStrengthSensor
from .sensors.adherence import PillAdherenceSensor

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    med_name = entry.data["medication_name"]
    tracking_type = entry.data.get("tracking_type")
    entities = [PillTotalSensor(med_name, entry.entry_id)]
    entities.append(PillLastDoseSensor(med_name, entry.entry_id))
    entities.append(PillLimitSensor(entry))
    entities.append(PillConcentrationSensor(entry))
    entities.append(PillNextDoseSensor(entry))
    entities.append(PillAvgDosesSensor(entry, 7, "Avg Daily Doses (7 Days)"))
    entities.append(PillAvgDosesSensor(entry, 14, "Avg Daily Doses (14 Days)"))
    entities.append(PillAvgDosesSensor(entry, 30, "Avg Daily Doses (30 Days)"))
    entities.append(PillAvgDosesSensor(entry, 365, "Avg Daily Doses (Yearly)"))
    # Steady state is only meaningful for scheduled medications (requires a fixed dosing interval τ)
    if tracking_type != "As Needed":
        entities.append(PillSteadyStateSensor(entry))
    entities.append(PillStrengthSensor(entry))
    enable_adherence = entry.options.get(
        "enable_adherence", entry.data.get("enable_adherence", tracking_type != "As Needed")
    )
    if enable_adherence:
        entities.append(PillAdherenceSensor(entry, 7, "Adherence (7 Days)"))
        entities.append(PillAdherenceSensor(entry, 14, "Adherence (14 Days)"))
        entities.append(PillAdherenceSensor(entry, 30, "Adherence (30 Days)"))
        entities.append(PillAdherenceSensor(entry, 365, "Adherence (Yearly)"))
    async_add_entities(entities)
