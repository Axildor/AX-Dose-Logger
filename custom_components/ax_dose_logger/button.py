import homeassistant.util.dt as dt_util
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, TRACKING_AS_NEEDED
from .coordinator import PillLoggerCoordinator
from .data import PillLoggerConfigEntry
from .entity import PillLoggerEntity


def _get_coordinator(hass: HomeAssistant, entry_id: str) -> PillLoggerCoordinator:
    """Retrieve the coordinator for this config entry from hass.data."""
    return hass.data[DOMAIN][entry_id]["coordinator"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PillLoggerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    tracking_type = entry.data.get("tracking_type")
    coordinator = _get_coordinator(hass, entry.entry_id)
    entities = [
        PillTakeButton(entry, coordinator),
        PillResetButton(entry, coordinator),
        PillUndoButton(entry, coordinator),
    ]
    # Adherence tools are only meaningful for scheduled medications.
    # As Needed (PRN) devices have no adherence sensors, so the buttons
    # would be dead entities — skip them.
    if tracking_type != TRACKING_AS_NEEDED:
        entities.append(PillAdherenceResetButton(entry, coordinator))
        entities.append(PillAdherenceCoverButton(entry, coordinator))
    async_add_entities(entities)

class PillTakeButton(PillLoggerEntity, ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "take"
        self._attr_unique_id = f"{entry.entry_id}_take"
        self._attr_icon = "mdi:pill"

    async def async_press(self):
        """
        When pressed, record a dose via the coordinator.

        The coordinator updates dose history, fires legacy dispatcher
        signals for not-yet-migrated sensors, and triggers an immediate
        refresh of all CoordinatorEntity subscribers.
        """
        now = dt_util.now()
        coordinator = _get_coordinator(self.hass, self._entry_id)
        await coordinator.async_take_dose(now)

class PillResetButton(PillLoggerEntity, ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "reset_history"
        self._attr_unique_id = f"{entry.entry_id}_reset"
        self._attr_icon = "mdi:history"
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self):
        """When pressed, clear all dose history via the coordinator."""
        coordinator = _get_coordinator(self.hass, self._entry_id)
        await coordinator.async_reset()

class PillUndoButton(PillLoggerEntity, ButtonEntity):
    """Button entity that reverts the most recently logged dose."""

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "undo_dose"
        self._attr_unique_id = f"{entry.entry_id}_undo"
        self._attr_icon = "mdi:undo"

    async def async_press(self):
        """When pressed, undo the last dose via the coordinator."""
        coordinator = _get_coordinator(self.hass, self._entry_id)
        await coordinator.async_undo_dose()


class PillAdherenceResetButton(PillLoggerEntity, ButtonEntity):
    """Button entity that clears adherence history only (no PK / dose count impact)."""

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "reset_adherence"
        self._attr_unique_id = f"{entry.entry_id}_reset_adherence"
        self._attr_icon = "mdi:percent-circle-outline"
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self):
        """When pressed, clear adherence state via the coordinator."""
        coordinator = _get_coordinator(self.hass, self._entry_id)
        await coordinator.async_adherence_reset()


class PillAdherenceCoverButton(PillLoggerEntity, ButtonEntity):
    """Button entity that marks the most recent missed dose slot as taken for adherence only."""

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "cover_last_missed"
        self._attr_unique_id = f"{entry.entry_id}_cover_last_missed"
        self._attr_icon = "mdi:check-underline-circle"
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self):
        """When pressed, cover the most recent missed dose slot via the coordinator."""
        coordinator = _get_coordinator(self.hass, self._entry_id)
        await coordinator.async_adherence_override()
