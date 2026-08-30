import homeassistant.util.dt as dt_util
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEVICE_CATEGORY_DRINKS,
    DEVICE_CATEGORY_DRINK_SETTINGS,
    DOMAIN,
    TRACKING_AS_NEEDED,
)
from .data import AxDoseLoggerConfigEntry
from .drink_coordinator import DrinkCoordinator, DrinkMasterCoordinator
from .entity import AxDoseLoggerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AxDoseLoggerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    category = entry.data.get("device_category")
    if category == DEVICE_CATEGORY_DRINKS:
        coordinator: DrinkCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        async_add_entities(
            [
                DrinkLogButton(entry, coordinator),
                DrinkResetButton(entry, coordinator),
                DrinkUndoButton(entry, coordinator),
            ]
        )
        return

    # --- Drink Settings (Master Tracker host) ---
    if category == DEVICE_CATEGORY_DRINK_SETTINGS:
        # Master Tracker devices get one Averages Reset button per substance
        # (caffeine / alcohol), each bound to that substance's aggregate
        # DrinkMasterCoordinator.  Created here (not in sensor.py) because
        # buttons live on the button platform.
        from .const import DEFAULT_PROFILE_ID

        masters: dict[tuple[str, str], DrinkMasterCoordinator] = hass.data[DOMAIN].get("_drink_masters", {})
        if entry.unique_id == "drink_settings":
            profile_id = DEFAULT_PROFILE_ID
        else:
            profile_id = entry.data.get("profile_id", DEFAULT_PROFILE_ID)
        profile_name = entry.data.get("profile_name")
        master_entities = []
        for substance in ("caffeine", "alcohol"):
            master = masters.get((profile_id, substance))
            if master is None:
                continue
            master_entities.append(
                DrinkMasterAveragesResetButton(entry, master, profile_id, profile_name)
            )
        if master_entities:
            async_add_entities(master_entities)
        return

    # --- Medicine (legacy) ---
    tracking_type = entry.data.get("tracking_type")
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = [
        PillTakeButton(entry, coordinator),
        PillResetButton(entry, coordinator),
        PillUndoButton(entry, coordinator),
        PillAveragesResetButton(entry, coordinator),
    ]
    # Adherence + Skip tools are only meaningful for scheduled medications.
    # As Needed (PRN) devices have no schedule → no overdue alarm → no
    # adherence sensors and no skip semantics. The buttons would be dead
    # entities, so skip them.
    if tracking_type != TRACKING_AS_NEEDED:
        entities.append(PillAdherenceResetButton(entry, coordinator))
        entities.append(PillAdherenceCoverButton(entry, coordinator))
        entities.append(PillSkipDoseButton(entry, coordinator))
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
        await self.coordinator.async_take_dose(now)


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
        await self.coordinator.async_reset()


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
        await self.coordinator.async_undo_dose()


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
        await self.coordinator.async_adherence_reset()


class PillAdherenceCoverButton(AxDoseLoggerEntity, ButtonEntity):
    """Button entity that marks the most recent missed dose slot as taken for adherence only."""

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "cover_last_missed"
        self._attr_unique_id = f"{entry.entry_id}_cover_last_missed"
        self._attr_icon = "mdi:check-underline-circle"
        self._attr_entity_category = EntityCategory.CONFIG
        # Frontend contract: the friendly name ("Mark Last Adherence
        # Taken") slugifies to ``_mark_last_adherence_taken``, NOT
        # ``_cover_last_missed`` (the translation_key). Suffix-matching on
        # the entity_id therefore fails to resolve this button and it was
        # missing from the card Tools pane. The ``role`` attribute lets the
        # frontend classify it robustly regardless of slugification — the
        # same pattern the drink buttons already use.
        self._attr_extra_state_attributes = {"role": "cover"}

    async def async_press(self):
        """When pressed, cover the most recent missed dose slot via the coordinator."""
        await self.coordinator.async_adherence_override()


class PillSkipDoseButton(AxDoseLoggerEntity, ButtonEntity):
    """Button entity that skips the most recent missed scheduled dose slot.

    Clears the overdue alarm and advances next_dose WITHOUT logging a
    dose — PK (Amount in Body), stock (Pills Left / Days Left), Total
    Doses, and Last Dose are all untouched. Adherence stays penalized: a
    skip is not adherence credit. A patient on a prescriber-directed
    skip presses both this button AND Mark Last Adherence Taken.
    """

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "skip_dose"
        self._attr_unique_id = f"{entry.entry_id}_skip_dose"
        self._attr_icon = "mdi:skip-next"
        self._attr_entity_category = EntityCategory.CONFIG
        # Frontend contract: lets the card resolve this button by role
        # rather than entity_id suffix (the friendly name "Skip Dose"
        # slugifies to ``_skip_dose`` which happens to match, but role is
        # the robust pattern — see PillAdherenceCoverButton for the bug
        # suffix-matching caused).
        self._attr_extra_state_attributes = {"role": "skip"}

    async def async_press(self):
        """When pressed, skip the current missed dose slot via the coordinator."""
        await self.coordinator.async_skip_dose()


