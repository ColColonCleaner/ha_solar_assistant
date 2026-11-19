"""SolarAssistant — Home Assistant integration entry points."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import CONF_ENABLED_TOPICS, DOMAIN, LOCAL_UNIQUE_PREFIXES, SYSTEM_PREFIX
from .coordinator import SolarAssistantConfigEntry, SolarAssistantCoordinator
from .services import async_setup_services
from .topoff_manager import TopOffManager

PLATFORMS = ["binary_sensor", "button", "number", "select", "sensor", "switch", "time"]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions once, so an automation can name them while no entry is loaded."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SolarAssistantConfigEntry) -> bool:
    coordinator = SolarAssistantCoordinator(hass, entry)
    topoff = TopOffManager(hass, entry, coordinator)
    await topoff.async_load()
    coordinator.topoff = topoff
    await coordinator.async_start()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    topoff.async_start()
    entry.async_on_unload(topoff.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: SolarAssistantConfigEntry) -> bool:
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        hass.config_entries.async_update_entry(
            entry, options=_with_system_metrics_enabled(entry.options), minor_version=2
        )
    return True


def _with_system_metrics_enabled(options: Mapping[str, Any]) -> dict[str, Any]:
    """Saved selections predate selectable system metrics, so add `system/*` to keep them on."""
    options = dict(options)
    topics = options.get(CONF_ENABLED_TOPICS)
    if topics is not None:
        options[CONF_ENABLED_TOPICS] = sorted({*topics, f"{SYSTEM_PREFIX}*"})
    return options


async def async_unload_entry(hass: HomeAssistant, entry: SolarAssistantConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: SolarAssistantConfigEntry) -> None:
    """Drop sensors that fall outside the new enabled-topic selection, then reload the entry.

    Only read-only metric sensors are governed by the topic selection. The
    settings platforms (number/select/switch/time), the connection
    binary_sensor and the entities that the integration makes itself (the
    top-off, the derived and the health entities) are left untouched.
    """
    if entry.options.get(CONF_ENABLED_TOPICS) is not None:
        registry = er.async_get(hass)
        coordinator = entry.runtime_data
        prefix = f"{entry.unique_id}_"
        for ent in list(er.async_entries_for_config_entry(registry, entry.entry_id)):
            if ent.domain != "sensor" or not ent.unique_id.startswith(prefix):
                continue
            topic = ent.unique_id[len(prefix):]
            if topic.startswith(LOCAL_UNIQUE_PREFIXES):
                continue
            if not coordinator.should_create_sensor(topic):
                registry.async_remove(ent.entity_id)
    await hass.config_entries.async_reload(entry.entry_id)
