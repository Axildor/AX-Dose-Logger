from datetime import timedelta
from homeassistant.core import HomeAssistant
from .sensors.total import PillTotalSensor
from .sensors.last_dose import PillLastDoseSensor
from .sensors.safe_doses import PillSafeDosesSensor
from .sensors.concentration import PillConcentrationSensor
from .sensors.next_dose import PillNextDoseSensor
from .sensors.avg_doses import PillAvgDosesSensor
from .sensors.steady_state import PillSteadyStateSensor
from .sensors.strength import PillStrengthSensor

SCAN_INTERVAL = timedelta(minutes=2)

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    med_name = entry.data["medication_name"]
    entities = [PillTotalSensor(med_name, entry.entry_id)]
    entities.append(PillLastDoseSensor(med_name, entry.entry_id))
    entities.append(PillSafeDosesSensor(entry))
    entities.append(PillConcentrationSensor(entry))
    entities.append(PillNextDoseSensor(entry))
    entities.append(PillAvgDosesSensor(entry, 7, "Avg Daily Doses (7 Days)"))
    entities.append(PillAvgDosesSensor(entry, 30, "Avg Daily Doses (30 Days)"))
    entities.append(PillAvgDosesSensor(entry, 365, "Avg Daily Doses (Yearly)"))
    entities.append(PillSteadyStateSensor(entry))
    entities.append(PillStrengthSensor(entry))
    async_add_entities(entities)