class PillAveragesResetButton(AxDoseLoggerEntity, ButtonEntity):
    """Button entity that resets the rolling averages only (no history impact).

    Sets a persisted reset anchor so the 7/14/30/365-day average sensors
    stop counting pre-reset doses.  Total Doses, Amount in Body (PK),
    stock, and Adherence % are untouched — no dose data is deleted.
    """

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "reset_averages"
        self._attr_unique_id = f"{entry.entry_id}_reset_averages"
        self._attr_icon = "mdi:chart-bell-curve-remove"
        self._attr_entity_category = EntityCategory.CONFIG
        # Frontend contract: lets the card resolve this button by role
        # rather than entity_id suffix (the robust pattern — see
        # PillAdherenceCoverButton for the bug suffix-matching caused).
        self._attr_extra_state_attributes = {"role": "averages_reset"}

    async def async_press(self):
        """When pressed, reset the rolling averages via the coordinator."""
        await self.coordinator.async_averages_reset()


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

    def __init__(self, entry, coordinator: DrinkCoordinator):
        super().__init__(entry, coordinator)
        # Store the typed coordinator so async_press can call DrinkCoordinator-
        # specific methods (async_log_drink) without re-fetching from hass.data.
        # self.coordinator (from CoordinatorEntity) is typed as the base
        # AxDoseLoggerCoordinator, so the subtype is kept here for type safety.
        self._drink_coordinator: DrinkCoordinator = coordinator
        self._attr_translation_key = "log_drink"
        self._attr_unique_id = f"{entry.entry_id}_log_drink"
        self._attr_icon = "mdi:cup-water"
        # Frontend contract: lets the card group granular drinks by substance
        # for the Master Tracker Log Drink popup + Inventory panel. `role`
        # lets the frontend classify this button without entity_id-suffix
        # matching (entity_id is slugify(translated_name), not the unique_id
        # stem; "Log Drink" → log_drink happens to match, but undo/reset do
        # not — role makes all three robust).
        # M2M topology: expose allowed_profiles + shared_drink so the
        # frontend card can auto-populate its profile picker (multi-select
        # read from the config entry data/options) and decide whether to
        # show the "Who is logging this?" popup (shared_drink flag).
        self._attr_extra_state_attributes = {
            "substance": entry.data.get("drink_type"),
            "device_type": "drink",
            "role": "log",
            "allowed_profiles": entry.data.get("allowed_profiles", ["default"]),
            "shared_drink": entry.options.get("shared_drink", entry.data.get("shared_drink", False)),
        }

    async def async_press(self):
        """Log a drink. Cooldown is card-enforced (override always allowed).

        M2M button-press routing: the button is a stateless HA trigger and
        cannot carry a per-press target_profile.  When the drink has
        exactly one allowed profile, the convenience default routes to
        it (single-user / single-profile case).  When the drink has
        multiple allowed profiles, the button CANNOT disambiguate, so it
        raises -- shared drinks must be logged via the frontend card
        (which calls the log_drink service with target_profile).  A drink
        with zero allowed profiles logs to inventory only (no PK routing).
        """
        await self._drink_coordinator.async_log_drink(dt_util.now())


class DrinkResetButton(AxDoseLoggerEntity, ButtonEntity):
    """Button that clears a granular drink's local history and notifies the master."""

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator: DrinkCoordinator):
        super().__init__(entry, coordinator)
        self._drink_coordinator: DrinkCoordinator = coordinator
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
        await self._drink_coordinator.async_reset()


class DrinkUndoButton(AxDoseLoggerEntity, ButtonEntity):
    """Button that reverts the most recent drink of this granular device."""

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator: DrinkCoordinator):
        super().__init__(entry, coordinator)
        self._drink_coordinator: DrinkCoordinator = coordinator
        self._attr_translation_key = "undo_drink"
        self._attr_unique_id = f"{entry.entry_id}_undo"
        self._attr_icon = "mdi:undo"
        self._attr_extra_state_attributes = {
            "substance": entry.data.get("drink_type"),
            "device_type": "drink",
            "role": "undo",
        }

    async def async_press(self):
        await self._drink_coordinator.async_undo_drink()


class DrinkAveragesResetButton(AxDoseLoggerEntity, ButtonEntity):
    """Button that resets a granular drink's rolling averages only (no history impact)."""

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator: DrinkCoordinator):
        super().__init__(entry, coordinator)
        self._drink_coordinator: DrinkCoordinator = coordinator
        self._attr_translation_key = "reset_averages"
        self._attr_unique_id = f"{entry.entry_id}_reset_averages"
        self._attr_icon = "mdi:chart-bell-curve-remove"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_extra_state_attributes = {
            "substance": entry.data.get("drink_type"),
            "device_type": "drink",
            "role": "averages_reset",
        }

    async def async_press(self):
        await self._drink_coordinator.async_averages_reset()


# =====================================================================
# Master Tracker buttons (Drink Settings entry — per-substance aggregate)
# =====================================================================
class DrinkMasterAveragesResetButton(AxDoseLoggerEntity, ButtonEntity):
    """Button that resets a Master Tracker's aggregate rolling averages.

    Hosted on the virtual Caffeine Tracker / Alcohol Tracker devices (the
    Drink Settings entry).  Resets the (profile, substance) aggregate
    averages across ALL granular drinks of the substance without touching
    any drink's history, body mass (PK), or the granular averages.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        entry,
        coordinator: DrinkMasterCoordinator,
        profile_id: str,
        profile_name: str | None,
    ):
        super().__init__(entry, coordinator)
        self._master_coordinator: DrinkMasterCoordinator = coordinator
        self._attr_translation_key = "reset_averages"
        self._attr_unique_id = f"master_{profile_id}_{coordinator.substance}_reset_averages"
        self._attr_icon = "mdi:chart-bell-curve-remove"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_extra_state_attributes = {
            "substance": coordinator.substance,
            "device_type": "drink_master",
            "role": "averages_reset",
            "profile_id": profile_id,
            "profile_name": profile_name,
        }

    async def async_press(self):
        await self._master_coordinator.async_averages_reset()
