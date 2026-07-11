from types import MappingProxyType

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    ALCOHOL_DEFAULT_LIMIT_G,
    CAFFEINE_DEFAULT_LIMIT_MG,
    CURRENT_VERSION,
    DEVICE_CATEGORY_DRINK_SETTINGS,
    DEVICE_CATEGORY_DRINKS,
    DEVICE_CATEGORY_MEDICINE,
    DOMAIN,
    DRINK_MASTER_STORE_KEYS,
    GLOBAL_PK_DEFAULTS,
    LOGGER,
    RELEASE_INSTANT,
    STANDARD_EFFECTIVENESS_METRICS,
    TRACKING_AS_NEEDED,
)
from .coordinator import AxDoseLoggerCoordinator
from .data import AxDoseLoggerConfigEntry
from .drink_coordinator import DrinkCoordinator, DrinkMasterCoordinator
from .services import async_setup_services, async_unload_services
from .store import AxDoseLoggerStore
from .views import AxDoseLoggerHistoryView, AxDoseLoggerPredictLowView

PLATFORMS = ["sensor", "button", "number", "calendar"]

# Options whose changes require entity add/remove (and thus a reload).
# All other options (PK params, dose_time, pill_limit, etc.) are read
# fresh by the coordinator and sensors on every update cycle, so they
# don't need a reload.
_STRUCTURAL_KEYS = ("enable_calendar", "enable_adherence", "tracking_type", "tracked_symptoms")

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

# Stable unique_id for the Drink Settings singleton entry.
_DRINK_SETTINGS_UNIQUE_ID = "drink_settings"


def _get_structural_options(entry: AxDoseLoggerConfigEntry) -> dict:
    """
    Return a snapshot of the structural options that affect entity creation.

    Each key is resolved from ``entry.options`` with a fallback to
    ``entry.data`` (matching the pattern used in sensor.py / calendar.py).
    """
    return {
        "enable_calendar": entry.options.get("enable_calendar", entry.data.get("enable_calendar", True)),
        "enable_adherence": entry.options.get("enable_adherence", entry.data.get("enable_adherence", True)),
        "tracking_type": entry.data.get("tracking_type"),
        "tracked_symptoms": entry.options.get("tracked_symptoms", entry.data.get("tracked_symptoms", [])),
    }


def _remove_entity(ent_reg: er.EntityRegistry, platform: str, unique_id: str) -> None:
    """
    Remove an entity from the registry if it exists.

    Prevents ghost "unavailable" entities after a feature is disabled.
    """
    entity_id = ent_reg.async_get_entity_id(platform, DOMAIN, unique_id)
    if entity_id:
        ent_reg.async_remove(entity_id)


async def _ensure_drink_settings_entry(hass: HomeAssistant) -> None:
    """Programmatically create the Drink Settings singleton entry if absent.

    Uses ``async_add(ConfigEntry(...))`` with the ``GLOBAL_PK_DEFAULTS``
    defaults so the master coordinators are set up synchronously (awaited)
    before the calling drink device's ``async_setup_entry`` continues.

    This bypasses the config-flow UI (no form shown) — the user can later
    edit the global constants via the options flow (Configure button).

    Idempotent: if a Drink Settings entry already exists (any state), this
    is a no-op.  The ``unique_id="drink_settings"`` singleton guard in
    ``async_step_drink_settings`` also prevents duplicate manual creation.
    """
    # Check whether a Drink Settings entry already exists.
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get("device_category") == DEVICE_CATEGORY_DRINK_SETTINGS:
            return

    settings_entry = ConfigEntry(
        data={
            "device_category": DEVICE_CATEGORY_DRINK_SETTINGS,
            **GLOBAL_PK_DEFAULTS,
            "caffeine_daily_limit_mg": CAFFEINE_DEFAULT_LIMIT_MG,
            "alcohol_daily_limit_g": ALCOHOL_DEFAULT_LIMIT_G,
        },
        discovery_keys=MappingProxyType({}),
        domain=DOMAIN,
        minor_version=1,
        options={},
        source="user",
        subentries_data=None,
        title="Drink Settings",
        unique_id=_DRINK_SETTINGS_UNIQUE_ID,
        version=CURRENT_VERSION,
    )
    # async_add awaits async_setup -> async_setup_entry -> _setup_drink_masters,
    # so the master coordinators exist in hass.data before this returns.
    await hass.config_entries.async_add(settings_entry)


