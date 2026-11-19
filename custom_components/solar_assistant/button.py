"""SolarAssistant buttons: start and stop the battery top-off."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import topoff_entities
from .const import DOMAIN
from .coordinator import SolarAssistantCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarAssistantCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(topoff_entities.buttons(coordinator.topoff, entry))
