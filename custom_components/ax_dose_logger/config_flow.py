from datetime import date

import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import callback
from homeassistant.helpers import selector as sel

from .const import (
    CURRENT_VERSION,
    DOMAIN,
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

# Section key for adherence (still used as a section)
_ADHERENCE_SECTION_KEY = "adherence"

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


def _make_adherence_section(enable_default=True, grace_default=1):
    """Create an Adherence Tracking section with the given defaults."""
    return data_entry_flow.section(
        vol.Schema({
            vol.Optional("enable_adherence", default=enable_default): sel.BooleanSelector(),
            vol.Optional("adherence_grace_hours", default=grace_default): _ADHERENCE_GRACE_SELECTOR,
        }),
        {"collapsed": False},
    )


class PillLoggerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = CURRENT_VERSION

    def __init__(self):
        self._data = {}

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            if user_input["tracking_type"] == TRACKING_REGULAR_INTERVAL:
                return await self.async_step_regular_interval()
            if user_input["tracking_type"] == TRACKING_TIME_OF_DAY:
                return await self.async_step_time_of_day()
            if user_input["tracking_type"] == TRACKING_CYCLIC:
                return await self.async_step_cyclic()
            return await self.async_step_as_needed()

        return self.async_show_form(
            step_id="user",
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
                vol.Optional("enable_calendar", default=True): sel.BooleanSelector(),
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
                vol.Optional("enable_calendar", default=True): sel.BooleanSelector(),
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
                vol.Optional("enable_calendar", default=True): sel.BooleanSelector(),
            }),
        )

    async def async_step_pk(self, user_input=None):
        """Step 3: Pharmacokinetic parameters for concentration tracking."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_effectiveness()

        release_type = self._data.get("release_type", RELEASE_INSTANT)

        pk_schema = {
            vol.Optional("strength", default=0): _STRENGTH_SELECTOR,
            vol.Optional("strength_unit", default=STRENGTH_UNIT_MG): _STRENGTH_UNIT_SELECTOR,
            vol.Optional("half_life", default=0): _HALF_LIFE_SELECTOR,
            vol.Optional("hours_to_peak", default=0): _HOURS_TO_PEAK_SELECTOR,
            vol.Optional("bioavailability", default=PK_DEFAULTS["bioavailability"]): _BIOAVAILABILITY_SELECTOR,
            vol.Optional("lag_time", default=PK_DEFAULTS["lag_time"]): _LAG_TIME_SELECTOR,
        }

        if release_type == RELEASE_SUSTAINED:
            pk_schema[vol.Optional("ir_hours_to_peak", default=PK_DEFAULTS["ir_hours_to_peak"])] = _IR_HOURS_TO_PEAK_SELECTOR
            pk_schema[vol.Optional("ir_fraction", default=PK_DEFAULTS["ir_fraction"])] = _IR_FRACTION_SELECTOR
            pk_schema[vol.Optional("zero_order_duration", default=PK_DEFAULTS["zero_order_duration"])] = _ZERO_ORDER_DURATION_SELECTOR
            pk_schema[vol.Optional("release_half_life", default=PK_DEFAULTS["release_half_life"])] = _RELEASE_HALF_LIFE_SELECTOR

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
        for key in STANDARD_EFFECTIVENESS_METRICS:
            fields[vol.Optional(f"metric_{key}", default=False)] = sel.BooleanSelector()
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PillLoggerOptionsFlowHandler(config_entry)


class PillLoggerOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._entry = config_entry
        self._data = {}

    async def async_step_init(self, user_input=None):
        """Step 1: Schedule and dosing parameters."""
        if user_input is not None:
            self._data.update(user_input)
            # For Time of Day: collect dose_time_N into dose_times list
            tracking_type = self._entry.data.get("tracking_type")
            if tracking_type == TRACKING_TIME_OF_DAY:
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
            if tracking_type == TRACKING_AS_NEEDED:
                self._data["enable_calendar"] = False
            return await self.async_step_pk()

        tracking_type = self._entry.data.get("tracking_type")
        options = self._entry.options
        data = self._entry.data

        main_schema = {}
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

        # Calendar toggle — only for scheduled tracking types (not As Needed)
        if tracking_type != TRACKING_AS_NEEDED:
            main_schema[vol.Optional("enable_calendar", default=options.get("enable_calendar", data.get("enable_calendar", True)))] = sel.BooleanSelector()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(main_schema),
        )

    async def async_step_pk(self, user_input=None):
        """Step 2: Pharmacokinetic parameters."""
        if user_input is not None:
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
            vol.Optional("lag_time", default=options.get("lag_time", data.get("lag_time", PK_DEFAULTS["lag_time"]))): _LAG_TIME_SELECTOR,
        }

        if release_type == RELEASE_SUSTAINED:
            pk_schema[vol.Optional("ir_hours_to_peak", default=options.get("ir_hours_to_peak", data.get("ir_hours_to_peak", PK_DEFAULTS["ir_hours_to_peak"])))] = _IR_HOURS_TO_PEAK_SELECTOR
            pk_schema[vol.Optional("ir_fraction", default=options.get("ir_fraction", data.get("ir_fraction", PK_DEFAULTS["ir_fraction"])))] = _IR_FRACTION_SELECTOR
            pk_schema[vol.Optional("zero_order_duration", default=options.get("zero_order_duration", data.get("zero_order_duration", PK_DEFAULTS["zero_order_duration"])))] = _ZERO_ORDER_DURATION_SELECTOR
            pk_schema[vol.Optional("release_half_life", default=options.get("release_half_life", data.get("release_half_life", PK_DEFAULTS["release_half_life"])))] = _RELEASE_HALF_LIFE_SELECTOR

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
            # Force adherence off for As Needed — no scheduled doses to track
            if self._entry.data.get("tracking_type") == TRACKING_AS_NEEDED:
                self._data["enable_adherence"] = False
            # OptionsFlow.async_create_entry REPLACES entry.options entirely
            # (it does not merge). self._data accumulates all fields across the
            # 3 steps (init → pk → effectiveness) via .update(), so the
            # complete options dict is submitted here.
            return self.async_create_entry(title="", data=self._data)

        options = self._entry.options
        data = self._entry.data
        tracking_type = data.get("tracking_type")

        fields = {}
        for key in STANDARD_EFFECTIVENESS_METRICS:
            opt_key = f"metric_{key}"
            fields[vol.Optional(opt_key, default=options.get(opt_key, data.get(opt_key, False)))] = sel.BooleanSelector()
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
