"""Custom REST endpoint exposing dose history to the frontend.

Provides /api/pill_logger/history/{device_id} which returns the
authoritative, pruned dose_history array from PillLoggerStore.
"""

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN


class PillLoggerHistoryView(HomeAssistantView):
    """Expose dose history via custom REST endpoint.

    URL: /api/pill_logger/history/{device_id}
    Method: GET
    Auth: Bearer token (requires_auth = True)
    Response: JSON array [[iso_timestamp, strength], ...]
    """

    url = "/api/pill_logger/history/{device_id}"
    name = "api:pill_logger:history"
    requires_auth = True

    async def get(self, request: web.Request, device_id: str) -> web.Response:
        """Return dose history for the given device."""
        hass = request.app["hass"]

        # Get the store from hass.data
        store = hass.data.get(DOMAIN, {}).get("_store")
        if not store:
            return self.json([])

        # Map device_id to config entry_id via device registry
        device_reg = dr.async_get(hass)
        device = device_reg.async_get(device_id)
        if not device or not device.config_entries:
            return self.json([])

        # Use the first config entry for this device.
        # Pill Logger creates one device per config entry (one medication per
        # device), so device.config_entries always has exactly one member.
        # If multi-entry devices are ever supported, this must be revisited.
        entry_id = next(iter(device.config_entries))

        # Get dose history from store
        history = store.get_history(entry_id)
        return self.json(history)
