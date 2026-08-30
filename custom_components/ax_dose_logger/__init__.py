import uuid
from types import MappingProxyType

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    ALCOHOL_DEFAULT_LIMIT_G,
    CAFFEINE_DEFAULT_LIMIT_MG,
    CURRENT_VERSION,
    DEFAULT_PROFILE_ID,
    DEVICE_CATEGORY_DRINK_SETTINGS,
    DEVICE_CATEGORY_DRINKS,
    DEVICE_CATEGORY_MEDICINE,
    DOMAIN,
    GLOBAL_PK_DEFAULTS,
    LOGGER,
    RELEASE_INSTANT,
    STANDARD_EFFECTIVENESS_METRICS,
    TRACKING_AS_NEEDED,
    drink_master_store_key,
)
from .coordinator import AxDoseLoggerCoordinator
from .data import AxDoseLoggerConfigEntry
from .drink_coordinator import DrinkCoordinator, DrinkMasterCoordinator
from .services import async_setup_services, async_unload_services
from .store import AxDoseLoggerStore
from .views import (
    AxDoseLoggerGraphView,
    AxDoseLoggerHistoryView,
    AxDoseLoggerPredictLowView,
)

PLATFORMS = ["sensor", "button", "number", "calendar"]

# Options whose changes require entity add/remove (and thus a reload).
# All other options (PK params, dose_time, pill_limit, etc.) are read
# fresh by the coordinator and sensors on every update cycle, so they
# don't need a reload.
# daily_limit is structural because the Pill24hLimitExceededSensor binary
# sensor is only created when daily_limit > 0; toggling between 0 and >0
# must trigger entity recreation.
_STRUCTURAL_KEYS = ("enable_calendar", "enable_adherence", "tracking_type", "tracked_symptoms", "daily_limit")

# Migration mapping for tracking_type (v8 title-case -> v9 snake_case)
_TRACKING_TYPE_MIGRATION = {
    "Regular Interval": "regular_interval",
    "Time of Day": "time_of_day",
    "As Needed": "as_needed",
    "Cyclic/Calendar Pattern": "cyclic",
}

# Migration mapping for release_type (v8 title-case -> v9 snake_case)
_RELEASE_TYPE_MIGRATION = {
    "Instant Release": "instant_release",
    "Sustained Release": "sustained_release",
}

# Stable unique_id for the legacy Drink Settings singleton entry.
# New (named) profiles use ``f"drink_settings_{profile_id}"`` where
# ``profile_id`` is the entry's own HA-managed entry_id (UUID).
_DRINK_SETTINGS_UNIQUE_ID = "drink_settings"


def _get_structural_options(entry: AxDoseLoggerConfigEntry) -> dict:
    """
    Return a snapshot of the structural options that affect entity creation.

    Each key is resolved from ``entry.options`` with a fallback to
    ``entry.data`` (matching the pattern used in sensor.py / calendar.py).

    ``daily_limit`` is cast to ``float`` so that a stored int ``0`` compares
    equal across snapshots (avoids false change-detection), and so a 0 to >0
    toggle is correctly detected as a structural change requiring entity
    recreation of :class:`Pill24hLimitExceededSensor` (created only when
    ``daily_limit > 0`` -- see :func:`_setup_medicine_sensors`).
    """
    return {
        "enable_calendar": entry.options.get("enable_calendar", entry.data.get("enable_calendar", True)),
        "enable_adherence": entry.options.get("enable_adherence", entry.data.get("enable_adherence", True)),
        "tracking_type": entry.data.get("tracking_type"),
        "tracked_symptoms": entry.options.get("tracked_symptoms", entry.data.get("tracked_symptoms", [])),
        "daily_limit": float(entry.options.get("daily_limit", entry.data.get("daily_limit", 0))),
    }


