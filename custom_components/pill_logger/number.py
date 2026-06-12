from homeassistant.components.number import RestoreNumber, NumberEntity, NumberMode
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later
from .const import DOMAIN, STANDARD_EFFECTIVENESS_METRICS, EFFECTIVENESS_METRIC_ICONS, DEFAULT_METRIC_ICON, sanitize_key

async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    med_name = entry.data["medication_name"]
    initial_stock = entry.data["initial_stock"]
    entities = [
        PillStockNumber(med_name, entry.entry_id, initial_stock),
        PillAddStockNumber(med_name, entry.entry_id),
    ]

    # Read effectiveness metrics from options, falling back to entry data
    options = entry.options
    data = entry.data

    for key, label in STANDARD_EFFECTIVENESS_METRICS.items():
        opt_key = f"metric_{key}"
        if options.get(opt_key, data.get(opt_key, False)):
            icon = EFFECTIVENESS_METRIC_ICONS.get(key, DEFAULT_METRIC_ICON)
            entities.append(PillEffectivenessSlider(med_name, entry.entry_id, key, label, icon))

    custom_str = options.get("custom_metrics", data.get("custom_metrics", ""))
    if custom_str:
        for raw in custom_str.split(","):
            name = raw.strip()
            if name:
                skey = sanitize_key(name)
                entities.append(PillEffectivenessSlider(
                    med_name, entry.entry_id, f"custom_{skey}", name, DEFAULT_METRIC_ICON
                ))

    async_add_entities(entities)

class PillStockNumber(RestoreNumber):
    _attr_has_entity_name = True
    should_poll = False

    def __init__(self, name, entry_id, initial_stock):
        self._med_name = name
        self._attr_name = "Pills Left"
        self._attr_unique_id = f"{entry_id}_stock"
        self._attr_icon = "mdi:medical-bag"
        self._entry_id = entry_id
        self._attr_native_value = float(initial_stock)
        self._attr_native_step = 1.0
        self._attr_native_min_value = 0.0
        self._attr_native_max_value = 5000.0
        self._attr_mode = NumberMode.BOX

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_taken_{self._entry_id}", self.decrement)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_add_stock_{self._entry_id}", self.add_stock)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"pill_undone_{self._entry_id}", self.increment_undo)
        )
        last_state = await self.async_get_last_number_data()
        if last_state and last_state.native_value is not None:
            self._attr_native_value = last_state.native_value

    async def async_set_native_value(self, value: float):
        self._attr_native_value = value
        self.async_write_ha_state()

    @callback
    def decrement(self, *args, **kwargs):
        if self._attr_native_value > 0:
            self._attr_native_value -= 1
            self.async_write_ha_state()

    @callback
    def add_stock(self, amount: float, *args, **kwargs):
        self._attr_native_value += amount
        self.async_write_ha_state()

    @callback
    def increment_undo(self, *args, **kwargs):
        """Increment inventory by 1 when a dose is undone."""
        self._attr_native_value += 1
        self.async_write_ha_state()

class PillAddStockNumber(NumberEntity):
    _attr_has_entity_name = True
    should_poll = False

    def __init__(self, name, entry_id):
        self._med_name = name
        self._attr_name = "Add Refill"
        self._attr_unique_id = f"{entry_id}_add_stock"
        self._attr_icon = "mdi:plus-box"
        self._entry_id = entry_id
        self._attr_native_value = 0.0
        self._attr_native_step = 1.0
        self._attr_native_min_value = 0.0
        self._attr_mode = NumberMode.BOX

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )

    async def async_set_native_value(self, value: float):
        if value > 0:
            # 1. Update this entity's state so HA registers a change
            self._attr_native_value = value
            self.async_write_ha_state()

            # 2. Tell the main inventory to add the stock
            async_dispatcher_send(self.hass, f"pill_add_stock_{self._entry_id}", value)

            # 3. Schedule a reset to 0 after a short delay (non-blocking)
            self.async_on_remove(
                async_call_later(
                    self.hass,
                    0.5,
                    self._reset_add_stock,
                )
            )

    @callback
    def _reset_add_stock(self, _now):
        self._attr_native_value = 0.0
        self.async_write_ha_state()

class PillEffectivenessSlider(RestoreNumber):
    """Number entity representing a 1-10 subjective effectiveness metric for a medication."""

    _attr_has_entity_name = True
    should_poll = False

    def __init__(self, med_name: str, entry_id: str, metric_key: str, metric_label: str, icon: str):
        self._med_name = med_name
        self._metric_key = metric_key
        self._attr_name = f"{metric_label} Effectiveness"
        self._attr_unique_id = f"{entry_id}_eff_{metric_key}"
        self._attr_icon = icon
        self._entry_id = entry_id
        self._attr_native_value = 1.0
        self._attr_native_step = 1.0
        self._attr_native_min_value = 1.0
        self._attr_native_max_value = 10.0
        self._attr_mode = NumberMode.SLIDER

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._med_name,
            manufacturer="Pill Logger",
        )

    async def async_added_to_hass(self):
        """Restore last value on restart."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_number_data()
        if last_state and last_state.native_value is not None:
            self._attr_native_value = last_state.native_value

    async def async_set_native_value(self, value: float):
        self._attr_native_value = value
        self.async_write_ha_state()
