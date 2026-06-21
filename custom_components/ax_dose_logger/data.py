"""Custom types for ax_dose_logger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


type AxDoseLoggerConfigEntry = ConfigEntry[AxDoseLoggerData]


@dataclass
class AxDoseLoggerData:
    """Data for the AX Dose Logger integration."""

