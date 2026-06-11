"""Constants for pill_logger."""

import re
from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "pill_logger"
ATTRIBUTION = "Data provided by http://jsonplaceholder.typicode.com/"

STANDARD_EFFECTIVENESS_METRICS: dict[str, str] = {
    "pain": "Pain",
    "mood": "Mood",
    "nausea": "Nausea",
    "fatigue": "Fatigue",
}

EFFECTIVENESS_METRIC_ICONS: dict[str, str] = {
    "pain": "mdi:emoticon-cry",
    "mood": "mdi:emoticon-happy",
    "nausea": "mdi:emoticon-sick",
    "fatigue": "mdi:sleep",
}

DEFAULT_METRIC_ICON = "mdi:chart-line"


def sanitize_key(name: str) -> str:
    """Convert a human-readable metric name into a safe entity key component."""
    return re.sub(r"[^a-z0-9]", "_", name.lower().strip())