def _get_drink_masters(hass: HomeAssistant) -> dict[str, DrinkMasterCoordinator]:
    """Return the master coordinators dict (lazily-initialized in hass.data)."""
    return hass.data.setdefault(DOMAIN, {}).setdefault("_drink_masters", {})


async def _setup_drink_masters(hass: HomeAssistant, settings_entry: AxDoseLoggerConfigEntry) -> None:
    """Create/refresh the two DrinkMasterCoordinator instances for the Drink Settings entry.

    Loads each substance's aggregated dose history + body mass from the store,
    refreshes the global PK constants from the settings entry, and starts the
    1-min refresh timers.  Called on Drink Settings entry setup AND on reload
    (so options-flow changes to the global constants propagate).
    """
    store: AxDoseLoggerStore = hass.data[DOMAIN]["_store"]
    masters = _get_drink_masters(hass)

    for substance, store_key in DRINK_MASTER_STORE_KEYS.items():
        await store.async_load_drink_master(substance, store_key)
        if substance in masters:
            # Existing coordinator — refresh constants + first refresh.
            masters[substance].update_global_constants(settings_entry)
            await masters[substance].async_config_entry_first_refresh()
        else:
            master = DrinkMasterCoordinator(hass, substance, store, store_key, settings_entry)
            master.update_global_constants(settings_entry)
            masters[substance] = master
            await master.async_config_entry_first_refresh()


