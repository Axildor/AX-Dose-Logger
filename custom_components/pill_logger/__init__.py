from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CURRENT_VERSION, DOMAIN, LOGGER, RELEASE_INSTANT, TRACKING_AS_NEEDED
from .coordinator import PillLoggerCoordinator
from .data import PillLoggerConfigEntry
from .services import async_setup_services, async_unload_services
from .store import PillLoggerStore
from .views import PillLoggerHistoryView

PLATFORMS = ["sensor", "button", "number", "calendar"]

# Options whose changes require entity add/remove (and thus a reload).
# All other options (PK params, dose_time, pill_limit, etc.) are read
# fresh by the coordinator and sensors on every update cycle, so they
# don't need a reload.
_STRUCTURAL_KEYS = ("enable_calendar", "enable_adherence", "tracking_type")

# Migration mapping for tracking_type (v8 title-case → v9 snake_case)
_TRACKING_TYPE_MIGRATION = {
    "Regular Interval": "regular_interval",
    "Time of Day": "time_of_day",
    "As Needed": "as_needed",
    "Cyclic/Calendar Pattern": "cyclic",
}

# Migration mapping for release_type (v8 title-case → v9 snake_case)
_RELEASE_TYPE_MIGRATION = {
    "Instant Release": "instant_release",
    "Sustained Release": "sustained_release",
}


def _get_structural_options(entry: PillLoggerConfigEntry) -> dict:
    """
    Return a snapshot of the structural options that affect entity creation.

    Each key is resolved from ``entry.options`` with a fallback to
    ``entry.data`` (matching the pattern used in sensor.py / calendar.py).
    """
    return {
        "enable_calendar": entry.options.get(
            "enable_calendar", entry.data.get("enable_calendar", True)
        ),
        "enable_adherence": entry.options.get(
            "enable_adherence", entry.data.get("enable_adherence", True)
        ),
        "tracking_type": entry.data.get("tracking_type"),
    }


def _remove_entity(ent_reg: er.EntityRegistry, platform: str, unique_id: str) -> None:
    """
    Remove an entity from the registry if it exists.

    Prevents ghost "unavailable" entities after a feature is disabled.
    """
    entity_id = ent_reg.async_get_entity_id(platform, DOMAIN, unique_id)
    if entity_id:
        ent_reg.async_remove(entity_id)


