"""
Services for the Pill Logger integration.

Exposes the coordinator's dose-management API as HA services so users
can log doses, undo, reset, and manage adherence from automations,
scripts, and dashboards.

Services:
    - ``pill_logger.take_dose``      — log a dose (optional custom timestamp)
    - ``pill_logger.undo_dose``      — revert the most recent dose
    - ``pill_logger.reset``          — clear all dose history
    - ``pill_logger.adherence_reset``  — clear adherence state only
    - ``pill_logger.adherence_override`` — mark last missed slot as taken

All services require an ``entry_id`` field (selected via
``ConfigEntrySelector``) to identify which medication to act on.
"""

from __future__ import annotations

from typing import Final

import homeassistant.util.dt as dt_util
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import selector, service

from .const import DOMAIN
from .coordinator import PillLoggerCoordinator

# Service names
SERVICE_TAKE_DOSE: Final = "take_dose"
SERVICE_UNDO_DOSE: Final = "undo_dose"
SERVICE_RESET: Final = "reset"
SERVICE_ADHERENCE_RESET: Final = "adherence_reset"
SERVICE_ADHERENCE_OVERRIDE: Final = "adherence_override"

# Service field
ATTR_ENTRY_ID: Final = "entry_id"
ATTR_TIMESTAMP: Final = "timestamp"

# Base schema — all services require entry_id via ConfigEntrySelector
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


def _get_coordinator(hass: HomeAssistant, entry_id: str) -> PillLoggerCoordinator:
    """
    Retrieve the coordinator for the given config entry.

    Raises ``HomeAssistantError`` (via ``service.async_get_config_entry``)
    if the entry_id is invalid or not loaded.
    """
    # Validate the entry exists and belongs to our domain
    service.async_get_config_entry(hass, DOMAIN, entry_id)
    return hass.data[DOMAIN][entry_id]["coordinator"]


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


def async_setup_services(hass: HomeAssistant) -> None:
    """
    Register all pill_logger services.

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


def async_unload_services(hass: HomeAssistant) -> None:
    """
    Remove all pill_logger services.

    Called when the last config entry is unloaded.
    """
    for service_name in (
        SERVICE_TAKE_DOSE,
        SERVICE_UNDO_DOSE,
        SERVICE_RESET,
        SERVICE_ADHERENCE_RESET,
        SERVICE_ADHERENCE_OVERRIDE,
    ):
        if hass.services.has_service(DOMAIN, service_name):
            hass.services.async_remove(DOMAIN, service_name)