def _remove_entity(ent_reg: er.EntityRegistry, platform: str, unique_id: str) -> None:
    """
    Remove an entity from the registry if it exists.

    Prevents ghost "unavailable" entities after a feature is disabled.
    """
    entity_id = ent_reg.async_get_entity_id(platform, DOMAIN, unique_id)
    if entity_id:
        ent_reg.async_remove(entity_id)


def _profile_id_of(entry: AxDoseLoggerConfigEntry) -> str:
    """Return the immutable profile id for a Drink Settings entry.

    The legacy singleton (``unique_id == "drink_settings"``) uses the reserved
    literal ``DEFAULT_PROFILE_ID`` ("default").  New (named) profiles use the
    entry's own HA-managed ``entry_id`` (UUID), written back into
    ``entry.data["profile_id"]`` at creation.  The defensive ``.get(..., ...)``
    fallback handles pre-migration entries that have no ``profile_id`` key yet.
    """
    if entry.unique_id == _DRINK_SETTINGS_UNIQUE_ID:
        return DEFAULT_PROFILE_ID
    return entry.data.get("profile_id", DEFAULT_PROFILE_ID)


def _profile_name_of(entry: AxDoseLoggerConfigEntry) -> str | None:
    """Return the mutable display name for a Drink Settings entry (or None)."""
    return entry.data.get("profile_name")


