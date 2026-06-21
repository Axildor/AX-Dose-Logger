"""
Services for the AX Dose Logger integration.

Exposes the coordinator's dose-management API as HA services so users
can log doses, undo, reset, and manage adherence from automations,
scripts, and dashboards.

Services:
    - ``ax_dose_logger.take_dose``      — log a dose (optional custom timestamp)
    - ``ax_dose_logger.undo_dose``      — revert the most recent dose
    - ``ax_dose_logger.reset``          — clear all dose history
    - ``ax_dose_logger.adherence_reset``  — clear adherence state only
    - ``ax_dose_logger.adherence_override`` — mark last missed slot as taken
    - ``ax_dose_logger.set_metric``     — set a daily-locked tracking value

Most services require an ``entry_id`` field (selected via
``ConfigEntrySelector``) to identify which medication to act on.
The ``set_metric`` service accepts an ``entity_id`` field instead, which
is resolved to the coordinator and metric key via the entity registry.
"""

from __future__ import annotations

from typing import Final

import homeassistant.util.dt as dt_util
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, HomeAssistantError
from homeassistant.helpers import entity_registry as er, selector, service

from .const import DOMAIN
from .coordinator import AxDoseLoggerCoordinator

# Service names
SERVICE_TAKE_DOSE: Final = "take_dose"
SERVICE_UNDO_DOSE: Final = "undo_dose"
SERVICE_RESET: Final = "reset"
SERVICE_ADHERENCE_RESET: Final = "adherence_reset"
SERVICE_ADHERENCE_OVERRIDE: Final = "adherence_override"
SERVICE_SET_METRIC: Final = "set_metric"

# Service fields
ATTR_ENTRY_ID: Final = "entry_id"
ATTR_ENTITY_ID: Final = "entity_id"
ATTR_TIMESTAMP: Final = "timestamp"
ATTR_METRIC_KEY: Final = "metric_key"
ATTR_VALUE: Final = "value"
ATTR_OVERRIDE: Final = "override"

# Base schema — most services require entry_id via ConfigEntrySelector
SERVICE_BASE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): selector.ConfigEntrySelector(
            {"integration": DOMAIN}
        ),
    }
)

# take_dose has an optional timestamp field
SERVICE_TAKE_DOSE_SCHEMA = SERVICE_BASE_SCHEMA.extend(
    {
        vol.Optional(ATTR_TIMESTAMP): selector.DateTimeSelector(),
    }
)

# set_metric accepts entity_id (the effectiveness number entity) instead of
# entry_id + metric_key. The handler resolves entity_id to the coordinator
# and metric key via the entity registry and entity state attributes.
SERVICE_SET_METRIC_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="number", integration=DOMAIN)
        ),
        vol.Required(ATTR_VALUE): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=10, step=1, mode=selector.NumberSelectorMode.BOX)
        ),
        vol.Optional(ATTR_OVERRIDE, default=False): selector.BooleanSelector(),
    }
)


def _get_coordinator(hass: HomeAssistant, entry_id: str) -> AxDoseLoggerCoordinator:
    """
    Retrieve the coordinator for the given config entry.

    Raises ``HomeAssistantError`` (via ``service.async_get_config_entry``)
    if the entry_id is invalid or not loaded.
    """
    # Validate the entry exists and belongs to our domain
    service.async_get_config_entry(hass, DOMAIN, entry_id)
    return hass.data[DOMAIN][entry_id]["coordinator"]


def _get_coordinator_for_entity(
    hass: HomeAssistant, entity_id: str
) -> tuple[AxDoseLoggerCoordinator, str]:
    """
    Resolve an effectiveness entity_id to its coordinator and metric key.

    Looks up the entity in the entity registry to find its config_entry_id,
    then retrieves the coordinator. The metric_key is read from the entity's
    state attributes (set by ``PillEffectivenessSlider.extra_state_attributes``).

    Raises ``HomeAssistantError`` if the entity is not found in the registry,
    does not belong to this integration, or has no metric_key attribute.
    """
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    if entry is None:
        raise HomeAssistantError(
            f"Entity '{entity_id}' not found in the entity registry."
        )
    if entry.platform != DOMAIN:
        raise HomeAssistantError(
            f"Entity '{entity_id}' does not belong to the {DOMAIN} integration."
        )
    if entry.config_entry_id is None:
        raise HomeAssistantError(
            f"Entity '{entity_id}' has no associated config entry."
        )

    coordinator = _get_coordinator(hass, entry.config_entry_id)

    # Read metric_key from the entity's state attributes
    state = hass.states.get(entity_id)
    if state is None or "metric_key" not in (state.attributes or {}):
        raise HomeAssistantError(
            f"Entity '{entity_id}' has no metric_key attribute. "
            "Ensure it is an effectiveness tracking entity."
        )
    metric_key = state.attributes["metric_key"]

    return coordinator, metric_key


