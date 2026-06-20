from homeassistant.components.number import RestoreNumber, NumberEntity, NumberMode
from homeassistant.core import HomeAssistant, CALLBACK_TYPE, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from .const import DOMAIN, STANDARD_EFFECTIVENESS_METRICS, EFFECTIVENESS_METRIC_ICONS, DEFAULT_METRIC_ICON, sanitize_key
from .coordinator import PillLoggerCoordinator
from .data import PillLoggerConfigEntry
from .entity import PillLoggerEntity

async def async_setup_entry(
    hass: HomeAssistant,
    entry: PillLoggerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    initial_stock = entry.data["initial_stock"]
    entities = [
        PillStockNumber(entry, coordinator, initial_stock),
        PillAddStockNumber(entry, coordinator),
    ]

    # Read effectiveness metrics from options, falling back to entry data
    options = entry.options
    data = entry.data

    for key, label in STANDARD_EFFECTIVENESS_METRICS.items():
        opt_key = f"metric_{key}"
        if options.get(opt_key, data.get(opt_key, False)):
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

class PillStockNumber(PillLoggerEntity, RestoreNumber):
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
        """Handle coordinator updates — decrement on dose taken, increment on undo.

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

class PillAddStockNumber(PillLoggerEntity, NumberEntity):
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

class PillEffectivenessSlider(PillLoggerEntity, RestoreNumber):
    """Number entity representing a 1-10 subjective effectiveness metric for a medication."""

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator, metric_key: str, metric_label: str, icon: str):
        super().__init__(entry, coordinator)
        self._metric_key = metric_key
        self._attr_name = f"{metric_label} Effectiveness"
        self._attr_unique_id = f"{entry.entry_id}_eff_{metric_key}"
        self._attr_icon = icon
        self._attr_native_value = 1.0
        self._attr_native_step = 1.0
        self._attr_native_min_value = 1.0
        self._attr_native_max_value = 10.0
        self._attr_mode = NumberMode.SLIDER

    async def async_added_to_hass(self):
        """Restore last value on restart."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_number_data()
        if last_state and last_state.native_value is not None:
            self._attr_native_value = last_state.native_value

    async def async_set_native_value(self, value: float):
        self._attr_native_value = value
        self.async_write_ha_state()
