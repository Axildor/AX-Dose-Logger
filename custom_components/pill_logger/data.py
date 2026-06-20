"""Custom types for pill_logger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


type PillLoggerConfigEntry = ConfigEntry[PillLoggerData]


@dataclass
class PillLoggerData:
    """Data for the Pill Logger integration."""

    pass
