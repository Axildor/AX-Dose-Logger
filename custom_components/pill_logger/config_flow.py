import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import callback
from homeassistant.helpers import selector as sel
from datetime import date
from .const import DOMAIN, STANDARD_EFFECTIVENESS_METRICS

# Section key for adherence (still used as a section)
_ADHERENCE_SECTION_KEY = "adherence"

# Reusable selector configs — BOX mode for all numeric fields
_STOCK_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=9999, step=1, unit_of_measurement="pills", mode=sel.NumberSelectorMode.BOX
))
_HOURS_BETWEEN_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=1, max=48, step=1, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_SAFE_DOSES_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=1, max=20, step=1, unit_of_measurement="doses", mode=sel.NumberSelectorMode.BOX
))
_TIME_WINDOW_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0.5, max=168, step=0.5, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_STRENGTH_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=9999, step=0.5, unit_of_measurement="mg", mode=sel.NumberSelectorMode.BOX
))
_HALF_LIFE_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=168, step=0.5, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_HOURS_TO_PEAK_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0, max=72, step=0.1, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
))
_DAYS_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=1, max=30, step=1, unit_of_measurement="days", mode=sel.NumberSelectorMode.BOX
))
_ADHERENCE_GRACE_SELECTOR = sel.NumberSelector(sel.NumberSelectorConfig(
    min=0.5, max=24, step=0.5, unit_of_measurement="h", mode=sel.NumberSelectorMode.BOX
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
    VERSION = 2

    def __init__(self):
        self._data = {}

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            if user_input["tracking_type"] == "Regular Interval":
                return await self.async_step_regular_interval()
            elif user_input["tracking_type"] == "Time of Day":
                return await self.async_step_time_of_day()
            elif user_input["tracking_type"] == "Cyclic/Calendar Pattern":
                return await self.async_step_cyclic()
            else:
                return await self.async_step_as_needed()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("medication_name", default="My Medication"): str,
                vol.Required("tracking_type", default="Regular Interval"): sel.SelectSelector(
                    sel.SelectSelectorConfig(
                        options=["Regular Interval", "Time of Day", "As Needed", "Cyclic/Calendar Pattern"],
                        mode=sel.SelectSelectorMode.DROPDOWN,
                        translation_key="tracking_type",
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
                vol.Required("safe_doses", default=1): _SAFE_DOSES_SELECTOR,
                vol.Required("time_window_hours", default=8): _TIME_WINDOW_SELECTOR,
                vol.Optional("enable_calendar", default=True): sel.BooleanSelector(),
            }),
        )

    async def async_step_time_of_day(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_pk()

        return self.async_show_form(
            step_id="time_of_day",
            data_schema=vol.Schema({
                vol.Required("initial_stock", default=30): _STOCK_SELECTOR,
                vol.Required("time_of_day", default="08:00"): sel.TimeSelector(),
                vol.Required("safe_doses", default=1): _SAFE_DOSES_SELECTOR,
                vol.Required("time_window_hours", default=24): _TIME_WINDOW_SELECTOR,
                vol.Optional("enable_calendar", default=True): sel.BooleanSelector(),
            }),
        )

    async def async_step_as_needed(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_pk()

        return self.async_show_form(
            step_id="as_needed",
            data_schema=vol.Schema({
                vol.Required("initial_stock", default=30): _STOCK_SELECTOR,
                vol.Required("safe_doses", default=2): _SAFE_DOSES_SELECTOR,
                vol.Required("time_window_hours", default=8): _TIME_WINDOW_SELECTOR,
                vol.Optional("enable_calendar", default=True): sel.BooleanSelector(),
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
                vol.Required("safe_doses", default=1): _SAFE_DOSES_SELECTOR,
                vol.Required("time_window_hours", default=24): _TIME_WINDOW_SELECTOR,
                vol.Optional("enable_calendar", default=True): sel.BooleanSelector(),
            }),
        )

    async def async_step_pk(self, user_input=None):
        """Step 3: Pharmacokinetic parameters for concentration tracking."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_effectiveness()

        return self.async_show_form(
            step_id="pk",
            data_schema=vol.Schema({
                vol.Optional("strength", default=0): _STRENGTH_SELECTOR,
                vol.Optional("half_life", default=0): _HALF_LIFE_SELECTOR,
                vol.Optional("hours_to_peak", default=0): _HOURS_TO_PEAK_SELECTOR,
            }),
        )

    async def async_step_effectiveness(self, user_input=None):
        """Step 4: Choose which effectiveness metrics to track and adherence settings."""
        if user_input is not None:
            adherence_data = user_input.pop(_ADHERENCE_SECTION_KEY, {})
            user_input.update(adherence_data)
            self._data.update(user_input)
            return self.async_create_entry(title=self._data["medication_name"], data=self._data)

        tracking_type = self._data.get("tracking_type")
        default_adherence = tracking_type != "As Needed"

        fields = {}
        for key in STANDARD_EFFECTIVENESS_METRICS:
            fields[vol.Optional(f"metric_{key}", default=False)] = sel.BooleanSelector()
        fields[vol.Optional("custom_metrics", default="")] = sel.TextSelector()
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
            return await self.async_step_pk()

        tracking_type = self._entry.data.get("tracking_type")
        options = self._entry.options
        data = self._entry.data

        main_schema = {}
        if tracking_type == "Regular Interval":
            main_schema[vol.Required("hours_between_doses", default=options.get("hours_between_doses", data.get("hours_between_doses", 8)))] = _HOURS_BETWEEN_SELECTOR
            # Default time_window_hours to hours_between_doses if not explicitly set
            tw_default = options.get("time_window_hours", data.get("time_window_hours", data.get("hours_between_doses", 8)))
            main_schema[vol.Required("time_window_hours", default=tw_default)] = _TIME_WINDOW_SELECTOR
        elif tracking_type == "Time of Day":
            main_schema[vol.Required("time_of_day", default=options.get("time_of_day", data.get("time_of_day", "08:00")))] = sel.TimeSelector()
            main_schema[vol.Required("time_window_hours", default=options.get("time_window_hours", data.get("time_window_hours", 24)))] = _TIME_WINDOW_SELECTOR
        elif tracking_type == "As Needed":
            main_schema[vol.Required("time_window_hours", default=options.get("time_window_hours", data.get("time_window_hours", 8)))] = _TIME_WINDOW_SELECTOR
        elif tracking_type == "Cyclic/Calendar Pattern":
            main_schema[vol.Required("days_on", default=options.get("days_on", data.get("days_on", 5)))] = _DAYS_SELECTOR
            main_schema[vol.Required("days_off", default=options.get("days_off", data.get("days_off", 2)))] = _DAYS_SELECTOR
            main_schema[vol.Required("cycle_anchor_date", default=options.get("cycle_anchor_date", data.get("cycle_anchor_date", date.today().isoformat())))] = sel.DateSelector(sel.DateSelectorConfig())
            main_schema[vol.Required("dose_time", default=options.get("dose_time", data.get("dose_time", "08:00")))] = sel.TimeSelector()
            main_schema[vol.Required("time_window_hours", default=options.get("time_window_hours", data.get("time_window_hours", 24)))] = _TIME_WINDOW_SELECTOR

        main_schema[vol.Required("safe_doses", default=options.get("safe_doses", data.get("safe_doses", 1)))] = _SAFE_DOSES_SELECTOR

        # Calendar toggle
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

        return self.async_show_form(
            step_id="pk",
            data_schema=vol.Schema({
                vol.Optional("strength", default=options.get("strength", data.get("strength", 0))): _STRENGTH_SELECTOR,
                vol.Optional("half_life", default=options.get("half_life", data.get("half_life", 0))): _HALF_LIFE_SELECTOR,
                vol.Optional("hours_to_peak", default=options.get("hours_to_peak", data.get("hours_to_peak", 0))): _HOURS_TO_PEAK_SELECTOR,
            }),
        )

    async def async_step_effectiveness(self, user_input=None):
        """Step 3: Choose which effectiveness metrics to track and adherence settings."""
        if user_input is not None:
            adherence_data = user_input.pop(_ADHERENCE_SECTION_KEY, {})
            user_input.update(adherence_data)
            self._data.update(user_input)
            return self.async_create_entry(title="", data=self._data)

        options = self._entry.options
        data = self._entry.data
        tracking_type = data.get("tracking_type")

        fields = {}
        for key in STANDARD_EFFECTIVENESS_METRICS:
            opt_key = f"metric_{key}"
            fields[vol.Optional(opt_key, default=options.get(opt_key, data.get(opt_key, False)))] = sel.BooleanSelector()
        fields[vol.Optional("custom_metrics", default=options.get("custom_metrics", data.get("custom_metrics", "")))] = sel.TextSelector()

        # Adherence section with loaded defaults
        fields[vol.Required(_ADHERENCE_SECTION_KEY)] = _make_adherence_section(
            enable_default=options.get("enable_adherence", data.get("enable_adherence", tracking_type != "As Needed")),
            grace_default=options.get("adherence_grace_hours", data.get("adherence_grace_hours", 1)),
        )

        return self.async_show_form(
            step_id="effectiveness",
            data_schema=vol.Schema(fields)
        )