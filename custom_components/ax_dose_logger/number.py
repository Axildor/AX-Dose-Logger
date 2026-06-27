from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
import homeassistant.util.dt as dt_util

from .const import (
    DEFAULT_METRIC_ICON,
    DEVICE_CATEGORY_DRINKS,
    DOMAIN,
    EFFECTIVENESS_METRIC_ICONS,
    STANDARD_EFFECTIVENESS_METRICS,
    sanitize_key,
)
from .data import AxDoseLoggerConfigEntry
from .entity import AxDoseLoggerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AxDoseLoggerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    category = entry.data.get("device_category")

    if category == DEVICE_CATEGORY_DRINKS:
        await _setup_drink_numbers(hass, entry, async_add_entities)
        return

    # --- Medicine (legacy) ---
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    initial_stock = entry.data["initial_stock"]
    entities = [
        PillStockNumber(entry, coordinator, initial_stock),
        PillAddStockNumber(entry, coordinator),
    ]

    # Read effectiveness metrics from options, falling back to entry data
    options = entry.options
    data = entry.data

    tracked = options.get("tracked_symptoms", data.get("tracked_symptoms", []))
    for key, label in STANDARD_EFFECTIVENESS_METRICS.items():
        if key in tracked:
            icon = EFFECTIVENESS_METRIC_ICONS.get(key, DEFAULT_METRIC_ICON)
            entities.append(PillEffectivenessSlider(entry, coordinator, key, label, icon))

    custom_str = options.get("custom_metrics", data.get("custom_metrics", ""))
    if custom_str:
        for raw in custom_str.split(","):
            name = raw.strip()
            if name:
                skey = sanitize_key(name)
                entities.append(PillEffectivenessSlider(
                    entry, coordinator, f"custom_{skey}", name, DEFAULT_METRIC_ICON
                ))

    async_add_entities(entities)