async def async_migrate_entry(hass: HomeAssistant, config_entry: AxDoseLoggerConfigEntry) -> bool:
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

    if config_entry.version <= 9:
        # Version 10: Convert metric_* booleans → tracked_symptoms list
        tracked: list[str] = []
        for key in STANDARD_EFFECTIVENESS_METRICS:
            if new_data.get(f"metric_{key}") or new_options.get(f"metric_{key}"):
                tracked.append(key)
        new_data["tracked_symptoms"] = tracked
        new_options["tracked_symptoms"] = tracked
        # Remove old boolean keys
        for key in STANDARD_EFFECTIVENESS_METRICS:
            new_data.pop(f"metric_{key}", None)
            new_options.pop(f"metric_{key}", None)

    if config_entry.version <= 10:
        # Version 11: Daily-locked effectiveness metrics.
        # No config entry data shape change — metric values are stored in
        # a separate storage key (ax_dose_logger_metrics), not in config
        # entry data/options.  This bump exists so HA knows the entry has
        # been processed by the new code.
        pass

    if config_entry.version <= 11:
        # Version 12: Drinks category router.  All pre-existing entries are
        # medicine entries (drinks are new).  Inject the category so the
        # router logic has a stable key for every entry.
        new_data.setdefault("device_category", DEVICE_CATEGORY_MEDICINE)

    if config_entry.version <= 12:
        # Version 13: Normalize strength_unit "mcg" → "μg" (HA canonical
        # UnitOfMass.MICROGRAMS). The v9 migration converted the legacy
        # micro-sign "µg" (U+00B5) into "mcg", but "mcg" is NOT in
        # set(UnitOfMass), so SensorDeviceClass.WEIGHT sensors
        # (PillStrengthSensor, PillDailyAmountSensor) emitted a validation
        # warning on every state write. Convert any stored "mcg" (and the
        # legacy "µg" micro-sign that v9 may have missed for entries that
        # skipped v9) to the canonical "μg" (Greek mu U+03BC + g) in both
        # entry.data and entry.options.
        for unit_store in (new_data, new_options):
            current_unit = unit_store.get("strength_unit")
            if current_unit in ("mcg", "µg"):
                unit_store["strength_unit"] = "μg"

    if config_entry.version <= 13:
        # Version 14: Remove the Master Tracker "Est. days left" aggregate
        # sensor (DrinkMasterDaysLeftSensor). The Master Tracker has no
        # single inventory of its own — summing every granular drink's stock
        # is misleading on the aggregate device. The per-granular-drink
        # DrinkDaysLeftSensor remains (it powers the Inventory panel's
        # per-drink "Est. days left" 2nd line). Remove the two master
        # entities from the registry so they don't linger as "unavailable".
        # Only the Drink Settings singleton owns these sensors.
        if new_data.get("device_category") == DEVICE_CATEGORY_DRINK_SETTINGS:
            ent_reg = er.async_get(hass)
            _remove_entity(ent_reg, "sensor", "drink_master_days_left_caffeine")
            _remove_entity(ent_reg, "sensor", "drink_master_days_left_alcohol")

    hass.config_entries.async_update_entry(config_entry, data=new_data, options=new_options, version=CURRENT_VERSION)

    LOGGER.info(
        "Migration to version %s successful for %s",
        CURRENT_VERSION,
        config_entry.title,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: AxDoseLoggerConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # Initialize shared store (singleton) with a load-once barrier.
    # Two races must both be guarded:
    #   1. INSTANCE race — concurrent entries must share ONE AxDoseLoggerStore.
    #      Guarded by reserving the slot synchronously before any `await`
    #      (the prior fix): the first entry publishes the store object
    #      immediately so no concurrent entry creates a second instance.
    #   2. LOAD race — concurrent entries must not read `_data` before the
    #      disk load completes.  Reserving the slot alone is NOT enough:
    #      a concurrent entry that arrives while entry #1 is still
    #      awaiting `store.async_load()` sees the slot populated, SKIPS the
    #      load block entirely, and reads an empty `_data`.  Its coordinator's
    #      `_async_setup` (which runs once during first refresh) then bakes
    #      `dose_history = []` into `self.data` and never re-reads — so
    #      every derived sensor (total, last dose, daily amount, averages)
    #      resets to 0/unknown after restart for THAT entry.
    #      "Pills left" survived because `PillStockNumber` restores from the
    #      recorder via `RestoreNumber`, NOT from this store — the smoking
    #      gun that the store (not persistence) was the failing data source.
    # Guard: schedule `async_load` as a SHARED task published synchronously,
    # and have EVERY entry `await` that same task.  The creator and all
    # concurrent siblings resume together once the disk read finishes, so
    # `_data` is guaranteed populated before any coordinator reads it.
    # Awaiting an already-completed task is cheap for late-arriving entries.
    if "_store" not in hass.data[DOMAIN]:
        store = AxDoseLoggerStore(hass)
        hass.data[DOMAIN]["_store"] = store  # reserve instance BEFORE await
        hass.data[DOMAIN]["_store_load"] = hass.async_create_task(store.async_load())
    await hass.data[DOMAIN]["_store_load"]

    # Register REST views (idempotent — HA ignores duplicate registrations)
    hass.http.register_view(AxDoseLoggerHistoryView())
    hass.http.register_view(AxDoseLoggerPredictLowView())

    device_category = entry.data.get("device_category", DEVICE_CATEGORY_MEDICINE)

    if device_category == DEVICE_CATEGORY_DRINK_SETTINGS:
        # Drink Settings singleton — creates the two Master Tracker
        # coordinators (caffeine/alcohol).  Forwards to the sensor platform
        # which instantiates the master PK sensor entities.
        await _setup_drink_masters(hass, entry)
        hass.data[DOMAIN][entry.entry_id] = {
            "entry_data": entry.data,
            "settings_entry_id": entry.entry_id,
        }
        async_setup_services(hass)
        entry.async_on_unload(entry.add_update_listener(async_reload_entry))
        await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
        return True

    if device_category == DEVICE_CATEGORY_DRINKS:
        # Granular drink entry — ensure the Drink Settings singleton exists
        # so the master coordinators are available to receive forwarded doses.
        await _ensure_drink_settings_entry(hass)
        store: AxDoseLoggerStore = hass.data[DOMAIN]["_store"]
        masters = _get_drink_masters(hass)
        coordinator = DrinkCoordinator(hass, entry, store, masters)
        await coordinator.async_config_entry_first_refresh()
        hass.data[DOMAIN][entry.entry_id] = {
            "entry_data": entry.data,
            "coordinator": coordinator,
        }
        async_setup_services(hass)
        entry.async_on_unload(entry.add_update_listener(async_reload_entry))
        await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "button", "number"])
        return True

    # --- Medicine (legacy) ---
    store: AxDoseLoggerStore = hass.data[DOMAIN]["_store"]
    coordinator = AxDoseLoggerCoordinator(hass, entry, store)
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


