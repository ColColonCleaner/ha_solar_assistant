"""SolarAssistant buttons: start and stop the battery top-off."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import topoff_entities
from .const import DOMAIN
from .coordinator import SolarAssistantConfigEntry, SolarAssistantCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarAssistantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(topoff_entities.buttons(coordinator.topoff, entry))