async def _async_take_dose(call: ServiceCall) -> None:
    """Handle the ``take_dose`` service — log a dose."""
    coordinator = _get_coordinator(call.hass, call.data[ATTR_ENTRY_ID])
    timestamp = None
    if call.data.get(ATTR_TIMESTAMP):
        timestamp = dt_util.parse_datetime(call.data[ATTR_TIMESTAMP])
    await coordinator.async_take_dose(timestamp)


async def _async_undo_dose(call: ServiceCall) -> None:
    """Handle the ``undo_dose`` service — revert the last dose."""
    coordinator = _get_coordinator(call.hass, call.data[ATTR_ENTRY_ID])
    await coordinator.async_undo_dose()


async def _async_reset(call: ServiceCall) -> None:
    """Handle the ``reset`` service — clear all dose history."""
    coordinator = _get_coordinator(call.hass, call.data[ATTR_ENTRY_ID])
    await coordinator.async_reset()


async def _async_adherence_reset(call: ServiceCall) -> None:
    """Handle the ``adherence_reset`` service — clear adherence state only."""
    coordinator = _get_coordinator(call.hass, call.data[ATTR_ENTRY_ID])
    await coordinator.async_adherence_reset()


async def _async_adherence_override(call: ServiceCall) -> None:
    """Handle the ``adherence_override`` service — mark last missed slot as taken."""
    coordinator = _get_coordinator(call.hass, call.data[ATTR_ENTRY_ID])
    await coordinator.async_adherence_override()


async def _async_set_metric(call: ServiceCall) -> None:
    """Handle the ``set_metric`` service — set a daily-locked tracking value.

    Accepts ``entity_id`` (the effectiveness number entity) instead of
    ``entry_id`` + ``metric_key``. The handler resolves the entity to its
    coordinator and metric key via the entity registry and state attributes.
    """
    entity_id = call.data[ATTR_ENTITY_ID]
    value = float(call.data[ATTR_VALUE])
    override = call.data.get(ATTR_OVERRIDE, False)
    coordinator, metric_key = _get_coordinator_for_entity(call.hass, entity_id)
    await coordinator.async_set_metric(metric_key, value, override=override)


def async_setup_services(hass: HomeAssistant) -> None:
    """
    Register all ax_dose_logger services.

    Called once from ``async_setup_entry``. Services are domain-level
    (not per-entry), so they are registered only if not already registered
    (idempotent for multi-entry setups).
    """
    if hass.services.has_service(DOMAIN, SERVICE_TAKE_DOSE):
        return

    # pylint: disable-next=home-assistant-service-registered-in-setup-entry
    hass.services.async_register(
        DOMAIN, SERVICE_TAKE_DOSE, _async_take_dose, schema=SERVICE_TAKE_DOSE_SCHEMA
    )
    # pylint: disable-next=home-assistant-service-registered-in-setup-entry
    hass.services.async_register(
        DOMAIN, SERVICE_UNDO_DOSE, _async_undo_dose, schema=SERVICE_BASE_SCHEMA
    )
    # pylint: disable-next=home-assistant-service-registered-in-setup-entry
    hass.services.async_register(
        DOMAIN, SERVICE_RESET, _async_reset, schema=SERVICE_BASE_SCHEMA
    )
    # pylint: disable-next=home-assistant-service-registered-in-setup-entry
    hass.services.async_register(
        DOMAIN, SERVICE_ADHERENCE_RESET, _async_adherence_reset, schema=SERVICE_BASE_SCHEMA
    )
    # pylint: disable-next=home-assistant-service-registered-in-setup-entry
    hass.services.async_register(
        DOMAIN, SERVICE_ADHERENCE_OVERRIDE, _async_adherence_override, schema=SERVICE_BASE_SCHEMA
    )
    # pylint: disable-next=home-assistant-service-registered-in-setup-entry
    hass.services.async_register(
        DOMAIN, SERVICE_SET_METRIC, _async_set_metric, schema=SERVICE_SET_METRIC_SCHEMA
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """
    Remove all ax_dose_logger services.

    Called when the last config entry is unloaded.
    """
    for service_name in (
        SERVICE_TAKE_DOSE,
        SERVICE_UNDO_DOSE,
        SERVICE_RESET,
        SERVICE_ADHERENCE_RESET,
        SERVICE_ADHERENCE_OVERRIDE,
        SERVICE_SET_METRIC,
    ):
        if hass.services.has_service(DOMAIN, service_name):
            hass.services.async_remove(DOMAIN, service_name)
