from datetime import date

import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import callback
from homeassistant.helpers import selector as sel

from .const import (
    ALCOHOL_DEFAULT_LIMIT_G,
    CAFFEINE_DEFAULT_LIMIT_MG,
    CURRENT_VERSION,
    DEVICE_CATEGORIES,
    DEVICE_CATEGORY_DRINK_SETTINGS,
    DEVICE_CATEGORY_DRINKS,
    DEVICE_CATEGORY_MEDICINE,
    DOMAIN,
    DRINK_TYPE_CAFFEINE,
    DRINK_TYPES,
    GLOBAL_PK_DEFAULTS,
    MAX_DOSES_PER_DAY,
    PK_DEFAULTS,
    RELEASE_INSTANT,
    RELEASE_SUSTAINED,
    RELEASE_TYPES,
    STANDARD_EFFECTIVENESS_METRICS,
    STRENGTH_UNIT_MG,
    STRENGTH_UNITS,
    TRACKING_AS_NEEDED,
    TRACKING_CYCLIC,
    TRACKING_REGULAR_INTERVAL,
    TRACKING_TIME_OF_DAY,
    TRACKING_TYPES,
    generate_default_dose_times,
)

# Section keys for collapsible UI sections
_ADHERENCE_SECTION_KEY = "adherence"
_ADVANCED_PK_SECTION_KEY = "advanced_pk"

# Reusable selector configs — BOX mode for all numeric fields
_STOCK_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=9999, step=1, unit_of_measurement="pills", mode=sel.NumberSelectorMode.BOX
))
_HOURS_BETWEEN_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=1, max=48, step=1, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_PILL_LIMIT_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=1, max=20, step=1, unit_of_measurement="pills", mode=sel.NumberSelectorMode.BOX
))
_TIME_WINDOW_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0.5, max=168, step=0.5, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_STRENGTH_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=9999, step=0.5, mode=sel.NumberSelectorMode.BOX
))
_STRENGTH_UNIT_SELECTOR = sel.SelectSelector(
    sel.SelectSelectorConfig(
        options=STRENGTH_UNITS,
        mode=sel.SelectSelectorMode.DROPDOWN,
        translation_key="strength_unit",
    )
)
_HALF_LIFE_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=168, step=0.5, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_HOURS_TO_PEAK_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=72, step=0.1, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_IR_HOURS_TO_PEAK_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=72, step=0.1, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_BIOAVAILABILITY_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=100, step=1, unit_of_measurement="%", mode=sel.NumberSelectorMode.BOX
))
_IR_FRACTION_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=100, step=1, unit_of_measurement="%", mode=sel.NumberSelectorMode.BOX
))
_ZERO_ORDER_DURATION_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=24, step=0.5, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_RELEASE_HALF_LIFE_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=24, step=0.1, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_LAG_TIME_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=1440, step=1, unit_of_measurement="min", mode=sel.NumberSelectorMode.BOX
))
_DAYS_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=1, max=30, step=1, unit_of_measurement="days", mode=sel.NumberSelectorMode.BOX
))
_ADHERENCE_GRACE_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0.5, max=24, step=0.5, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_DOSES_PER_DAY_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=1, max=MAX_DOSES_PER_DAY, step=1, unit_of_measurement="times/day", mode=sel.NumberSelectorMode.BOX
))
_TRACKED_SYMPTOMS_SELECTOR = sel.SelectSelector(
    sel.SelectSelectorConfig(
        options=[
            {"value": key, "label": label}
            for key, label in STANDARD_EFFECTIVENESS_METRICS.items()
        ],
        multiple=True,
        mode=sel.SelectSelectorMode.LIST,
        translation_key="tracked_symptoms",
    )
)

# --- Drink selector configs ---
_DRINK_TYPE_SELECTOR = sel.SelectSelector(
    sel.SelectSelectorConfig(
        options=DRINK_TYPES,
        mode=sel.SelectSelectorMode.DROPDOWN,
        translation_key="drink_type",
    )
)
# Drink stock is measured in arbitrary units (cans, bottles, cups) — NOT pills.
# Medicine reuses _STOCK_SELECTOR (unit "pills"); drinks need their own selector
# so the config-flow UI does not display "pills" next to the input box.
_DRINK_STOCK_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=9999, step=1, unit_of_measurement="units", mode=sel.NumberSelectorMode.BOX
))
# Cooldown window is expressed in HOURS to align with medicine's time-window
# fields (_TIME_WINDOW_SELECTOR / _HOURS_BETWEEN_SELECTOR both use "h").
# Previously this was minutes (max 1440, unit "min"); changed per user request.
_COOLDOWN_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=24, step=0.5, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_DRINKING_DURATION_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=1, max=300, step=1, unit_of_measurement="min", mode=sel.NumberSelectorMode.BOX
))
_CAFFEINE_MG_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=2000, step=1, unit_of_measurement="mg", mode=sel.NumberSelectorMode.BOX
))
_VOLUME_ML_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=5000, step=1, unit_of_measurement="ml", mode=sel.NumberSelectorMode.BOX
))
_ABV_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=100, step=0.1, unit_of_measurement="%", mode=sel.NumberSelectorMode.BOX
))
_DOSE_STRENGTH_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=9999, step=0.1, mode=sel.NumberSelectorMode.BOX
))
# --- Global PK constant selectors (Drink Settings) ---
_GLOBAL_CAFFEINE_HALF_LIFE_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0.5, max=24, step=0.1, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_GLOBAL_CAFFEINE_TMAX_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0.1, max=8, step=0.05, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_GLOBAL_ALCOHOL_RATE_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=1, max=20, step=0.5, unit_of_measurement="g/h", mode=sel.NumberSelectorMode.BOX
))
# --- Daily intake limit selectors (24-hour window sensors) ---
# Medicine: entered in the device's own strength_unit (mg/mcg/g). 0 = no limit.
_DAILY_LIMIT_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=100000, step=1, mode=sel.NumberSelectorMode.BOX
))
# Caffeine: FDA default 400 mg/day (user-overridable in Drink Settings).
_CAFFEINE_DAILY_LIMIT_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=2000, step=1, unit_of_measurement="mg", mode=sel.NumberSelectorMode.BOX
))
# Alcohol: no FDA limit. Default 0 = no limit; user sets in grams ethanol.
_ALCOHOL_DAILY_LIMIT_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=500, step=0.5, unit_of_measurement="g", mode=sel.NumberSelectorMode.BOX
))