async def async_reload_entry(hass: HomeAssistant, entry: AxDoseLoggerConfigEntry) -> None:
    """
    Reload config entry, but only when structural options change.

    Compares ``enable_calendar``, ``enable_adherence``, and ``tracking_type``
    before/after.  If none changed (e.g. a PK-only save), the coordinator and
    sensors already read the new values on their next update cycle, so no
    reload or entity-registry surgery is needed.

    When a structural option *did* change, removes entities for newly-disabled
    features to prevent ghost "unavailable" entities, then reloads the entry.

    For the Drink Settings entry, a reload refreshes the master coordinators'
    global PK constants (no entity-registry surgery needed).
    """
    device_category = entry.data.get("device_category", DEVICE_CATEGORY_MEDICINE)

    if device_category == DEVICE_CATEGORY_DRINK_SETTINGS:
        # Refresh master coordinator constants + restart their refresh timers.
        await _setup_drink_masters(hass, entry)
        return

    if device_category == DEVICE_CATEGORY_DRINKS:
        # Granular drink entries only have mutable cooldown/dose_strength/
        # drinking_duration — no structural entity changes.  Coordinator
        # reads the new options on its next update cycle.
        return

    # --- Medicine ---
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

    # --- tracking_type changed ---
    if "tracking_type" in changed and curr["tracking_type"] == TRACKING_AS_NEEDED:
        _remove_entity(ent_reg, "sensor", f"{entry.entry_id}_steady_state")
        _remove_entity(ent_reg, "sensor", f"{entry.entry_id}_overdue")
        _remove_entity(ent_reg, "calendar", f"{entry.entry_id}_calendar")
        for window in (7, 14, 30, 365):
            _remove_entity(ent_reg, "sensor", f"{entry.entry_id}_adherence_{window}")
        for suffix in ("_reset_adherence", "_cover_last_missed"):
            _remove_entity(ent_reg, "button", f"{entry.entry_id}{suffix}")

    # --- tracked_symptoms: metric removed ---
    if "tracked_symptoms" in changed:
        prev_tracked = set(prev.get("tracked_symptoms", []))
        curr_tracked = set(curr.get("tracked_symptoms", []))
        for key in prev_tracked - curr_tracked:
            _remove_entity(ent_reg, "number", f"{entry.entry_id}_eff_{key}")

    # Update the snapshot so the next options save has a fresh baseline
    entry_data["prev_structural"] = curr

    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AxDoseLoggerConfigEntry) -> bool:
    device_category = entry.data.get("device_category", DEVICE_CATEGORY_MEDICINE)
    # Drink Settings only forwards to sensor; drinks forward to sensor+button.
    if device_category == DEVICE_CATEGORY_DRINK_SETTINGS:
        platforms = ["sensor"]
    elif device_category == DEVICE_CATEGORY_DRINKS:
        platforms = ["sensor", "button", "number"]
    else:
        platforms = PLATFORMS

    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Remove services when the last medicine coordinator is gone.
        if not any(isinstance(v, dict) and "coordinator" in v for v in hass.data.get(DOMAIN, {}).values()):
            async_unload_services(hass)
    return unload_ok