async def _setup_drink_numbers(
    hass: HomeAssistant,
    entry: AxDoseLoggerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Granular drink device inventory number entities — mirror medicine stock.

    Each drink gets an Inventory counter (using the configured
    ``unit_of_measurement``) that decrements by 1 on each Log Drink press
    (mirrors medicine's one-pill-per-take), plus a disposable Add Stock input
    that fires a ``drink_add_stock_{entry_id}`` signal to refill the counter.
    """
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    initial_stock = entry.data.get("initial_stock", 0)
    async_add_entities([
        DrinkStockNumber(entry, coordinator, initial_stock),
        DrinkAddStockNumber(entry, coordinator),
    ])

class PillStockNumber(AxDoseLoggerEntity, RestoreNumber):
    _attr_has_entity_name = True

    def __init__(self, entry, coordinator, initial_stock):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "pills_left"
        self._attr_unique_id = f"{entry.entry_id}_stock"
        self._attr_icon = "mdi:medical-bag"
        self._attr_native_value = float(initial_stock)
        self._attr_native_step = 1.0
        self._attr_native_min_value = 0.0
        self._attr_native_max_value = 5000.0
        self._attr_mode = NumberMode.BOX

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Listen to legacy dispatcher signals for stock-specific events
        # (pill_add_stock). Dose taken/undo are handled via coordinator
        # updates in _handle_coordinator_update.
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_add_stock_{self._entry_id}", self.add_stock)
        )
        last_state = await self.async_get_last_number_data()
        if last_state and last_state.native_value is not None:
            self._attr_native_value = last_state.native_value

    async def async_set_native_value(self, value: float):
        self._attr_native_value = value
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Handle coordinator updates — decrement on dose taken, increment on undo.

        We detect take vs undo by comparing the dose_history length to our
        last-seen count. This avoids needing separate dispatcher signals.
        """
        if not self.coordinator.data:
            return
        current_count = len(self.coordinator.data.dose_history)
        if not hasattr(self, "_last_dose_count"):
            self._last_dose_count = current_count
            return
        if current_count > self._last_dose_count:
            # Dose taken — decrement
            if self._attr_native_value > 0:
                self._attr_native_value -= 1
        elif current_count < self._last_dose_count:
            # Dose undone — increment
            self._attr_native_value += 1
        self._last_dose_count = current_count
        self.async_write_ha_state()

    @callback
    def add_stock(self, amount: float, *args, **kwargs):
        self._attr_native_value += amount
        self.async_write_ha_state()

class PillAddStockNumber(AxDoseLoggerEntity, NumberEntity):
    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "add_refill"
        self._attr_unique_id = f"{entry.entry_id}_add_stock"
        self._attr_icon = "mdi:plus-box"
        self._attr_native_value = 0.0
        self._attr_native_step = 1.0
        self._attr_native_min_value = 0.0
        self._attr_mode = NumberMode.BOX
        self._reset_timer: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        """Register cleanup for the reset timer on entity removal."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_reset_timer)

    @callback
    def _cancel_reset_timer(self) -> None:
        """Cancel any pending reset timer."""
        if self._reset_timer:
            self._reset_timer()
            self._reset_timer = None

    async def async_set_native_value(self, value: float):
        if value > 0:
            self._attr_native_value = value
            self.async_write_ha_state()

            # Tell the main inventory to add the stock via legacy signal
            async_dispatcher_send(self.hass, f"pill_add_stock_{self._entry_id}", value)

            # Cancel any previous reset timer before scheduling a new one
            self._cancel_reset_timer()
            self._reset_timer = async_call_later(
                self.hass,
                0.5,
                self._reset_add_stock,
            )

    @callback
    def _reset_add_stock(self, _now):
        self._attr_native_value = 0.0
        self._reset_timer = None
        self.async_write_ha_state()

class PillEffectivenessSlider(AxDoseLoggerEntity, NumberEntity):
    """
    Daily-locked effectiveness metric slider.

    State is ``unknown`` (None) until the user actively logs a value for the
    current day.  Once set, the value is locked — further changes require an
    override (via the ``ax_dose_logger.set_metric`` service with
    ``override=true``).  At midnight, the coordinator clears all metric
    values and the state resets to ``unknown``.

    Extra state attributes:
      - ``logged_today`` (bool): whether the metric has been set today
      - ``last_logged_date`` (str | None): ISO date string of the last log
    """

    _attr_has_entity_name = True
    _attr_native_step = 1.0
    _attr_native_min_value = 0.0
    _attr_native_max_value = 10.0
    _attr_mode = NumberMode.SLIDER

    def __init__(self, entry, coordinator, metric_key: str, metric_label: str, icon: str):
        super().__init__(entry, coordinator)
        self._metric_key = metric_key
        self._attr_name = f"{metric_label} Effectiveness"
        self._attr_unique_id = f"{entry.entry_id}_eff_{metric_key}"
        self._attr_icon = icon
        # Start as None (unknown) — coordinator will provide the real value
        self._attr_native_value: float | None = None

    @property
    def extra_state_attributes(self) -> dict[str, bool | str | None]:
        """Return whether the metric has been logged today and when."""
        base_attrs: dict[str, bool | str | None] = {
            "logged_today": False,
            "last_logged_date": None,
            "metric_key": self._metric_key,
            "metric_label": self._attr_name.replace(" Effectiveness", ""),
        }
        if not self.coordinator.data:
            return base_attrs
        metric_entry = self.coordinator.data.metric_values.get(self._metric_key)
        today = dt_util.now().date().isoformat()
        if metric_entry and metric_entry.get("date") == today:
            base_attrs["logged_today"] = True
            base_attrs["last_logged_date"] = metric_entry.get("date")
        return base_attrs

    async def async_added_to_hass(self):
        """Register for coordinator updates (no RestoreNumber — coordinator is source of truth)."""
        await super().async_added_to_hass()
        # Sync initial value from coordinator
        self._sync_from_coordinator()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Read metric value from coordinator data and update state."""
        self._sync_from_coordinator()
        self.async_write_ha_state()

    def _sync_from_coordinator(self) -> None:
        """Sync native_value from coordinator metric data."""
        if not self.coordinator.data:
            return
        value = self.coordinator.get_metric_value(self._metric_key)
        if value is not None:
            self._attr_native_value = value
        else:
            self._attr_native_value = None

    async def async_set_native_value(self, value: float):
        """
        Set the metric value via the coordinator's daily-lock API.

        If the metric has already been logged today, the coordinator will
        raise ``HomeAssistantError`` which propagates to the HA UI as an
        error toast.  The frontend card handles this by showing a warning
        dialog with an Override button that calls the ``set_metric`` service
        with ``override=true``.
        """
        await self.coordinator.async_set_metric(
            self._metric_key, value, override=False
        )


# =====================================================================
# Drink number entities (granular drink devices)
# =====================================================================
class DrinkStockNumber(AxDoseLoggerEntity, RestoreNumber):
    """Current drink inventory (e.g. Cups/Cans/Bottles Left).

    Mirrors :class:`PillStockNumber` but uses the drink's configured
    ``unit_of_measurement`` and decrements by 1 on each Log Drink press
    (each press represents consuming one unit, matching medicine's
    one-pill-per-take).  Restores last value on restart.
    """

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator, initial_stock):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "drink_stock"
        self._attr_unique_id = f"{entry.entry_id}_drink_stock"
        self._attr_icon = "mdi:cup-outline"
        self._attr_native_value = float(initial_stock)
        self._attr_native_step = 1.0
        self._attr_native_min_value = 0.0
        self._attr_native_max_value = 9999.0
        self._attr_native_unit_of_measurement = entry.data.get(
            "unit_of_measurement", entry.data.get("unit_of_measurement")
        )
        self._attr_mode = NumberMode.BOX

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"drink_add_stock_{self._entry_id}",
                self.add_stock,
            )
        )
        last_state = await self.async_get_last_number_data()
        if last_state and last_state.native_value is not None:
            self._attr_native_value = last_state.native_value

    async def async_set_native_value(self, value: float):
        self._attr_native_value = value
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Decrement on log_drink, increment on undo (dose_history length)."""
        if not self.coordinator.data:
            return
        current_count = len(self.coordinator.data.dose_history)
        if not hasattr(self, "_last_dose_count"):
            self._last_dose_count = current_count
            return
        if current_count > self._last_dose_count:
            # Drink logged — decrement by 1 (one unit per log press)
            if self._attr_native_value > 0:
                self._attr_native_value -= 1
        elif current_count < self._last_dose_count:
            # Drink undone — increment
            self._attr_native_value += 1
        self._last_dose_count = current_count
        self.async_write_ha_state()

    @callback
    def add_stock(self, amount: float, *args, **kwargs):
        self._attr_native_value += amount
        self.async_write_ha_state()


class DrinkAddStockNumber(AxDoseLoggerEntity, NumberEntity):
    """Disposable input to add stock to a granular drink's inventory.

    Mirrors :class:`PillAddStockNumber`: on submit it fires a
    ``drink_add_stock_{entry_id}`` signal (consumed by
    :class:`DrinkStockNumber.add_stock`) and auto-resets to 0 after 0.5 s.
    """

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator):
        super().__init__(entry, coordinator)
        self._attr_translation_key = "drink_add_refill"
        self._attr_unique_id = f"{entry.entry_id}_drink_add_stock"
        self._attr_icon = "mdi:plus-box"
        self._attr_native_value = 0.0
        self._attr_native_step = 1.0
        self._attr_native_min_value = 0.0
        self._attr_native_unit_of_measurement = entry.data.get(
            "unit_of_measurement", entry.data.get("unit_of_measurement")
        )
        self._attr_mode = NumberMode.BOX
        self._reset_timer: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_reset_timer)

    @callback
    def _cancel_reset_timer(self) -> None:
        if self._reset_timer:
            self._reset_timer()
            self._reset_timer = None

    async def async_set_native_value(self, value: float):
        if value > 0:
            self._attr_native_value = value
            self.async_write_ha_state()
            async_dispatcher_send(
                self.hass, f"drink_add_stock_{self._entry_id}", value
            )
            self._cancel_reset_timer()
            self._reset_timer = async_call_later(
                self.hass,
                0.5,
                self._reset_add_stock,
            )

    @callback
    def _reset_add_stock(self, _now):
        self._attr_native_value = 0.0
        self._reset_timer = None
        self.async_write_ha_state()
