import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector as sel
from datetime import date
from .const import DOMAIN, STANDARD_EFFECTIVENESS_METRICS

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
                vol.Required("tracking_type", default="Regular Interval"): vol.In(["Regular Interval", "Time of Day", "As Needed", "Cyclic/Calendar Pattern"]),
            })
        )

    async def async_step_regular_interval(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_effectiveness()

        schema_dict = {
             vol.Required("initial_stock", default=30): int,
             vol.Required("hours_between_doses", default=8): int,
             vol.Required("safe_doses", default=1): int,
             vol.Required("time_window_hours", default=8): int,
              vol.Optional("strength", default=0): vol.Coerce(float),
              vol.Optional("half_life", default=0): vol.Coerce(float),
              vol.Optional("hours_to_peak", default=0.0): vol.Coerce(float),
        }

        return self.async_show_form(
            step_id="regular_interval",
            data_schema=vol.Schema(schema_dict)
        )

    async def async_step_time_of_day(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_effectiveness()

        schema_dict = {
             vol.Required("initial_stock", default=30): int,
             vol.Required("time_of_day", default="08:00"): sel.TimeSelector(),
             vol.Required("safe_doses", default=1): int,
             vol.Required("time_window_hours", default=24): int,
              vol.Optional("strength", default=0): vol.Coerce(float),
              vol.Optional("half_life", default=0): vol.Coerce(float),
              vol.Optional("hours_to_peak", default=0.0): vol.Coerce(float),
        }

        return self.async_show_form(
            step_id="time_of_day",
            data_schema=vol.Schema(schema_dict)
        )

    async def async_step_as_needed(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_effectiveness()

        schema_dict = {
             vol.Required("initial_stock", default=30): int,
             vol.Required("safe_doses", default=2): int,
             vol.Required("time_window_hours", default=8): int,
              vol.Optional("strength", default=0): vol.Coerce(float),
              vol.Optional("half_life", default=0): vol.Coerce(float),
              vol.Optional("hours_to_peak", default=0.0): vol.Coerce(float),
        }

        return self.async_show_form(
            step_id="as_needed",
            data_schema=vol.Schema(schema_dict)
        )

    async def async_step_cyclic(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_effectiveness()

        schema_dict = {
             vol.Required("initial_stock", default=30): int,
             vol.Required("days_on", default=5): int,
             vol.Required("days_off", default=2): int,
             vol.Required("cycle_anchor_date", default=date.today().isoformat()): sel.DateSelector(sel.DateSelectorConfig()),
             vol.Required("dose_time", default="08:00"): sel.TimeSelector(),
             vol.Required("safe_doses", default=1): int,
             vol.Required("time_window_hours", default=24): int,
              vol.Optional("strength", default=0): vol.Coerce(float),
              vol.Optional("half_life", default=0): vol.Coerce(float),
              vol.Optional("hours_to_peak", default=0.0): vol.Coerce(float),
        }

        return self.async_show_form(
            step_id="cyclic",
            data_schema=vol.Schema(schema_dict)
        )

    async def async_step_effectiveness(self, user_input=None):
        """Step 3: Choose which effectiveness metrics to track."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title=self._data["medication_name"], data=self._data)

        fields = {}
        for key in STANDARD_EFFECTIVENESS_METRICS:
            fields[vol.Optional(f"metric_{key}", default=False)] = bool
        fields[vol.Optional("custom_metrics", default="")] = str

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
        """Step 1: Schedule, dosing, and PK parameters."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_effectiveness()

        tracking_type = self._entry.data.get("tracking_type")
        options = self._entry.options
        data = self._entry.data

        schema_dict = {}
        if tracking_type == "Regular Interval":
            schema_dict[vol.Required("hours_between_doses", default=options.get("hours_between_doses", data.get("hours_between_doses", 8)))] = int
            # Default time_window_hours to hours_between_doses if not explicitly set
            tw_default = options.get("time_window_hours", data.get("time_window_hours", data.get("hours_between_doses", 8)))
            schema_dict[vol.Required("time_window_hours", default=tw_default)] = int
        elif tracking_type == "Time of Day":
            schema_dict[vol.Required("time_of_day", default=options.get("time_of_day", data.get("time_of_day", "08:00")))] = sel.TimeSelector()
            schema_dict[vol.Required("time_window_hours", default=options.get("time_window_hours", data.get("time_window_hours", 24)))] = int
        elif tracking_type == "As Needed":
            schema_dict[vol.Required("time_window_hours", default=options.get("time_window_hours", data.get("time_window_hours", 8)))] = int
        elif tracking_type == "Cyclic/Calendar Pattern":
            schema_dict[vol.Required("days_on", default=options.get("days_on", data.get("days_on", 5)))] = int
            schema_dict[vol.Required("days_off", default=options.get("days_off", data.get("days_off", 2)))] = int
            schema_dict[vol.Required("cycle_anchor_date", default=options.get("cycle_anchor_date", data.get("cycle_anchor_date", date.today().isoformat())))] = sel.DateSelector(sel.DateSelectorConfig())
            schema_dict[vol.Required("dose_time", default=options.get("dose_time", data.get("dose_time", "08:00")))] = sel.TimeSelector()
            schema_dict[vol.Required("time_window_hours", default=options.get("time_window_hours", data.get("time_window_hours", 24)))] = int

        schema_dict[vol.Required("safe_doses", default=options.get("safe_doses", data.get("safe_doses", 1)))] = int
        schema_dict[vol.Optional("strength", default=options.get("strength", data.get("strength", 0)))] = vol.Coerce(float)
        schema_dict[vol.Optional("half_life", default=options.get("half_life", data.get("half_life", 0)))] = vol.Coerce(float)
        schema_dict[vol.Optional("hours_to_peak", default=options.get("hours_to_peak", data.get("hours_to_peak", 0.0)))] = vol.Coerce(float)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict)
        )

    async def async_step_effectiveness(self, user_input=None):
        """Step 2: Choose which effectiveness metrics to track."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="", data=self._data)

        options = self._entry.options
        data = self._entry.data

        fields = {}
        for key in STANDARD_EFFECTIVENESS_METRICS:
            opt_key = f"metric_{key}"
            fields[vol.Optional(opt_key, default=options.get(opt_key, data.get(opt_key, False)))] = bool
        fields[vol.Optional("custom_metrics", default=options.get("custom_metrics", data.get("custom_metrics", "")))] = str

        return self.async_show_form(
            step_id="effectiveness",
            data_schema=vol.Schema(fields)
        )