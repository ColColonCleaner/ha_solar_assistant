"""SolarAssistant time-of-day Settings entities (e.g. Grid charge slot 1 start).

SolarAssistant sends each time setting as a select of quarter hours. A time
entity takes any minute, and it shows a value that is not a quarter hour
(upstream issues #28 and #34). The select entity that an older version made
for the same topic leaves the entity registry.
"""
from __future__ import annotations

import logging
from datetime import time
from typing import Any

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import topoff_entities
from .const import DOMAIN
from .coordinator import SolarAssistantConfigEntry, SolarAssistantCoordinator
from .entity import SolarAssistantMetricEntity
from .precision import format_hhmm, is_time_metric, parse_hhmm

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarAssistantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(topoff_entities.times(coordinator.topoff, entry))
    registry = er.async_get(hass)
    known: set[str] = set()

    @callback
    def _add(topic: str, defn: dict[str, Any]) -> None:
        known.add(topic)
        entity = SolarAssistantTime(coordinator, entry, topic, defn)
        old = registry.async_get_entity_id("select", DOMAIN, entity.unique_id)
        if old is not None:
            _LOGGER.info("Replacing %s with a time entity", old)
            registry.async_remove(old)
        async_add_entities([entity])

    @callback
    def _on_new_metric(topic: str) -> None:
        if topic in known:
            return
        defn = coordinator.definitions.get(topic)
        if defn is None or not is_time_metric(defn):
            return
        _add(topic, defn)

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_metric, _on_new_metric)
    )

    for topic, defn in list(coordinator.definitions.items()):
        if topic not in known and is_time_metric(defn):
            _add(topic, defn)


class SolarAssistantTime(SolarAssistantMetricEntity, TimeEntity):
    @property
    def native_value(self) -> time | None:
        return parse_hhmm(self._coordinator.data.get(self._topic))

    async def async_set_value(self, value: time) -> None:
        await self._coordinator.set_setting(self._topic, format_hhmm(value))
