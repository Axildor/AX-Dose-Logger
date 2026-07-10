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
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    ALCOHOL_TRACKER_ID,
    CAFFEINE_TRACKER_ID,
    DOMAIN,
    DRINK_TYPE_ALCOHOL,
    DRINK_TYPE_CAFFEINE,
)
from .const import (
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


class AxDoseLoggerPredictLowView(HomeAssistantView):
    """Predict the Low-band wall-clock time if a drink were logged now.

    URL: /api/ax_dose_logger/predict_low?entity_id=<button.log_drink_entity_id>
    Method: GET
    Auth: Bearer token (requires_auth = True)
    Response: JSON ``{"low_time": iso_string | null}``

    Resolves the log-drink button entity to its granular drink config entry,
    reads ``dose_strength`` + ``drinking_duration`` from the entry, finds the
    matching :class:`DrinkMasterCoordinator` by ``drink_type``, and calls its
    pure what-if :meth:`predict_low_time_if_dose`.  The coordinator state is
    never mutated — this is a read-only prediction for the Log Drink popup.
    """

    url = "/api/ax_dose_logger/predict_low"
    name = "api:ax_dose_logger:predict_low"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Return the predicted Low-band timestamp for the given drink button.

        The PK what-if (``predict_low_time_if_dose``) is a synchronous,
        CPU-bound full-history recompute, so it is offloaded to the executor
        to avoid blocking the event loop.  Any unexpected exception is
        caught and returned as ``{"low_time": null}`` so the frontend always
        receives a 200 and renders ``Low: —`` instead of hanging on the
        ``Low: …`` loading placeholder that a 500 would produce.
        """
        hass = request.app["hass"]
        entity_id = request.query.get("entity_id")
        if not entity_id:
            return self.json({"low_time": None})

        try:
            # Resolve entity_id -> config entry via the entity registry.
            ent_reg = er.async_get(hass)
            entry = ent_reg.async_get(entity_id)
            if entry is None or not entry.config_entry_id:
                _LOGGER.info(
                    "ax_dose_logger predict_low REST: entity %s not in registry",
                    entity_id,
                )
                return self.json({"low_time": None})

            config_entry: ConfigEntry | None = hass.config_entries.async_get_entry(
                entry.config_entry_id
            )
            if config_entry is None:
                return self.json({"low_time": None})

            # Only granular drink entries carry dose_strength + drinking_duration.
            if config_entry.data.get("device_category") != "drinks":
                return self.json({"low_time": None})

            substance = config_entry.data.get("drink_type")
            masters = hass.data.get(DOMAIN, {}).get("_drink_masters", {})
            coordinator = masters.get(substance)
            if coordinator is None:
                _LOGGER.info(
                    "ax_dose_logger predict_low REST: no master coordinator for %s",
                    substance,
                )
                return self.json({"low_time": None})

            dose_strength = float(
                config_entry.options.get(
                    "dose_strength",
                    config_entry.data.get("dose_strength", 0),
                )
            )
            drinking_duration_min = float(
                config_entry.options.get(
                    "drinking_duration",
                    config_entry.data.get("drinking_duration", 15),
                )
            )

            # Offload the synchronous PK what-if to the executor — it does a
            # full-history Bateman recompute (N mini-boluses * len(history))
            # which is CPU-bound and must not block the event loop.
            low_time = await hass.async_add_executor_job(
                coordinator.predict_low_time_if_dose,
                dose_strength,
                drinking_duration_min / 60.0,
            )
            payload = {"low_time": low_time.isoformat() if low_time else None}
            _LOGGER.info(
                "ax_dose_logger predict_low REST: entity=%s substance=%s strength=%s "
                "low_time=%s",
                entity_id, substance, dose_strength, payload["low_time"],
            )
            return self.json(payload)
        except Exception as err:  # noqa: BLE001 — defensive; never 500 the popup
            _LOGGER.warning(
                "ax_dose_logger predict_low REST: error for entity %s: %s",
                entity_id, err,
            )
            return self.json({"low_time": None})
