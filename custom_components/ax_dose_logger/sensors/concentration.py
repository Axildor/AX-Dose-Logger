
import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback

from ..const import PK_DEFAULTS, RELEASE_INSTANT, RELEASE_SUSTAINED
from ..entity import AxDoseLoggerSensorEntity
from ..pk_model import PKParams


class PillConcentrationSensor(AxDoseLoggerSensorEntity, RestoreSensor):
    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "amount_in_body"
        self._attr_unique_id = f"{entry.entry_id}_concentration"
        self._attr_icon = "mdi:chart-bell-curve"
        self._strength_unit = entry.options.get("strength_unit", entry.data.get("strength_unit", "mg"))
        self._release_type = entry.data.get("release_type", RELEASE_INSTANT)

        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = self._strength_unit
        self._attr_suggested_display_precision = 1
        self._attr_native_value = 0.0
        self._attr_extra_state_attributes = {"last_updated": None, "gut_mass": 0.0, "ka": 0.0, "lag_time": 0.0}

    def _load_pk_params(self):
        """Reload PK parameters + unit from the current config entry."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry:
            self._release_type = entry.data.get("release_type", RELEASE_INSTANT)
            self._strength_unit = entry.options.get("strength_unit", entry.data.get("strength_unit", "mg"))
            self._attr_native_unit_of_measurement = self._strength_unit

    def _build_pk_params(self) -> PKParams:
        """Build a PKParams snapshot from the current config entry."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        opts = entry.options
        data = entry.data
        return PKParams(
            release_type=data.get("release_type", RELEASE_INSTANT),
            strength=float(opts.get("strength", data.get("strength", 0))),
            half_life=float(opts.get("half_life", data.get("half_life", 0))),
            hours_to_peak=float(opts.get("hours_to_peak", data.get("hours_to_peak", 0.0))),
            bioavailability=float(opts.get("bioavailability", data.get("bioavailability", PK_DEFAULTS["bioavailability"]))),
            ir_fraction=float(opts.get("ir_fraction", data.get("ir_fraction", PK_DEFAULTS["ir_fraction"]))),
            zero_order_duration=float(opts.get("zero_order_duration", data.get("zero_order_duration", PK_DEFAULTS["zero_order_duration"]))),
            release_half_life=float(opts.get("release_half_life", data.get("release_half_life", PK_DEFAULTS["release_half_life"]))),
            lag_time=float(opts.get("lag_time", data.get("lag_time", PK_DEFAULTS["lag_time"]))),
            ir_hours_to_peak=float(opts.get("ir_hours_to_peak", data.get("ir_hours_to_peak", PK_DEFAULTS["ir_hours_to_peak"]))),
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._load_pk_params()

        # Legacy restore for smooth UI transition; the coordinator's
        # first refresh (async_config_entry_first_refresh) already loaded
        # dose history from the store and computed PK state, so
        # _handle_coordinator_update will override this immediately.
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            try:
                old_mass = float(last_state.state)
                self._attr_native_value = round(old_mass, 1)
            except (ValueError, TypeError):
                pass

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Handle updated data from the coordinator.

        The coordinator recomputes PK concentration on every refresh
        (dose event or 1-min tick) and stores it in
        ``coordinator.data.concentration`` / ``coordinator.data.pk_result``.
        We read those values and format the attributes here.
        """
        self._load_pk_params()

        if self.coordinator.data and self.coordinator.data.pk_result is not None:
            pk = self.coordinator.data.pk_result
            self._attr_native_value = round(self.coordinator.data.concentration, 1)

            if self._release_type == RELEASE_SUSTAINED:
                self._attr_extra_state_attributes = {
                    "last_updated": dt_util.now().isoformat(),
                    "gut_mass": round(pk.gut_ir, 1),
                    "gut_ir_mass": round(pk.gut_ir, 1),
                    "matrix_sr_mass": round(pk.matrix_sr, 1),
                    "gut_sr_mass": round(pk.gut_sr, 1),
                    "ka": pk.ka,
                    "kr": pk.kr,
                    "lag_time": self._build_pk_params().lag_time,
                }
            else:
                self._attr_extra_state_attributes = {
                    "last_updated": dt_util.now().isoformat(),
                    "gut_mass": round(pk.gut_ir, 1),
                    "ka": pk.ka,
                    "lag_time": self._build_pk_params().lag_time,
                }
        else:
            self._attr_native_value = 0.0
            self._attr_extra_state_attributes = {
                "last_updated": None,
                "gut_mass": 0.0,
                "ka": 0.0,
                "lag_time": 0.0,
            }

        self.async_write_ha_state()