async def _ensure_drink_settings_entry(hass: HomeAssistant, profile_name: str | None = None) -> str:
    """Programmatically create a Drink Settings config entry for a profile.

    Behavior:
    * ``profile_name=None`` (default) -> the legacy ``default`` profile.
      Idempotent: if a ``default`` Drink Settings entry already exists, this
      is a no-op and returns ``DEFAULT_PROFILE_ID``.
    * ``profile_name="Alice"`` -> a new named profile.  ``profile_id`` is the
      new entry's own HA-managed ``entry_id`` (UUID, immutable).  Returns that
      UUID so the caller (the drink config flow) can store it in the drink's
      ``allowed_profiles`` array.

    Uses ``async_add(ConfigEntry(...))`` with the ``GLOBAL_PK_DEFAULTS``
    defaults so the master coordinators are set up synchronously (awaited)
    before the calling drink device's ``async_setup_entry`` continues.

    This bypasses the config-flow UI (no form shown) -- the user can later
    edit the per-profile constants via the options flow (Configure button).

    Returns the immutable ``profile_id`` of the (existing or newly-created)
    Drink Settings entry, so the caller can record it.
    """
    # The legacy default profile: idempotent singleton guard.
    if profile_name is None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.data.get("device_category") == DEVICE_CATEGORY_DRINK_SETTINGS:
                return DEFAULT_PROFILE_ID
        settings_entry = ConfigEntry(
            data={
                "device_category": DEVICE_CATEGORY_DRINK_SETTINGS,
                "profile_id": DEFAULT_PROFILE_ID,
                "profile_name": None,
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
        return DEFAULT_PROFILE_ID

    # Named profile: create a new entry.  The profile_id is a pre-generated
    # UUID (not the entry's own entry_id), so it is known at construction time
    # and every downstream consumer (store keys, device ids, sensor
    # unique_ids, _drink_masters routing) keys off it from the first
    # async_setup_entry -- no placeholder, no post-add write-back, no re-key.
    # This avoids the prior "two-phase" creation that (a) built sensors under
    # a transient "__pending__" profile_id (orphaning unique_ids) and (b)
    # re-ran _setup_drink_masters on a LOADED entry (async_config_entry_first_
    # refresh raises ConfigEntryError outside SETUP_IN_PROGRESS).
    profile_id = uuid.uuid4().hex
    title = f"Drink Settings \u2014 {profile_name}"
    settings_entry = ConfigEntry(
        data={
            "device_category": DEVICE_CATEGORY_DRINK_SETTINGS,
            "profile_id": profile_id,
            "profile_name": profile_name,
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
        title=title,
        # unique_id is deliberately separate from profile_id: it only serves
        # HA's config-entry dedup guard.  profile_id is the immutable
        # routing/storage key consumed by store + sensor + coordinator code.
        unique_id=f"drink_settings_named::{profile_name}",
        version=CURRENT_VERSION,
    )
    # async_add awaits async_setup -> async_setup_entry -> _setup_drink_masters
    # (coordinators keyed under profile_id) + async_forward_entry_setups
    # (sensors with profile_id-derived unique_ids).  By the time this returns
    # the master coordinators and sensors all use the real UUID.
    await hass.config_entries.async_add(settings_entry)
    return profile_id


def _get_drink_masters(hass: HomeAssistant) -> dict[tuple[str, str], DrinkMasterCoordinator]:
    """Return the master coordinators dict (lazily-initialized in hass.data).

    Keyed by ``(profile_id, substance)`` -- a 2D map.  The legacy single-user
    install has one key pair: ``("default", "caffeine")`` and
    ``("default", "alcohol")``.
    """
    return hass.data.setdefault(DOMAIN, {}).setdefault("_drink_masters", {})


async def _setup_drink_masters(hass: HomeAssistant, settings_entry: AxDoseLoggerConfigEntry) -> None:
    """Create/refresh the two DrinkMasterCoordinator instances for ONE profile.

    Reads the ``profile_id`` from the Drink Settings entry (immutable UUID for
    named profiles, ``DEFAULT_PROFILE_ID`` for the legacy singleton).  Loads
    each substance's aggregated dose history + body mass from the per-profile
    store file, refreshes the global PK constants from the settings entry,
    and starts the 1-min refresh timers.  Called on Drink Settings entry setup
    AND on reload (so options-flow changes to the per-profile constants
    propagate).  Only the calling entry's profile is touched -- other
    profiles' coordinators are left intact.
    """
    store: AxDoseLoggerStore = hass.data[DOMAIN]["_store"]
    masters = _get_drink_masters(hass)
    profile_id = _profile_id_of(settings_entry)

    # Purge stale coordinators previously keyed under a different profile_id
    # for this same settings entry.  This cleans up any leftover entries from
    # the now-removed "__pending__" two-phase creation path (which created
    # coordinators keyed by "__pending__" and never re-keyed them).  The
    # identity check on ``config_entry`` scopes the purge to this entry only;
    # other profiles' coordinators are untouched.  ``config_entry`` is the
    # ``settings_entry`` passed to DataUpdateCoordinator.__init__ (see
    # drink_coordinator.py), so the reference identity is reliable.
    for key, coord in list(masters.items()):
        if coord.config_entry is settings_entry and key[0] != profile_id:
            masters.pop(key, None)
            LOGGER.info(
                "Purged stale master coordinator keyed under %s for settings "
                "entry %s (real profile_id=%s).",
                key[0],
                settings_entry.entry_id,
                profile_id,
            )

    # ``async_config_entry_first_refresh`` is only valid while the entry is
    # SETUP_IN_PROGRESS (the initial async_setup_entry path); HA raises
    # ConfigEntryError if called in any other state.  The reload-listener
    # path (async_reload_entry) reaches here with the entry LOADED, so it must
    # use ``async_refresh`` (a plain data refresh with no state guard) instead.
    first_refresh = settings_entry.state is ConfigEntryState.SETUP_IN_PROGRESS

    for substance in ("caffeine", "alcohol"):
        store_key = drink_master_store_key(profile_id, substance)
        await store.async_load_drink_master(profile_id, substance, store_key)
        key = (profile_id, substance)
        if key in masters:
            # Existing coordinator -- refresh constants + refresh.
            masters[key].update_global_constants(settings_entry)
            if first_refresh:
                await masters[key].async_config_entry_first_refresh()
            else:
                await masters[key].async_refresh()
        else:
            master = DrinkMasterCoordinator(hass, profile_id, substance, store, store_key, settings_entry)
            master.update_global_constants(settings_entry)
            masters[key] = master
            if first_refresh:
                await master.async_config_entry_first_refresh()
            else:
                await master.async_refresh()


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
        # Version 6: Rename safe_doses -> pill_limit
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
        # tracking_type: "Regular Interval" -> "regular_interval", etc.
        old_tracking = new_data.get("tracking_type")
        if old_tracking and old_tracking in _TRACKING_TYPE_MIGRATION:
            new_data["tracking_type"] = _TRACKING_TYPE_MIGRATION[old_tracking]

        # release_type: "Instant Release" -> "instant_release", etc.
        old_release = new_data.get("release_type")
        if old_release and old_release in _RELEASE_TYPE_MIGRATION:
            new_data["release_type"] = _RELEASE_TYPE_MIGRATION[old_release]

        # strength_unit: "\u00b5g" -> "mcg" (mg and g unchanged)
        if new_data.get("strength_unit") == "\u00b5g":
            new_data["strength_unit"] = "mcg"
        if new_options.get("strength_unit") == "\u00b5g":
            new_options["strength_unit"] = "mcg"

    if config_entry.version <= 9:
        # Version 10: Convert metric_* booleans -> tracked_symptoms list
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
        # No config entry data shape change -- metric values are stored in
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
        # Version 13: Normalize strength_unit "mcg" -> "\u03bcg" (HA canonical
        # UnitOfMass.MICROGRAMS). The v9 migration converted the legacy
        # micro-sign "\u00b5g" (U+00B5) into "mcg", but "mcg" is NOT in
        # set(UnitOfMass), so SensorDeviceClass.WEIGHT sensors
        # (PillStrengthSensor, PillDailyAmountSensor) emitted a validation
        # warning on every state write. Convert any stored "mcg" (and the
        # legacy "\u00b5g" micro-sign that v9 may have missed for entries that
        # skipped v9) to the canonical "\u03bcg" (Greek mu U+03BC + g) in both
        # entry.data and entry.options.
        for unit_store in (new_data, new_options):
            current_unit = unit_store.get("strength_unit")
            if current_unit in ("mcg", "\u00b5g"):
                unit_store["strength_unit"] = "\u03bcg"

    if config_entry.version <= 13:
        # Version 14: Remove the Master Tracker "Est. days left" aggregate
        # sensor (DrinkMasterDaysLeftSensor). The Master Tracker has no
        # single inventory of its own -- summing every granular drink's stock
        # is misleading on the aggregate device. The per-granular-drink
        # DrinkDaysLeftSensor remains (it powers the Inventory panel's
        # per-drink "Est. days left" 2nd line). Remove the two master
        # entities from the registry so they don't linger as "unavailable".
        # Only the Drink Settings singleton owns these sensors.
        if new_data.get("device_category") == DEVICE_CATEGORY_DRINK_SETTINGS:
            ent_reg = er.async_get(hass)
            _remove_entity(ent_reg, "sensor", "drink_master_days_left_caffeine")
            _remove_entity(ent_reg, "sensor", "drink_master_days_left_alcohol")

    if config_entry.version <= 14:
        # Version 15: Rename adherence_grace_hours (hours) -> adherence_grace_minutes
        # (minutes). The On-Time Window field migrates from hours (step 0.5h,
        # which could not represent 45 min) to minutes (step 1, default 60).
        # Multiply any existing hours value by 60 and round. Entries that never
        # had the key (e.g. As Needed, where the field is hidden) get the
        # default 60 min. Applies to both entry.data and entry.options since
        # the field can live in either store depending on whether the user
        # reconfigured it post-setup. No entity-registry change (value tweak
        # only, not structural).
        for store in (new_data, new_options):
            old_hours = store.pop("adherence_grace_hours", None)
            if old_hours is not None:
                store["adherence_grace_minutes"] = round(float(old_hours) * 60)
            else:
                store.setdefault("adherence_grace_minutes", 60)

    if config_entry.version <= 15:
        # Version 16: Multi-Profile (M2M Decoupled Topology).
        # Inject profile fields into every existing entry so the config-flow
        # dropdowns and options flows have the keys present (rather than
        # relying on .get() fallbacks forever).  This is a one-line-per-entry,
        # idempotent migration.
        #
        # Drink Settings entries -> profile_id="default", profile_name=None.
        #   The legacy singleton keeps its non-UUID literal id so existing
        #   store keys / device ids / sensor unique_ids are unchanged
        #   (backwards compatibility).  Named profiles created post-v16 get
        #   the entry's own entry_id (UUID) as profile_id at creation time
        #   (see _ensure_drink_settings_entry), NOT here.
        #
        # Granular drink entries -> allowed_profiles=["default"].  The M2M
        # topology stores an array of allowed profile UUIDs (multi-select in
        # the drink config flow) instead of a single profile_id.  Existing
        # single-user drinks get the one-element array ["default"] so their
        # routing is identical (single-element -> convenience default routes
        # to the default master).  shared_drink defaults to False (the
        # frontend flag for the "Who is logging this?" popup).
        #
        # Granular drink dose_history (stored in .storage, NOT in config
        # entry data) is normalized to the 3-element form
        # [ts, strength, null] separately by the DrinkCoordinator load path
        # (defensive read: item[2] if len(item) > 2 else None).  No config-
        # entry data change is needed for the dose_history shape.
        if new_data.get("device_category") == DEVICE_CATEGORY_DRINK_SETTINGS:
            new_data.setdefault("profile_id", DEFAULT_PROFILE_ID)
            new_data.setdefault("profile_name", None)
        elif new_data.get("device_category") == DEVICE_CATEGORY_DRINKS:
            # allowed_profiles is the M2M array; legacy single-profile drinks
            # get ["default"].  shared_drink is the frontend flag.
            if "allowed_profiles" not in new_data:
                # Migrate a legacy single profile_id -> [profile_id] if present,
                # else ["default"].  Pre-v16 drinks never had profile_id, so
                # the common path is ["default"].
                legacy_pid = new_data.pop("profile_id", DEFAULT_PROFILE_ID)
                new_data["allowed_profiles"] = [legacy_pid]
            new_data.setdefault("shared_drink", False)

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
    #   1. INSTANCE race -- concurrent entries must share ONE AxDoseLoggerStore.
    #      Guarded by reserving the slot synchronously before any `await`
    #      (the prior fix): the first entry publishes the store object
    #      immediately so no concurrent entry creates a second instance.
    #   2. LOAD race -- concurrent entries must not read `_data` before the
    #      disk load completes.  Reserving the slot alone is NOT enough:
    #      a concurrent entry that arrives while entry #1 is still
    #      awaiting `store.async_load()` sees the slot populated, SKIPS the
    #      load block entirely, and reads an empty `_data`.  Its coordinator's
    #      `_async_setup` (which runs once during first refresh) then bakes
    #      `dose_history = []` into `self.data` and never re-reads -- so
    #      every derived sensor (total, last dose, daily amount, averages)
    #      resets to 0/unknown after restart for THAT entry.
    #      "Pills left" survived because `PillStockNumber` restores from the
    #      recorder via `RestoreNumber`, NOT from this store -- the smoking
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

    # Register REST views (idempotent -- HA ignores duplicate registrations)
    hass.http.register_view(AxDoseLoggerHistoryView())
    hass.http.register_view(AxDoseLoggerPredictLowView())
    hass.http.register_view(AxDoseLoggerGraphView())

    device_category = entry.data.get("device_category", DEVICE_CATEGORY_MEDICINE)

    if device_category == DEVICE_CATEGORY_DRINK_SETTINGS:
        # Drink Settings entry (a profile) -- creates the two Master Tracker
        # coordinators (caffeine/alcohol) for THIS profile.  Forwards to the
        # sensor platform which instantiates the master PK sensor entities.
        # The profile_id (immutable UUID, or "default" for the legacy
        # singleton) keys the coordinators in hass.data[DOMAIN]["_drink_masters"].
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
        # Granular drink entry (global inventory node).  Ensure at least the
        # legacy default Drink Settings entry exists so a master coordinator
        # is available to receive forwarded doses (a drink with an empty
        # allowed_profiles array is a pure inventory tracker and routes no
        # PK payload, but the default profile still must exist for the
        # convenience-default path).
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
        # enable_calendar, enable_adherence, tracking_type, tracked_symptoms, and
        # daily_limit affect which entities are created (see _STRUCTURAL_KEYS); all
        # other options are read fresh by the coordinator and sensors on every
        # update cycle, so they don't need a reload.
        "prev_structural": _get_structural_options(entry),
    }

    # First refresh loads dose history from the store
    await coordinator.async_config_entry_first_refresh()

    # Register domain-level services (idempotent -- skips if already registered)
    async_setup_services(hass)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_reload_entry(hass: HomeAssistant, entry: AxDoseLoggerConfigEntry) -> None:
    """
    Reload config entry, but only when structural options change.

    Compares ``enable_calendar``, ``enable_adherence``, ``tracking_type``,
    ``tracked_symptoms``, and ``daily_limit`` before/after.  If none changed
    (e.g. a PK-only save), the coordinator and sensors already read the new
    values on their next update cycle, so no reload or entity-registry
    surgery is needed.

    When a structural option *did* change, removes entities for newly-disabled
    features to prevent ghost "unavailable" entities, then reloads the entry.

    For the Drink Settings entry, a reload refreshes that profile's master
    coordinators' global PK constants (no entity-registry surgery needed).
    A profile_name rename is NOT structural (display-only; the immutable
    profile_id is unchanged) so it does not trigger a reload here -- the
    Master Tracker device display name refreshes on the next coordinator push.
    """
    device_category = entry.data.get("device_category", DEVICE_CATEGORY_MEDICINE)

    if device_category == DEVICE_CATEGORY_DRINK_SETTINGS:
        # Refresh this profile's master coordinator constants + restart their
        # refresh timers.  Only this profile is touched.
        await _setup_drink_masters(hass, entry)
        return

    if device_category == DEVICE_CATEGORY_DRINKS:
        # Granular drink entries only have mutable cooldown/dose_strength/
        # drinking_duration -- no structural entity changes.  Coordinator
        # reads the new options on its next update cycle.
        # NOTE: changing allowed_profiles IS structural (the multi-select in
        # async_step_drink_options triggers a reload via the options-flow
        # create_entry -> add_update_listener path, not here).  The reload
        # re-runs async_setup_entry which re-reads allowed_profiles.
        return

    # --- Medicine ---
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    prev = entry_data.get("prev_structural", {})
    curr = _get_structural_options(entry)

    # Detect which structural keys changed
    changed = {k for k in _STRUCTURAL_KEYS if prev.get(k) != curr.get(k)}

    if not changed:
        # No structural change -- coordinator and sensors will pick up the
        # new option values on their next update cycle.  Skip reload entirely.
        return

    ent_reg = er.async_get(hass)

    # --- enable_calendar: True -> False ---
    if "enable_calendar" in changed and not curr["enable_calendar"]:
        _remove_entity(ent_reg, "calendar", f"{entry.entry_id}_calendar")

    # --- enable_adherence: True -> False ---
    if "enable_adherence" in changed and not curr["enable_adherence"]:
        # Remove adherence sensors (7, 14, 30, 365-day windows)
        for window in (7, 14, 30, 365):
            _remove_entity(ent_reg, "sensor", f"{entry.entry_id}_adherence_{window}")
        # Remove adherence tool buttons
        for suffix in ("_reset_adherence", "_cover_last_missed", "_skip_dose"):
            _remove_entity(ent_reg, "button", f"{entry.entry_id}{suffix}")

    # --- tracking_type changed ---
    if "tracking_type" in changed and curr["tracking_type"] == TRACKING_AS_NEEDED:
        _remove_entity(ent_reg, "sensor", f"{entry.entry_id}_steady_state")
        _remove_entity(ent_reg, "sensor", f"{entry.entry_id}_overdue")
        _remove_entity(ent_reg, "calendar", f"{entry.entry_id}_calendar")
        for window in (7, 14, 30, 365):
            _remove_entity(ent_reg, "sensor", f"{entry.entry_id}_adherence_{window}")
        for suffix in ("_reset_adherence", "_cover_last_missed", "_skip_dose"):
            _remove_entity(ent_reg, "button", f"{entry.entry_id}{suffix}")

    # --- tracked_symptoms: metric removed ---
    if "tracked_symptoms" in changed:
        prev_tracked = set(prev.get("tracked_symptoms", []))
        curr_tracked = set(curr.get("tracked_symptoms", []))
        for key in prev_tracked - curr_tracked:
            _remove_entity(ent_reg, "number", f"{entry.entry_id}_eff_{key}")

    # --- daily_limit: >0 -> 0 (limit disabled) ---
    # The Pill24hLimitExceededSensor binary sensor is only created when
    # daily_limit > 0 (see _setup_medicine_sensors).  When the user disables
    # the limit (sets it back to 0), remove the now-orphaned entity so it
    # doesn't linger as a ghost "unavailable" binary sensor.  The 0 -> >0
    # direction (enabling the limit) needs no cleanup -- the entity is created
    # fresh by async_reload -> _setup_medicine_sensors after the reload.
    if "daily_limit" in changed and curr["daily_limit"] <= 0:
        _remove_entity(ent_reg, "sensor", f"{entry.entry_id}_24h_limit_exceeded")

    # Update the snapshot so the next options save has a fresh baseline
    entry_data["prev_structural"] = curr

    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: AxDoseLoggerConfigEntry) -> None:
    """Non-destructive scrubber -- called by HA when a config entry is deleted.

    For a Drink Settings entry (a profile), this scrubs the deleted profile's
    immutable UUID from every granular drink's ``allowed_profiles`` array.
    The drink devices themselves are NOT removed (M2M decoupled topology:
    drinks are global inventory nodes that survive profile deletion).  A
    drink whose ``allowed_profiles`` becomes empty after scrubbing degrades
    gracefully to a pure inventory tracker (no PK routing) -- it is NOT
    deleted.

    For a granular drink or medicine entry, this is a no-op (those entries
    don't own any cross-entry references).

    See plans/m2m-decoupled-topology-plan.md section 2.3 (Deletion Protocol).
    """
    device_category = entry.data.get("device_category", DEVICE_CATEGORY_MEDICINE)
    if device_category != DEVICE_CATEGORY_DRINK_SETTINGS:
        # Only profile deletion triggers the scrubber.
        return

    deleted_profile_id = _profile_id_of(entry)
    # The legacy "default" profile is scrubbed too -- removing it is the
    # user's explicit choice, and surviving drinks with allowed_profiles
    # containing "default" would otherwise reference a dead master forever.

    # Scan every drink entry and scrub the deleted UUID from its array.
    affected_entry_ids: list[str] = []
    for child in hass.config_entries.async_entries(DOMAIN):
        if child.data.get("device_category") != DEVICE_CATEGORY_DRINKS:
            continue
        allowed = list(child.data.get("allowed_profiles", []))
        if deleted_profile_id not in allowed:
            continue
        new_allowed = [p for p in allowed if p != deleted_profile_id]
        hass.config_entries.async_update_entry(
            child,
            data={**child.data, "allowed_profiles": new_allowed},
        )
        affected_entry_ids.append(child.entry_id)
        LOGGER.info(
            "M2M scrubber: removed profile %s from drink %s allowed_profiles "
            "(now %d profile(s) remain).",
            deleted_profile_id,
            child.title,
            len(new_allowed),
        )

    # Reload each affected drink so its sensors re-read the trimmed
    # allowed_profiles (the coordinator caches the array at setup).  Reloads
    # run after we return so the entry-removal sequence for the profile
    # completes first.
    for child_id in affected_entry_ids:
        await hass.config_entries.async_reload(child_id)


async def async_unload_entry(hass: HomeAssistant, entry: AxDoseLoggerConfigEntry) -> bool:
    """Unload a config entry and clean up domain-level state.

    Cleans up domain-level singletons (``_store``, ``_store_load``,
    ``_drink_masters``) when the last loaded entry is removed so
    ``hass.data[DOMAIN]`` does not leak (audit findings #3 and #4).  Also
    removes only the unloaded profile's master coordinators from the 2D
    ``_drink_masters`` dict (keyed ``(profile_id, substance)``) so a later
    re-created Drink Settings entry for a different profile does not reuse
    shut-down coordinators (audit finding #5).

    Ordering note: ``ConfigEntry.async_unload`` sets the entry state to
    ``UNLOAD_IN_PROGRESS`` *before* calling this function and runs the
    ``async_on_unload`` callbacks (coordinator ``async_shutdown``) *after* it
    returns.  Therefore ``async_loaded_entries(DOMAIN)`` already excludes the
    entry being unloaded, and removing the profile's coordinators here does
    not prevent their ``async_shutdown`` from running -- the bound
    ``self.async_shutdown`` method stored in ``entry._on_unload`` (registered
    in ``DataUpdateCoordinator.__init__``) keeps each coordinator alive until
    ``_async_process_on_unload`` runs them after we return.
    """
    device_category = entry.data.get("device_category", DEVICE_CATEGORY_MEDICINE)
    # Drink Settings forwards to sensor+button (the button platform hosts the
    # Master Tracker Averages Reset buttons); drinks forward to
    # sensor+button+number.
    if device_category == DEVICE_CATEGORY_DRINK_SETTINGS:
        platforms = ["sensor", "button"]
    elif device_category == DEVICE_CATEGORY_DRINKS:
        platforms = ["sensor", "button", "number"]
    else:
        platforms = PLATFORMS

    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

        # #5: Drop ONLY the unloaded profile's drink master coordinators.
        # The 2D dict is keyed (profile_id, substance); remove both substances
        # for this profile.  They have already been queued for ``async_shutdown``
        # via their ``config_entry.async_on_unload`` hook (registered in
        # DataUpdateCoordinator.__init__); that runs after we return.
        # Removing only this profile's entries (not .clear()-ing the whole dict)
        # preserves other profiles' coordinators in a multi-profile install.
        if device_category == DEVICE_CATEGORY_DRINK_SETTINGS:
            profile_id = _profile_id_of(entry)
            masters = _get_drink_masters(hass)
            for substance in ("caffeine", "alcohol"):
                masters.pop((profile_id, substance), None)

        # Remove services when the last coordinator-bearing entry (medicine or
        # drinks) is gone.  Drink Settings entries don't host a coordinator.
        if not any(isinstance(v, dict) and "coordinator" in v for v in hass.data.get(DOMAIN, {}).values()):
            async_unload_services(hass)

        # #3 + #4: When no loaded entries remain for the domain, drop the
        # domain-level singletons (``_store``, ``_store_load`` -- the completed
        # load Task -- and ``_drink_masters``) so they don't leak.
        # ``async_loaded_entries`` excludes the entry currently being unloaded
        # (state == ``UNLOAD_IN_PROGRESS``), so this fires on the final entry's
        # unload.  The store is recreated from disk on re-add.
        if not hass.config_entries.async_loaded_entries(DOMAIN):
            hass.data.pop(DOMAIN, None)
    return unload_ok