# Ethanol density (g/ml) for Widmark mass calculation
_ETHANOL_DENSITY = 0.789


def _make_adherence_section(enable_default=True, grace_default=1):
    """Create an Adherence Tracking section with the given defaults."""
    return data_entry_flow.section(
        vol.Schema({
            vol.Optional("enable_adherence", default=enable_default): sel.BooleanSelector(),
            vol.Optional("adherence_grace_hours", default=grace_default): _ADHERENCE_GRACE_SELECTOR,
        }),
        {"collapsed": False},
    )


def _make_advanced_pk_section(lag_default, zero_order_default=None, release_half_life_default=None):
    """Create a collapsed Advanced Pharmacokinetics section.

    Always includes lag_time. Conditionally includes zero_order_duration and
    release_half_life when their defaults are not None (i.e. for ER medications).
    """
    advanced_schema = {
        vol.Optional("lag_time", default=lag_default): _LAG_TIME_SELECTOR,
    }
    if zero_order_default is not None:
        advanced_schema[vol.Optional("zero_order_duration", default=zero_order_default)] = _ZERO_ORDER_DURATION_SELECTOR
    if release_half_life_default is not None:
        advanced_schema[vol.Optional("release_half_life", default=release_half_life_default)] = _RELEASE_HALF_LIFE_SELECTOR
    return data_entry_flow.section(
        vol.Schema(advanced_schema),
        data_entry_flow.SectionConfig(collapsed=True),
    )


class AxDoseLoggerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = CURRENT_VERSION

    def __init__(self):
        self._data = {}

    async def async_step_user(self, user_input=None):
        """Category router — medicine / drinks / drink_settings."""
        if user_input is not None:
            category = user_input["device_category"]
            if category == DEVICE_CATEGORY_MEDICINE:
                return await self.async_step_user_medicine()
            if category == DEVICE_CATEGORY_DRINKS:
                return await self.async_step_drink_setup()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("device_category", default=DEVICE_CATEGORY_MEDICINE): sel.SelectSelector(
                    sel.SelectSelectorConfig(
                        options=DEVICE_CATEGORIES,
                        mode=sel.SelectSelectorMode.DROPDOWN,
                        translation_key="device_category",
                    )
                ),
            })
        )

    # ------------------------------------------------------------------
    # Medicine flow (legacy — body of the old async_step_user)
    # ------------------------------------------------------------------
    async def async_step_user_medicine(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            self._data["device_category"] = DEVICE_CATEGORY_MEDICINE
            if user_input["tracking_type"] == TRACKING_REGULAR_INTERVAL:
                return await self.async_step_regular_interval()
            if user_input["tracking_type"] == TRACKING_TIME_OF_DAY:
                return await self.async_step_time_of_day()
            if user_input["tracking_type"] == TRACKING_CYCLIC:
                return await self.async_step_cyclic()
            return await self.async_step_as_needed()

        return self.async_show_form(
            step_id="user_medicine",
            data_schema=vol.Schema({
                vol.Required("medication_name", default="My Medication"): str,
                vol.Required("tracking_type", default=TRACKING_REGULAR_INTERVAL): sel.SelectSelector(
                    sel.SelectSelectorConfig(
                        options=TRACKING_TYPES,
                        mode=sel.SelectSelectorMode.DROPDOWN,
                        translation_key="tracking_type",
                    )
                ),
                vol.Required("release_type", default=RELEASE_INSTANT): sel.SelectSelector(
                    sel.SelectSelectorConfig(
                        options=RELEASE_TYPES,
                        mode=sel.SelectSelectorMode.DROPDOWN,
                        translation_key="release_type",
                    )
                ),
            })
        )

    async def async_step_regular_interval(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_pk()

        return self.async_show_form(
            step_id="regular_interval",
            data_schema=vol.Schema({
                vol.Required("initial_stock", default=30): _STOCK_SELECTOR,
                vol.Required("hours_between_doses", default=8): _HOURS_BETWEEN_SELECTOR,
                vol.Required("pill_limit", default=1): _PILL_LIMIT_SELECTOR,
                vol.Required("time_window_hours", default=8): _TIME_WINDOW_SELECTOR,
                vol.Optional("enable_calendar", default=False): sel.BooleanSelector(),
                vol.Optional("daily_limit", default=0): _DAILY_LIMIT_SELECTOR,
            }),
        )

    async def async_step_time_of_day(self, user_input=None):
        """Step 2a: Choose how many times per day and safety settings."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_time_of_day_times()

        return self.async_show_form(
            step_id="time_of_day",
            data_schema=vol.Schema({
                vol.Required("initial_stock", default=30): _STOCK_SELECTOR,
                vol.Required("doses_per_day", default=1): _DOSES_PER_DAY_SELECTOR,
                vol.Required("pill_limit", default=1): _PILL_LIMIT_SELECTOR,
                vol.Required("time_window_hours", default=24): _TIME_WINDOW_SELECTOR,
                vol.Optional("enable_calendar", default=False): sel.BooleanSelector(),
                vol.Optional("daily_limit", default=0): _DAILY_LIMIT_SELECTOR,
            }),
        )

    async def async_step_time_of_day_times(self, user_input=None):
        """Step 2b: Configure individual dose times based on doses_per_day."""
        doses_per_day = int(self._data.get("doses_per_day", 1))
        defaults = generate_default_dose_times(doses_per_day)

        if user_input is not None:
            # Collect dose_time_1..dose_time_N into a sorted list
            dose_times = []
            for i in range(doses_per_day):
                key = f"dose_time_{i + 1}"
                val = user_input.get(key, defaults[i] if i < len(defaults) else "08:00")
                dose_times.append(val)
            dose_times.sort()
            self._data["dose_times"] = dose_times
            # Remove individual dose_time_N keys from data
            for i in range(doses_per_day):
                self._data.pop(f"dose_time_{i + 1}", None)
            return await self.async_step_pk()

        # Build schema with dose_time_1 through dose_time_N
        schema_fields = {}
        for i in range(doses_per_day):
            default_time = defaults[i] if i < len(defaults) else "08:00"
            schema_fields[vol.Required(f"dose_time_{i + 1}", default=default_time)] = sel.TimeSelector()

        return self.async_show_form(
            step_id="time_of_day_times",
            data_schema=vol.Schema(schema_fields),
        )

    async def async_step_as_needed(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            self._data["enable_calendar"] = False  # No calendar for PRN meds
            return await self.async_step_pk()

        return self.async_show_form(
            step_id="as_needed",
            data_schema=vol.Schema({
                vol.Required("initial_stock", default=30): _STOCK_SELECTOR,
                vol.Required("pill_limit", default=2): _PILL_LIMIT_SELECTOR,
                vol.Required("time_window_hours", default=8): _TIME_WINDOW_SELECTOR,
                vol.Optional("daily_limit", default=0): _DAILY_LIMIT_SELECTOR,
            }),
        )

    async def async_step_cyclic(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_pk()

        return self.async_show_form(
            step_id="cyclic",
            data_schema=vol.Schema({
                vol.Required("initial_stock", default=30): _STOCK_SELECTOR,
                vol.Required("days_on", default=5): _DAYS_SELECTOR,
                vol.Required("days_off", default=2): _DAYS_SELECTOR,
                vol.Required("cycle_anchor_date", default=date.today().isoformat()): sel.DateSelector(sel.DateSelectorConfig()),
                vol.Required("dose_time", default="08:00"): sel.TimeSelector(),
                vol.Required("pill_limit", default=1): _PILL_LIMIT_SELECTOR,
                vol.Required("time_window_hours", default=24): _TIME_WINDOW_SELECTOR,
                vol.Optional("enable_calendar", default=False): sel.BooleanSelector(),
                vol.Optional("daily_limit", default=0): _DAILY_LIMIT_SELECTOR,
            }),
        )

    async def async_step_pk(self, user_input=None):
        """Step 3: Pharmacokinetic parameters for concentration tracking."""
        if user_input is not None:
            # Flatten the collapsed Advanced PK section into the top-level data
            advanced_data = user_input.pop(_ADVANCED_PK_SECTION_KEY, {})
            user_input.update(advanced_data)
            self._data.update(user_input)
            return await self.async_step_effectiveness()

        release_type = self._data.get("release_type", RELEASE_INSTANT)

        pk_schema = {
            vol.Optional("strength", default=0): _STRENGTH_SELECTOR,
            vol.Optional("strength_unit", default=STRENGTH_UNIT_MG): _STRENGTH_UNIT_SELECTOR,
            vol.Optional("half_life", default=0): _HALF_LIFE_SELECTOR,
            vol.Optional("hours_to_peak", default=0): _HOURS_TO_PEAK_SELECTOR,
            vol.Optional("bioavailability", default=PK_DEFAULTS["bioavailability"]): _BIOAVAILABILITY_SELECTOR,
        }

        if release_type == RELEASE_SUSTAINED:
            pk_schema[vol.Optional("ir_hours_to_peak", default=PK_DEFAULTS["ir_hours_to_peak"])] = _IR_HOURS_TO_PEAK_SELECTOR
            pk_schema[vol.Optional("ir_fraction", default=PK_DEFAULTS["ir_fraction"])] = _IR_FRACTION_SELECTOR
            # Advanced section: lag_time (always) + ER-only zero_order_duration & release_half_life
            pk_schema[vol.Required(_ADVANCED_PK_SECTION_KEY)] = _make_advanced_pk_section(
                lag_default=PK_DEFAULTS["lag_time"],
                zero_order_default=PK_DEFAULTS["zero_order_duration"],
                release_half_life_default=PK_DEFAULTS["release_half_life"],
            )
        else:
            # Advanced section: lag_time only (IR medications)
            pk_schema[vol.Required(_ADVANCED_PK_SECTION_KEY)] = _make_advanced_pk_section(
                lag_default=PK_DEFAULTS["lag_time"],
            )

        return self.async_show_form(
            step_id="pk",
            data_schema=vol.Schema(pk_schema),
        )

    async def async_step_effectiveness(self, user_input=None):
        """Step 4: Choose which effectiveness metrics to track and adherence settings."""
        if user_input is not None:
            adherence_data = user_input.pop(_ADHERENCE_SECTION_KEY, {})
            user_input.update(adherence_data)
            self._data.update(user_input)
            # Force adherence off for As Needed — no scheduled doses to track
            if self._data.get("tracking_type") == TRACKING_AS_NEEDED:
                self._data["enable_adherence"] = False
            return self.async_create_entry(title=self._data["medication_name"], data=self._data)

        tracking_type = self._data.get("tracking_type")
        default_adherence = tracking_type != TRACKING_AS_NEEDED

        fields = {}
        fields[vol.Optional("tracked_symptoms", default=[])] = _TRACKED_SYMPTOMS_SELECTOR
        fields[vol.Optional("custom_metrics", default="")] = sel.TextSelector()
        # Only show adherence section for scheduled tracking types
        if tracking_type != TRACKING_AS_NEEDED:
            fields[vol.Required(_ADHERENCE_SECTION_KEY)] = _make_adherence_section(
                enable_default=default_adherence,
            )

        return self.async_show_form(
            step_id="effectiveness",
            data_schema=vol.Schema(fields)
        )

    # ------------------------------------------------------------------
    # Drinks flow — Step 1: Drink Setup
    # ------------------------------------------------------------------
    async def async_step_drink_setup(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_drink_cooldown()

        return self.async_show_form(
            step_id="drink_setup",
            data_schema=vol.Schema({
                vol.Required("name", default="My Drink"): str,
                vol.Required("drink_type", default=DRINK_TYPE_CAFFEINE): _DRINK_TYPE_SELECTOR,
                vol.Required("unit_of_measurement", default="Cups"): str,
                vol.Required("initial_stock", default=12): _DRINK_STOCK_SELECTOR,
            }),
        )

    # ------------------------------------------------------------------
    # Drinks flow — Step 2: Cooldown Timer
    # ------------------------------------------------------------------
    async def async_step_drink_cooldown(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            drink_type = self._data.get("drink_type")
            if drink_type == DRINK_TYPE_CAFFEINE:
                return await self.async_step_drink_caffeine()
            return await self.async_step_drink_alcohol()

        return self.async_show_form(
            step_id="drink_cooldown",
            data_schema=vol.Schema({
                vol.Required("cooldown_window", default=0): _COOLDOWN_SELECTOR,
            }),
        )

    # ------------------------------------------------------------------
    # Drinks flow — Step 3a: Caffeine payload
    # ------------------------------------------------------------------
    async def async_step_drink_caffeine(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            # dose_strength = caffeine_mg; bioavailability hardcoded to 100
            self._data["dose_strength"] = float(user_input["caffeine_mg"])
            self._data["bioavailability"] = 100
            self._data["device_category"] = DEVICE_CATEGORY_DRINKS
            return await self._create_drink_entry()

        return self.async_show_form(
            step_id="drink_caffeine",
            data_schema=vol.Schema({
                vol.Required("caffeine_mg", default=90): _CAFFEINE_MG_SELECTOR,
                vol.Required("drinking_duration", default=15): _DRINKING_DURATION_SELECTOR,
            }),
        )

    # ------------------------------------------------------------------
    # Drinks flow — Step 3b: Alcohol payload (Widmark mass computed silently)
    # ------------------------------------------------------------------
    async def async_step_drink_alcohol(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            # Widmark: grams_ethanol = volume_ml * (abv_percent / 100) * 0.789
            volume_ml = float(user_input["volume_ml"])
            abv_percent = float(user_input["abv_percent"])
            self._data["dose_strength"] = round(volume_ml * (abv_percent / 100.0) * _ETHANOL_DENSITY, 2)
            self._data["bioavailability"] = 100
            self._data["device_category"] = DEVICE_CATEGORY_DRINKS
            return await self._create_drink_entry()

        return self.async_show_form(
            step_id="drink_alcohol",
            data_schema=vol.Schema({
                vol.Required("volume_ml", default=330): _VOLUME_ML_SELECTOR,
                vol.Required("abv_percent", default=5.0): _ABV_SELECTOR,
                vol.Required("drinking_duration", default=15): _DRINKING_DURATION_SELECTOR,
            }),
        )

    async def _create_drink_entry(self):
        """Create the drink config entry and ensure the Drink Settings singleton exists."""
        # Title uses the drink name; remove the raw payload fields from data
        # that were only needed for dose_strength computation.
        title = self._data.get("name", "My Drink")
        # Strip per-payload intermediate keys (keep dose_strength + drinking_duration)
        entry_data = {k: v for k, v in self._data.items() if k not in ("caffeine_mg", "volume_ml", "abv_percent")}
        return self.async_create_entry(title=title, data=entry_data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Route to the correct options flow handler by device category."""
        category = config_entry.data.get("device_category", DEVICE_CATEGORY_MEDICINE)
        handler = AxDoseLoggerOptionsFlowHandler(config_entry)
        handler._initial_step = {
            DEVICE_CATEGORY_MEDICINE: "init",
            DEVICE_CATEGORY_DRINK_SETTINGS: "drink_settings_options",
            DEVICE_CATEGORY_DRINKS: "drink_options",
        }.get(category, "init")
        return handler


class AxDoseLoggerOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._entry = config_entry
        self._data = {}
        # Snapshot of the tracking_type at the moment the options flow opened.
        # Used to detect whether the user changed it in async_step_init, which
        # routes to async_step_schedule to collect the new type's schedule
        # fields before continuing to the PK step.
        self._original_tracking_type = config_entry.data.get("tracking_type")
        # Device category determines which options flow branch to use.
        self._device_category = config_entry.data.get("device_category", DEVICE_CATEGORY_MEDICINE)
        # Initial step for this handler — set by async_get_options_flow.
        # Medicine entries start at 'init'; Drink Settings at
        # 'drink_settings_options'; granular drinks at 'drink_options'.
        self._initial_step = "init"

    # ------------------------------------------------------------------
    # Medicine options flow (legacy)
    # ------------------------------------------------------------------
    async def async_step_init(self, user_input=None):
        """Step 1: Schedule and dosing parameters (medicine category).

        For Drink Settings / granular drink entries, dispatch to the
        appropriate single-step options flow instead of the medicine
        multi-step flow.
        """
        if self._initial_step != "init" and user_input is None:
            # Route to the category-specific first step on the initial show.
            method = getattr(self, f"async_step_{self._initial_step}")
            return await method(None)
        if user_input is not None:
            self._data.update(user_input)
            new_tracking_type = user_input.get("tracking_type")
            # For Time of Day (unchanged): collect dose_time_N into dose_times list
            if new_tracking_type == TRACKING_TIME_OF_DAY and new_tracking_type == self._original_tracking_type:
                doses_per_day = int(user_input.get("doses_per_day", self._entry.data.get("doses_per_day", 1)))
                dose_times = []
                for i in range(doses_per_day):
                    key = f"dose_time_{i + 1}"
                    val = user_input.get(key)
                    if val:
                        dose_times.append(val)
                dose_times.sort()
                self._data["dose_times"] = dose_times
                # Remove individual dose_time_N keys
                for i in range(doses_per_day):
                    self._data.pop(f"dose_time_{i + 1}", None)
            # Force calendar off for As Needed — no predictable schedule
            if new_tracking_type == TRACKING_AS_NEEDED:
                self._data["enable_calendar"] = False
            # If the tracking type changed, collect the new type's schedule
            # fields in a dedicated step before continuing to PK.
            if new_tracking_type != self._original_tracking_type:
                return await self.async_step_schedule()
            return await self.async_step_pk()

        tracking_type = self._entry.data.get("tracking_type")
        options = self._entry.options
        data = self._entry.data

        main_schema = {}
        # Tracking type selector — allows changing the type post-setup.
        main_schema[vol.Required("tracking_type", default=tracking_type)] = sel.SelectSelector(
            sel.SelectSelectorConfig(
                options=TRACKING_TYPES,
                mode=sel.SelectSelectorMode.DROPDOWN,
                translation_key="tracking_type",
            )
        )
        if tracking_type == TRACKING_REGULAR_INTERVAL:
            main_schema[vol.Required("hours_between_doses", default=options.get("hours_between_doses", data.get("hours_between_doses", 8)))] = _HOURS_BETWEEN_SELECTOR
            # Default time_window_hours to hours_between_doses if not explicitly set
            tw_default = options.get("time_window_hours", data.get("time_window_hours", data.get("hours_between_doses", 8)))
            main_schema[vol.Required("time_window_hours", default=tw_default)] = _TIME_WINDOW_SELECTOR
        elif tracking_type == TRACKING_TIME_OF_DAY:
            current_doses_per_day = int(options.get("doses_per_day", data.get("doses_per_day", 1)))
            main_schema[vol.Required("doses_per_day", default=current_doses_per_day)] = _DOSES_PER_DAY_SELECTOR
            current_dose_times = options.get("dose_times", data.get("dose_times", generate_default_dose_times(current_doses_per_day)))
            # Add dose_time_1 through dose_time_N
            for i in range(current_doses_per_day):
                default_val = current_dose_times[i] if i < len(current_dose_times) else "08:00"
                main_schema[vol.Required(f"dose_time_{i + 1}", default=default_val)] = sel.TimeSelector()
            main_schema[vol.Required("time_window_hours", default=options.get("time_window_hours", data.get("time_window_hours", 24)))] = _TIME_WINDOW_SELECTOR
        elif tracking_type == TRACKING_AS_NEEDED:
            main_schema[vol.Required("time_window_hours", default=options.get("time_window_hours", data.get("time_window_hours", 8)))] = _TIME_WINDOW_SELECTOR
        elif tracking_type == TRACKING_CYCLIC:
            main_schema[vol.Required("days_on", default=options.get("days_on", data.get("days_on", 5)))] = _DAYS_SELECTOR
            main_schema[vol.Required("days_off", default=options.get("days_off", data.get("days_off", 2)))] = _DAYS_SELECTOR
            main_schema[vol.Required("cycle_anchor_date", default=options.get("cycle_anchor_date", data.get("cycle_anchor_date", date.today().isoformat())))] = sel.DateSelector(sel.DateSelectorConfig())
            main_schema[vol.Required("dose_time", default=options.get("dose_time", data.get("dose_time", "08:00")))] = sel.TimeSelector()
            main_schema[vol.Required("time_window_hours", default=options.get("time_window_hours", data.get("time_window_hours", 24)))] = _TIME_WINDOW_SELECTOR

        main_schema[vol.Required("pill_limit", default=options.get("pill_limit", data.get("pill_limit", 1)))] = _PILL_LIMIT_SELECTOR
        main_schema[vol.Optional("daily_limit", default=options.get("daily_limit", data.get("daily_limit", 0)))] = _DAILY_LIMIT_SELECTOR

        # Calendar toggle — only for scheduled tracking types (not As Needed)
        if tracking_type != TRACKING_AS_NEEDED:
            main_schema[vol.Optional("enable_calendar", default=options.get("enable_calendar", data.get("enable_calendar", False)))] = sel.BooleanSelector()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(main_schema),
        )

    async def async_step_schedule(self, user_input=None):
        """Step 1b: Collect schedule fields for a newly-selected tracking type.

        Shown only when the user changed ``tracking_type`` in
        ``async_step_init``.  Presents the new type's schedule-specific
        fields with config-flow defaults (since the entry's data/options
        don't yet contain fields for the new type).  For Time of Day, this
        is a two-sub-step flow: first ``doses_per_day`` + safety fields,
        then ``async_step_schedule_times`` for the N individual times.
        """
        new_tracking_type = self._data.get("tracking_type")

        if user_input is not None:
            self._data.update(user_input)
            # For Time of Day: route to the times sub-step
            if new_tracking_type == TRACKING_TIME_OF_DAY:
                return await self.async_step_schedule_times()
            return await self.async_step_pk()

        # Build the schema for the new tracking type using config-flow defaults.
        # Existing entry.data/options values are reused where the field already
        # exists (e.g. pill_limit, time_window_hours), otherwise defaults.
        options = self._entry.options
        data = self._entry.data
        schema = {}

        if new_tracking_type == TRACKING_REGULAR_INTERVAL:
            schema[vol.Required("hours_between_doses", default=options.get("hours_between_doses", data.get("hours_between_doses", 8)))] = _HOURS_BETWEEN_SELECTOR
            tw_default = options.get("time_window_hours", data.get("time_window_hours", 8))
            schema[vol.Required("time_window_hours", default=tw_default)] = _TIME_WINDOW_SELECTOR
        elif new_tracking_type == TRACKING_TIME_OF_DAY:
            schema[vol.Required("doses_per_day", default=1)] = _DOSES_PER_DAY_SELECTOR
            schema[vol.Required("time_window_hours", default=options.get("time_window_hours", data.get("time_window_hours", 24)))] = _TIME_WINDOW_SELECTOR
        elif new_tracking_type == TRACKING_AS_NEEDED:
            schema[vol.Required("time_window_hours", default=options.get("time_window_hours", data.get("time_window_hours", 8)))] = _TIME_WINDOW_SELECTOR
        elif new_tracking_type == TRACKING_CYCLIC:
            schema[vol.Required("days_on", default=5)] = _DAYS_SELECTOR
            schema[vol.Required("days_off", default=2)] = _DAYS_SELECTOR
            schema[vol.Required("cycle_anchor_date", default=date.today().isoformat())] = sel.DateSelector(sel.DateSelectorConfig())
            schema[vol.Required("dose_time", default="08:00")] = sel.TimeSelector()
            schema[vol.Required("time_window_hours", default=options.get("time_window_hours", data.get("time_window_hours", 24)))] = _TIME_WINDOW_SELECTOR

        schema[vol.Required("pill_limit", default=options.get("pill_limit", data.get("pill_limit", 1)))] = _PILL_LIMIT_SELECTOR
        schema[vol.Optional("daily_limit", default=options.get("daily_limit", data.get("daily_limit", 0)))] = _DAILY_LIMIT_SELECTOR

        # Calendar toggle — only for scheduled tracking types (not As Needed)
        if new_tracking_type != TRACKING_AS_NEEDED:
            schema[vol.Optional("enable_calendar", default=options.get("enable_calendar", data.get("enable_calendar", False)))] = sel.BooleanSelector()

        return self.async_show_form(
            step_id="schedule",
            data_schema=vol.Schema(schema),
        )

    async def async_step_schedule_times(self, user_input=None):
        """Step 1c: Configure individual dose times for a new Time of Day type.

        Mirrors the config flow's ``async_step_time_of_day_times``.  Shown
        only when switching *to* Time of Day (after ``async_step_schedule``
        collected ``doses_per_day``).
        """
        doses_per_day = int(self._data.get("doses_per_day", 1))
        defaults = generate_default_dose_times(doses_per_day)

        if user_input is not None:
            dose_times = []
            for i in range(doses_per_day):
                key = f"dose_time_{i + 1}"
                val = user_input.get(key, defaults[i] if i < len(defaults) else "08:00")
                dose_times.append(val)
            dose_times.sort()
            self._data["dose_times"] = dose_times
            for i in range(doses_per_day):
                self._data.pop(f"dose_time_{i + 1}", None)
            return await self.async_step_pk()

        schema_fields = {}
        for i in range(doses_per_day):
            default_time = defaults[i] if i < len(defaults) else "08:00"
            schema_fields[vol.Required(f"dose_time_{i + 1}", default=default_time)] = sel.TimeSelector()

        return self.async_show_form(
            step_id="schedule_times",
            data_schema=vol.Schema(schema_fields),
        )

    async def async_step_pk(self, user_input=None):
        """Step 2: Pharmacokinetic parameters."""
        if user_input is not None:
            # Flatten the collapsed Advanced PK section into the top-level data
            advanced_data = user_input.pop(_ADVANCED_PK_SECTION_KEY, {})
            user_input.update(advanced_data)
            self._data.update(user_input)
            return await self.async_step_effectiveness()

        options = self._entry.options
        data = self._entry.data
        release_type = data.get("release_type", RELEASE_INSTANT)

        pk_schema = {
            vol.Optional("strength", default=options.get("strength", data.get("strength", 0))): _STRENGTH_SELECTOR,
            vol.Optional("strength_unit", default=options.get("strength_unit", data.get("strength_unit", STRENGTH_UNIT_MG))): _STRENGTH_UNIT_SELECTOR,
            vol.Optional("half_life", default=options.get("half_life", data.get("half_life", 0))): _HALF_LIFE_SELECTOR,
            vol.Optional("hours_to_peak", default=options.get("hours_to_peak", data.get("hours_to_peak", 0))): _HOURS_TO_PEAK_SELECTOR,
            vol.Optional("bioavailability", default=options.get("bioavailability", data.get("bioavailability", PK_DEFAULTS["bioavailability"]))): _BIOAVAILABILITY_SELECTOR,
        }

        if release_type == RELEASE_SUSTAINED:
            pk_schema[vol.Optional("ir_hours_to_peak", default=options.get("ir_hours_to_peak", data.get("ir_hours_to_peak", PK_DEFAULTS["ir_hours_to_peak"])))] = _IR_HOURS_TO_PEAK_SELECTOR
            pk_schema[vol.Optional("ir_fraction", default=options.get("ir_fraction", data.get("ir_fraction", PK_DEFAULTS["ir_fraction"])))] = _IR_FRACTION_SELECTOR
            # Advanced section: lag_time (always) + ER-only zero_order_duration & release_half_life
            pk_schema[vol.Required(_ADVANCED_PK_SECTION_KEY)] = _make_advanced_pk_section(
                lag_default=options.get("lag_time", data.get("lag_time", PK_DEFAULTS["lag_time"])),
                zero_order_default=options.get("zero_order_duration", data.get("zero_order_duration", PK_DEFAULTS["zero_order_duration"])),
                release_half_life_default=options.get("release_half_life", data.get("release_half_life", PK_DEFAULTS["release_half_life"])),
            )
        else:
            # Advanced section: lag_time only (IR medications)
            pk_schema[vol.Required(_ADVANCED_PK_SECTION_KEY)] = _make_advanced_pk_section(
                lag_default=options.get("lag_time", data.get("lag_time", PK_DEFAULTS["lag_time"])),
            )

        return self.async_show_form(
            step_id="pk",
            data_schema=vol.Schema(pk_schema),
        )

    async def async_step_effectiveness(self, user_input=None):
        """Step 3: Choose which effectiveness metrics to track and adherence settings."""
        if user_input is not None:
            adherence_data = user_input.pop(_ADHERENCE_SECTION_KEY, {})
            user_input.update(adherence_data)
            self._data.update(user_input)
            # Use the (possibly changed) tracking_type from self._data, not
            # the original entry.data value, so adherence is forced off when
            # switching to As Needed.
            new_tracking_type = self._data.get("tracking_type", self._entry.data.get("tracking_type"))
            if new_tracking_type == TRACKING_AS_NEEDED:
                self._data["enable_adherence"] = False
            # If the tracking type changed, persist the new type + schedule
            # fields into entry.data via async_update_entry.  OptionsFlow
            # only writes entry.options, so we mutate entry.data explicitly
            # here.  This fires the update listener (async_reload_entry),
            # which detects the tracking_type change and reloads the entry
            # to recreate entities for the new type.
            if new_tracking_type != self._original_tracking_type:
                new_data = {**self._entry.data}
                # Copy tracking_type + schedule fields from self._data into data.
                # These are the fields that belong in entry.data (set during
                # the initial config flow); PK/effectiveness/common fields
                # stay in entry.options via async_create_entry below.
                _DATA_KEYS_ON_TYPE_CHANGE = (
                    "tracking_type",
                    "hours_between_doses",
                    "doses_per_day",
                    "dose_times",
                    "days_on",
                    "days_off",
                    "cycle_anchor_date",
                    "dose_time",
                    "time_window_hours",
                    "pill_limit",
                    "daily_limit",
                    "enable_calendar",
                )
                for key in _DATA_KEYS_ON_TYPE_CHANGE:
                    if key in self._data:
                        new_data[key] = self._data[key]
                self.hass.config_entries.async_update_entry(
                    self._entry, data=new_data
                )
            # OptionsFlow.async_create_entry REPLACES entry.options entirely
            # (it does not merge). self._data accumulates all fields across the
            # steps (init/schedule → pk → effectiveness) via .update(), so the
            # complete options dict is submitted here.
            return self.async_create_entry(title="", data=self._data)

        options = self._entry.options
        data = self._entry.data
        # Use the (possibly changed) tracking_type from self._data if the user
        # already went through the schedule step; otherwise the original.
        tracking_type = self._data.get("tracking_type", data.get("tracking_type"))

        fields = {}
        # Read tracked_symptoms from options/data; fall back to migrating
        # legacy metric_* booleans for not-yet-migrated v9 entries.
        existing_tracked = options.get("tracked_symptoms", data.get("tracked_symptoms"))
        if existing_tracked is None:
            existing_tracked = [
                key for key in STANDARD_EFFECTIVENESS_METRICS
                if options.get(f"metric_{key}", data.get(f"metric_{key}", False))
            ]
        fields[vol.Optional("tracked_symptoms", default=existing_tracked)] = _TRACKED_SYMPTOMS_SELECTOR
        fields[vol.Optional("custom_metrics", default=options.get("custom_metrics", data.get("custom_metrics", "")))] = sel.TextSelector()

        # Only show adherence section for scheduled tracking types
        if tracking_type != TRACKING_AS_NEEDED:
            fields[vol.Required(_ADHERENCE_SECTION_KEY)] = _make_adherence_section(
                enable_default=options.get("enable_adherence", data.get("enable_adherence", True)),
                grace_default=options.get("adherence_grace_hours", data.get("adherence_grace_hours", 1)),
            )

        return self.async_show_form(
            step_id="effectiveness",
            data_schema=vol.Schema(fields)
        )

    # ------------------------------------------------------------------
    # Drink Settings options flow — global PK constants
    # ------------------------------------------------------------------
    async def async_step_drink_settings_options(self, user_input=None):
        """Edit global metabolic constants for the Drink Settings entry."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="", data=self._data)

        options = self._entry.options
        data = self._entry.data
        return self.async_show_form(
            step_id="drink_settings_options",
            data_schema=vol.Schema({
                vol.Required("global_caffeine_half_life", default=options.get("global_caffeine_half_life", data.get("global_caffeine_half_life", GLOBAL_PK_DEFAULTS["global_caffeine_half_life"]))): _GLOBAL_CAFFEINE_HALF_LIFE_SELECTOR,
                vol.Required("global_caffeine_tmax", default=options.get("global_caffeine_tmax", data.get("global_caffeine_tmax", GLOBAL_PK_DEFAULTS["global_caffeine_tmax"]))): _GLOBAL_CAFFEINE_TMAX_SELECTOR,
                vol.Required("global_alcohol_elimination_rate", default=options.get("global_alcohol_elimination_rate", data.get("global_alcohol_elimination_rate", GLOBAL_PK_DEFAULTS["global_alcohol_elimination_rate"]))): _GLOBAL_ALCOHOL_RATE_SELECTOR,
                vol.Required("caffeine_daily_limit_mg", default=options.get("caffeine_daily_limit_mg", data.get("caffeine_daily_limit_mg", CAFFEINE_DEFAULT_LIMIT_MG))): _CAFFEINE_DAILY_LIMIT_SELECTOR,
                vol.Required("alcohol_daily_limit_g", default=options.get("alcohol_daily_limit_g", data.get("alcohol_daily_limit_g", ALCOHOL_DEFAULT_LIMIT_G))): _ALCOHOL_DAILY_LIMIT_SELECTOR,
            }),
        )

    # ------------------------------------------------------------------
    # Granular drink options flow — cooldown + dose_strength + drinking_duration
    # ------------------------------------------------------------------
    async def async_step_drink_options(self, user_input=None):
        """Edit a granular drink's mutable settings (name/drink_type immutable)."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="", data=self._data)

        options = self._entry.options
        data = self._entry.data
        return self.async_show_form(
            step_id="drink_options",
            data_schema=vol.Schema({
                vol.Required("cooldown_window", default=options.get("cooldown_window", data.get("cooldown_window", 0))): _COOLDOWN_SELECTOR,
                vol.Required("dose_strength", default=options.get("dose_strength", data.get("dose_strength", 0))): _DOSE_STRENGTH_SELECTOR,
                vol.Required("drinking_duration", default=options.get("drinking_duration", data.get("drinking_duration", 15))): _DRINKING_DURATION_SELECTOR,
            }),
        )
