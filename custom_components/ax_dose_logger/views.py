"""
Custom REST endpoint exposing dose history to the frontend.

Provides /api/ax_dose_logger/history/{device_id} which returns the
authoritative, pruned dose_history array from AxDoseLoggerStore.

For Master Tracker devices (Caffeine Tracker / Alcohol Tracker) the endpoint
returns the aggregated master ``dose_history`` (every drink of that substance
across all granular drink devices) so the frontend's 14-day bar graph renders
correctly.  The per-substance store lives in ``store.get_drink_master()``.
"""

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers import device_registry as dr

from .const import (
    ALCOHOL_TRACKER_ID,
    CAFFEINE_TRACKER_ID,
    DOMAIN,
    DRINK_TYPE_ALCOHOL,
    DRINK_TYPE_CAFFEINE,
    LOGGER as _LOGGER,
)

# Map the stable Master Tracker device identifier suffix to its substance.
_TRACKER_SUBSTANCE = {
    CAFFEINE_TRACKER_ID: DRINK_TYPE_CAFFEINE,
    ALCOHOL_TRACKER_ID: DRINK_TYPE_ALCOHOL,
}


class AxDoseLoggerHistoryView(HomeAssistantView):
    """
    Expose dose history via custom REST endpoint.

    URL: /api/ax_dose_logger/history/{device_id}
    Method: GET
    Auth: Bearer token (requires_auth = True)
    Response: JSON array [[iso_timestamp, strength], ...]
    """

    url = "/api/ax_dose_logger/history/{device_id}"
    name = "api:ax_dose_logger:history"
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

        # Master Tracker devices: identifiers carry the stable tracker id
        # (e.g. (DOMAIN, "caffeine_tracker")) rather than an entry_id.  Their
        # aggregated dose history lives in store.get_drink_master(substance),
        # not in the per-entry store history.  Detect the tracker id and
        # return the aggregated master history serialized as
        # [[iso, strength], ...] to match the medicine format the frontend's
        # _bucketByDay expects.
        for identifier in device.identifiers:
            if identifier[0] != DOMAIN:
                continue
            substance = _TRACKER_SUBSTANCE.get(identifier[1])
            if substance is not None:
                master_data = store.get_drink_master(substance)
                doses = master_data.get("doses", [])
                # Master doses are stored as [iso, strength, t_dur_hours];
                # the frontend bar graph only consumes [iso, strength].
                payload = [[d[0], d[1]] for d in doses if len(d) >= 2]
                _LOGGER.info(
                    "ax_dose_logger history REST: master device_id=%s substance=%s "
                    "returned %d doses (store had %d)",
                    device_id, substance, len(payload), len(doses),
                )
                return self.json(payload)

        # Use the first config entry for this device.
        # AX Dose Logger creates one device per config entry (one medication per
        # device), so device.config_entries always has exactly one member.
        # If multi-entry devices are ever supported, this must be revisited.
        entry_id = next(iter(device.config_entries))

        # Get dose history from store
        history = store.get_history(entry_id)
        _LOGGER.info(
            "ax_dose_logger history REST: device_id=%s entry_id=%s returned %d doses",
            device_id, entry_id, len(history),
        )
        return self.json(history)