async def async_migrate_entry(hass: HomeAssistant, config_entry: PillLoggerConfigEntry) -> bool:
    """Migrate old entry to new version."""
    LOGGER.debug("Migrating from version %s", config_entry.version)

    new_data = {**config_entry.data}
    new_options = {**config_entry.options}

    if config_entry.version == 2:
        # Version 3 added ER pharmacokinetics fields
        new_data.setdefault("release_type", RELEASE_INSTANT)
        new_data.setdefault("bioavailability", 100)
        new_data.setdefault("ir_fraction", 100)
        new_data.setdefault("zero_order_duration", 0)
        new_data.setdefault("release_half_life", 0)
        # Also ensure these exist in options (user may have partially saved)
        new_options.setdefault("bioavailability", 100)
        new_options.setdefault("ir_fraction", 100)
        new_options.setdefault("zero_order_duration", 0)
        new_options.setdefault("release_half_life", 0)

    if config_entry.version <= 3:
        # Version 4 added lag_time
        new_data.setdefault("lag_time", 0)
        new_options.setdefault("lag_time", 0)

    if config_entry.version <= 4:
        # Version 5: Convert time_of_day string to dose_times list
        # Old format: time_of_day = "08:00"
        # New format: dose_times = ["08:00"], doses_per_day = 1
        old_time = new_data.pop("time_of_day", None)
        if old_time:
            new_data["dose_times"] = [old_time]
            new_data["doses_per_day"] = 1
        else:
            new_data.setdefault("dose_times", ["08:00"])
            new_data.setdefault("doses_per_day", 1)

        old_time_opt = new_options.pop("time_of_day", None)
        if old_time_opt:
            new_options["dose_times"] = [old_time_opt]
            new_options["doses_per_day"] = 1
        else:
            new_options.setdefault("dose_times", ["08:00"])
            new_options.setdefault("doses_per_day", 1)

    if config_entry.version <= 5:
        # Version 6: Rename safe_doses → pill_limit
        if "safe_doses" in new_data:
            new_data["pill_limit"] = new_data.pop("safe_doses")
        if "safe_doses" in new_options:
            new_options["pill_limit"] = new_options.pop("safe_doses")

    if config_entry.version <= 6:
        # Version 7: Force calendar and adherence off for As Needed entries
        if new_data.get("tracking_type") == TRACKING_AS_NEEDED:
            new_data["enable_calendar"] = False
            new_data["enable_adherence"] = False
            new_options["enable_calendar"] = False
            new_options["enable_adherence"] = False

    if config_entry.version <= 7:
        # Version 8: Add strength_unit (default mg for existing entries)
        new_data.setdefault("strength_unit", "mg")
        new_options.setdefault("strength_unit", "mg")

    if config_entry.version <= 8:
        # Version 9: Migrate title-case selector values to snake_case
        # tracking_type: "Regular Interval" → "regular_interval", etc.
        old_tracking = new_data.get("tracking_type")
        if old_tracking and old_tracking in _TRACKING_TYPE_MIGRATION:
            new_data["tracking_type"] = _TRACKING_TYPE_MIGRATION[old_tracking]

        # release_type: "Instant Release" → "instant_release", etc.
        old_release = new_data.get("release_type")
        if old_release and old_release in _RELEASE_TYPE_MIGRATION:
            new_data["release_type"] = _RELEASE_TYPE_MIGRATION[old_release]

        # strength_unit: "µg" → "mcg" (mg and g unchanged)
        if new_data.get("strength_unit") == "µg":
            new_data["strength_unit"] = "mcg"
        if new_options.get("strength_unit") == "µg":
            new_options["strength_unit"] = "mcg"

    hass.config_entries.async_update_entry(
        config_entry, data=new_data, options=new_options, version=CURRENT_VERSION
    )

    LOGGER.info(
        "Migration to version %s successful for %s",
        CURRENT_VERSION, config_entry.title,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: PillLoggerConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # Initialize shared store (singleton)
    if "_store" not in hass.data[DOMAIN]:
        store = PillLoggerStore(hass)
        await store.async_load()
        hass.data[DOMAIN]["_store"] = store

    # Register REST view (idempotent — HA ignores duplicate registrations)
    hass.http.register_view(PillLoggerHistoryView())

    # Create per-entry coordinator — single source of truth for dose history
    store: PillLoggerStore = hass.data[DOMAIN]["_store"]
    coordinator = PillLoggerCoordinator(hass, entry, store)
    hass.data[DOMAIN][entry.entry_id] = {
        "entry_data": entry.data,
        "coordinator": coordinator,
        # Snapshot of structural options for change detection in async_reload_entry.
        # Only enable_calendar, enable_adherence, and tracking_type affect which
        # entities are created; all other options are read fresh by the coordinator
        # and sensors on every update cycle, so they don't need a reload.
        "prev_structural": _get_structural_options(entry),
    }

    # First refresh loads dose history from the store
    await coordinator.async_config_entry_first_refresh()

    # Register domain-level services (idempotent — skips if already registered)
    async_setup_services(hass)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_reload_entry(hass: HomeAssistant, entry: PillLoggerConfigEntry) -> None:
    """
    Reload config entry, but only when structural options change.

    Compares ``enable_calendar``, ``enable_adherence``, and ``tracking_type``
    before/after.  If none changed (e.g. a PK-only save), the coordinator and
    sensors already read the new values on their next update cycle, so no
    reload or entity-registry surgery is needed.

    When a structural option *did* change, removes entities for newly-disabled
    features to prevent ghost "unavailable" entities, then reloads the entry.
    """
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    prev = entry_data.get("prev_structural", {})
    curr = _get_structural_options(entry)

    # Detect which structural keys changed
    changed = {k for k in _STRUCTURAL_KEYS if prev.get(k) != curr.get(k)}

    if not changed:
        # No structural change — coordinator and sensors will pick up the
        # new option values on their next update cycle.  Skip reload entirely.
        return

    ent_reg = er.async_get(hass)

    # --- enable_calendar: True → False ---
    if "enable_calendar" in changed and not curr["enable_calendar"]:
        _remove_entity(ent_reg, "calendar", f"{entry.entry_id}_calendar")

    # --- enable_adherence: True → False ---
    if "enable_adherence" in changed and not curr["enable_adherence"]:
        # Remove adherence sensors (7, 14, 30, 365-day windows)
        for window in (7, 14, 30, 365):
            _remove_entity(ent_reg, "sensor", f"{entry.entry_id}_adherence_{window}")
        # Remove adherence tool buttons
        for suffix in ("_reset_adherence", "_cover_last_missed"):
            _remove_entity(ent_reg, "button", f"{entry.entry_id}{suffix}")

    # --- tracking_type: * → "as_needed" ---
    # tracking_type is immutable via the options flow (set during initial
    # config flow), so this branch is effectively dead code.  It's kept for
    # safety in case a future reconfigure step allows changing tracking_type.
    if "tracking_type" in changed and curr["tracking_type"] == TRACKING_AS_NEEDED:
        _remove_entity(ent_reg, "sensor", f"{entry.entry_id}_steady_state")
        _remove_entity(ent_reg, "calendar", f"{entry.entry_id}_calendar")
        for window in (7, 14, 30, 365):
            _remove_entity(ent_reg, "sensor", f"{entry.entry_id}_adherence_{window}")
        for suffix in ("_reset_adherence", "_cover_last_missed"):
            _remove_entity(ent_reg, "button", f"{entry.entry_id}{suffix}")

    # Update the snapshot so the next options save has a fresh baseline
    entry_data["prev_structural"] = curr

    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: PillLoggerConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        # Remove services when the last entry is unloaded
        if not any(
            isinstance(v, dict) and "coordinator" in v
            for v in hass.data.get(DOMAIN, {}).values()
        ):
            async_unload_services(hass)
    return unload_ok
