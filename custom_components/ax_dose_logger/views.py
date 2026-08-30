"""
Custom REST endpoints exposing history data to the frontend.

* /api/ax_dose_logger/history/{device_id} — the authoritative, pruned
  dose_history array from AxDoseLoggerStore (bar-graph source).

  For Master Tracker devices (Caffeine Tracker / Alcohol Tracker) the endpoint
  returns the aggregated master ``dose_history`` (every drink of that substance
  across all granular drink devices) so the frontend's 14-day bar graph renders
  correctly.  The per-substance store lives in ``store.get_drink_master()``.

* /api/ax_dose_logger/graph/{device_id} — recorder-independent graph payload
  for the card's Amount-in-Body line graph + Effectiveness graphs.  The PK
  curve is recomputed from the integration's own dose-history store (365-day
  default retention) so the graphs are NOT truncated by the HA recorder's
  ``purge_keep_days`` default of 10 days.
"""

from datetime import timedelta

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    DEVICE_CATEGORY_DRINKS,
    DEVICE_CATEGORY_MEDICINE,
    DOMAIN,
)
from .const import (
    LOGGER as _LOGGER,
)
from .sensors._tracker_info import tracker_substance

# Point-budget bounds for the graph endpoint (see the plan:
# plans/recorder-independent-graphs-plan.md).  At the chart's ~284 px width
# more than ~1 point per 1.2 px is invisible, so 240 is visually lossless
# while being cheaper to render than the old 800-node recorder fetches.
_GRAPH_POINTS_DEFAULT = 240
_GRAPH_POINTS_MIN = 40
_GRAPH_POINTS_MAX = 400
# Window bounds (hours).  The store retains 365 days by default; cap the
# request window at that so a bogus query can't force a 1095-day sweep.
_GRAPH_HOURS_MIN = 1
_GRAPH_HOURS_MAX = 1095 * 24


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

        # Master Tracker devices: identifiers carry the profile-scoped tracker
        # id (e.g. (DOMAIN, "caffeine_tracker") for the legacy default profile,
        # (DOMAIN, "caffeine_tracker_{uuid}") for a named profile).  Their
        # aggregated dose history lives in store.get_drink_master(profile_id,
        # substance), not in the per-entry store history.  Detect the tracker
        # id and return the aggregated master history serialized as
        # [[iso, strength], ...] to match the medicine format the frontend's
        # _bucketByDay expects.
        for identifier in device.identifiers:
            if identifier[0] != DOMAIN:
                continue
            resolved = tracker_substance(identifier[1])
            if resolved is not None:
                profile_id, substance = resolved
                master_data = store.get_drink_master(profile_id, substance)
                doses = master_data.get("doses", [])
                # Master doses are stored as [iso, strength, t_dur_hours];
                # the frontend bar graph only consumes [iso, strength].
                payload = [[d[0], d[1]] for d in doses if len(d) >= 2]
                _LOGGER.debug(
                    "ax_dose_logger history REST: master device_id=%s profile=%s substance=%s returned %d doses (store had %d)",
                    device_id,
                    profile_id,
                    substance,
                    len(payload),
                    len(doses),
                )
                return self.json(payload)

        # Use the first config entry for this device.
        # AX Dose Logger creates one device per config entry (one medication per
        # device), so device.config_entries always has exactly one member.
        # If multi-entry devices are ever supported, this must be revisited.
        entry_id = next(iter(device.config_entries))

        # Get dose history from store
        history = store.get_history(entry_id)
        _LOGGER.debug(
            "ax_dose_logger history REST: device_id=%s entry_id=%s returned %d doses",
            device_id,
            entry_id,
            len(history),
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

            config_entry: ConfigEntry | None = hass.config_entries.async_get_entry(entry.config_entry_id)
            if config_entry is None:
                return self.json({"low_time": None})

            # Only granular drink entries carry dose_strength + drinking_duration.
            if config_entry.data.get("device_category") != "drinks":
                return self.json({"low_time": None})

            substance = config_entry.data.get("drink_type")
            # M2M: resolve the target profile.  The card passes target_profile
            # (the UUID of the profile whose Low band to predict for).  If
            # omitted, fall back to the drink's first allowed_profile (single-
            # profile convenience default).  Validate the target is in the
            # drink's allowed_profiles; if not, return None (the card will
            # render "Low: -").
            target_profile = request.query.get("target_profile")
            # Options-first: the options flow writes allowed_profiles to
            # entry.options; fall back to entry.data for pre-migration entries.
            allowed_profiles = config_entry.options.get(
                "allowed_profiles",
                config_entry.data.get("allowed_profiles", ["default"]),
            )
            if target_profile:
                if target_profile not in allowed_profiles:
                    _LOGGER.info(
                        "ax_dose_logger predict_low REST: target_profile %s not in allowed_profiles %s",
                        target_profile,
                        allowed_profiles,
                    )
                    return self.json({"low_time": None})
                predict_profile = target_profile
            elif allowed_profiles:
                predict_profile = allowed_profiles[0]
            else:
                # No allowed profiles -> pure inventory tracker, no PK curve.
                return self.json({"low_time": None})
            masters = hass.data.get(DOMAIN, {}).get("_drink_masters", {})
            coordinator = masters.get((predict_profile, substance))
            if coordinator is None:
                _LOGGER.info(
                    "ax_dose_logger predict_low REST: no master coordinator for (profile=%s, %s)",
                    predict_profile,
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
            _LOGGER.debug(
                "ax_dose_logger predict_low REST: entity=%s substance=%s strength=%s low_time=%s",
                entity_id,
                substance,
                dose_strength,
                payload["low_time"],
            )
            return self.json(payload)
        except Exception as err:  # noqa: BLE001 — defensive; never 500 the popup
            _LOGGER.warning(
                "ax_dose_logger predict_low REST: error for entity %s: %s",
                entity_id,
                err,
            )
            return self.json({"low_time": None})


class AxDoseLoggerGraphView(HomeAssistantView):
    """Recorder-independent graph payload for the card's Graphs pane.

    URL: /api/ax_dose_logger/graph/{device_id}?hours=720&points=240
    Method: GET
    Auth: Bearer token (requires_auth = True)
    Response: JSON
        {
          "amount":  [[iso_timestamp, value], ...],   # evenly sampled PK curve
          "metrics": { metric_key: { "YYYY-MM-DD": value, ... }, ... }
        }

    The Amount-in-Body curve is recomputed from the integration's own
    dose-history store (365-day default retention) instead of the HA
    recorder, whose ``purge_keep_days`` default of 10 days silently
    truncated the card's long timeframes.  Effectiveness metrics come from
    the date-keyed metrics store (also 365-day retention).

    For Master Tracker devices the amount series is the aggregated
    (profile, substance) body-mass curve; ``metrics`` is empty
    (effectiveness is medicine-only).
    """

    url = "/api/ax_dose_logger/graph/{device_id}"
    name = "api:ax_dose_logger:graph"
    requires_auth = True

    async def get(self, request: web.Request, device_id: str) -> web.Response:
        """Return the graph payload for the given device."""
        hass = request.app["hass"]

        # --- Query params (clamped; bogus values fall back to defaults) ---
        try:
            hours = int(float(request.query.get("hours", "720")))
        except TypeError, ValueError:
            hours = 720
        hours = max(_GRAPH_HOURS_MIN, min(hours, _GRAPH_HOURS_MAX))
        try:
            points = int(float(request.query.get("points", str(_GRAPH_POINTS_DEFAULT))))
        except TypeError, ValueError:
            points = _GRAPH_POINTS_DEFAULT
        points = max(_GRAPH_POINTS_MIN, min(points, _GRAPH_POINTS_MAX))

        store = hass.data.get(DOMAIN, {}).get("_store")
        if not store:
            return self.json({"amount": [], "metrics": {}})

        # --- Device resolution (mirrors the history view) ---
        device_reg = dr.async_get(hass)
        device = device_reg.async_get(device_id)
        if not device or not device.config_entries:
            return self.json({"amount": [], "metrics": {}})

        # Master Tracker devices: sample the aggregated (profile, substance)
        # body-mass curve from the master coordinator.
        for identifier in device.identifiers:
            if identifier[0] != DOMAIN:
                continue
            resolved = tracker_substance(identifier[1])
            if resolved is not None:
                profile_id, substance = resolved
                masters = hass.data.get(DOMAIN, {}).get("_drink_masters", {})
                coordinator = masters.get((profile_id, substance))
                if coordinator is None:
                    return self.json({"amount": [], "metrics": {}})
                now = dt_util.now()
                start = now - timedelta(hours=hours)
                samples = await hass.async_add_executor_job(coordinator.sample_body_mass_curve, start, now, points)
                payload = [[ts.isoformat(), round(v, 2)] for ts, v in samples]
                _LOGGER.debug(
                    "ax_dose_logger graph REST: master device_id=%s profile=%s substance=%s "
                    "hours=%d points=%d returned %d samples",
                    device_id,
                    profile_id,
                    substance,
                    hours,
                    points,
                    len(payload),
                )
                return self.json({"amount": payload, "metrics": {}})

        # Medicine / granular drink device: resolve the config entry.
        entry_id = next(iter(device.config_entries))
        entry = hass.config_entries.async_get_entry(entry_id)
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id, {}).get("coordinator")
        if entry is None or coordinator is None:
            return self.json({"amount": [], "metrics": {}})

        # Granular drink devices host a DrinkCoordinator, which has no
        # sample_amount_curve method (the PK curve lives on the Master
        # Tracker coordinators handled above).  Return an empty payload —
        # the frontend already renders that gracefully.
        if entry.data.get("device_category", DEVICE_CATEGORY_MEDICINE) == DEVICE_CATEGORY_DRINKS:
            _LOGGER.debug(
                "ax_dose_logger graph REST: device_id=%s entry_id=%s is a granular "
                "drink device; returning empty payload",
                device_id,
                entry_id,
            )
            return self.json({"amount": [], "metrics": {}})

        now = dt_util.now()
        start = now - timedelta(hours=hours)

        # PK curve sampling is CPU-bound (points × len(history) Bateman
        # evaluations) — offload to the executor like predict_low.
        samples = await hass.async_add_executor_job(coordinator.sample_amount_curve, start, now, points)
        amount = [[ts.isoformat(), round(v, 2)] for ts, v in samples]

        # Effectiveness metrics: date-keyed map straight from the
        # coordinator (already pruned to the retention window; cheap copy).
        metrics = {
            key: dict(dated) for key, dated in (coordinator.data.metric_values or {}).items() if isinstance(dated, dict)
        }

        _LOGGER.debug(
            "ax_dose_logger graph REST: device_id=%s entry_id=%s hours=%d points=%d "
            "returned %d samples, %d metric keys",
            device_id,
            entry_id,
            hours,
            points,
            len(amount),
            len(metrics),
        )
        return self.json({"amount": amount, "metrics": metrics})
