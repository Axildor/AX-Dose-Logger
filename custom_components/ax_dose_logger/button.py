import homeassistant.util.dt as dt_util
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_CATEGORY_DRINKS, DOMAIN, TRACKING_AS_NEEDED
from .coordinator import AxDoseLoggerCoordinator
from .data import AxDoseLoggerConfigEntry
from .drink_coordinator import DrinkCoordinator
from .entity import AxDoseLoggerEntity


def _get_coordinator(hass: HomeAssistant, entry_id: str) -> AxDoseLoggerCoordinator:
    """Retrieve the coordinator for this config entry from hass.data."""
    return hass.data[DOMAIN][entry_id]["coordinator"]


def _get_drink_coordinator(hass: HomeAssistant, entry_id: str) -> DrinkCoordinator:
    """Retrieve the granular drink coordinator for this config entry."""
    return hass.data[DOMAIN][entry_id]["coordinator"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AxDoseLoggerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    category = entry.data.get("device_category")
    if category == DEVICE_CATEGORY_DRINKS:
        coordinator = _get_drink_coordinator(hass, entry.entry_id)
        async_add_entities(
            [
                DrinkLogButton(entry, coordinator),
                DrinkResetButton(entry, coordinator),
                DrinkUndoButton(entry, coordinator),
            ]
        )
        return

    # --- Medicine (legacy) ---
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


# =====================================================================
# Medicine buttons (legacy — unchanged)
# =====================================================================
class PillTakeButton(AxDoseLoggerEntity, ButtonEntity):
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


class PillResetButton(AxDoseLoggerEntity, ButtonEntity):
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


class PillUndoButton(AxDoseLoggerEntity, ButtonEntity):
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


class PillAdherenceResetButton(AxDoseLoggerEntity, ButtonEntity):
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


class PillAdherenceCoverButton(AxDoseLoggerEntity, ButtonEntity):
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


# =====================================================================
# Drink buttons (granular drink devices)
# =====================================================================
class DrinkLogButton(AxDoseLoggerEntity, ButtonEntity):
    """Button that logs a drink.

    The cooldown lockout is NOT enforced here. It is exposed to the frontend
    via the DrinkCooldownSensor (mirrors the medicine pill_limit pattern).
    The card reads that sensor to soft-disable the Log button and show a
    warning with Last/Next countdown, but the user can always override by
    pressing anyway.  On press the coordinator updates local stats AND
    forwards the dose_strength + drinking_duration to the matching Master
    Tracker coordinator for global PK computation.
    """

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "log_drink"
        self._attr_unique_id = f"{entry.entry_id}_log_drink"
        self._attr_icon = "mdi:cup-water"
        # Frontend contract: lets the card group granular drinks by substance
        # for the Master Tracker Log Drink popup + Inventory panel. `role`
        # lets the frontend classify this button without entity_id-suffix
        # matching (entity_id is slugify(translated_name), not the unique_id
        # stem; "Log Drink" → log_drink happens to match, but undo/reset do
        # not — role makes all three robust).
        self._attr_extra_state_attributes = {
            "substance": entry.data.get("drink_type"),
            "device_type": "drink",
            "role": "log",
        }

    async def async_press(self):
        """Log a drink. Cooldown is card-enforced (override always allowed)."""
        coordinator = _get_drink_coordinator(self.hass, self._entry_id)
        await coordinator.async_log_drink(dt_util.now())


class DrinkResetButton(AxDoseLoggerEntity, ButtonEntity):
    """Button that clears a granular drink's local history and notifies the master."""

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "reset_history"
        self._attr_unique_id = f"{entry.entry_id}_reset"
        self._attr_icon = "mdi:history"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_extra_state_attributes = {
            "substance": entry.data.get("drink_type"),
            "device_type": "drink",
            "role": "reset",
        }

    async def async_press(self):
        coordinator = _get_drink_coordinator(self.hass, self._entry_id)
        await coordinator.async_reset()


class DrinkUndoButton(AxDoseLoggerEntity, ButtonEntity):
    """Button that reverts the most recent drink of this granular device."""

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "undo_drink"
        self._attr_unique_id = f"{entry.entry_id}_undo"
        self._attr_icon = "mdi:undo"
        self._attr_extra_state_attributes = {
            "substance": entry.data.get("drink_type"),
            "device_type": "drink",
            "role": "undo",
        }

    async def async_press(self):
        coordinator = _get_drink_coordinator(self.hass, self._entry_id)
        await coordinator.async_undo_drink()
