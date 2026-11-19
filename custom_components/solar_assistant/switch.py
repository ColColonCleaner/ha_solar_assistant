"""SolarAssistant boolean Settings entities (e.g. Grid charge, Smart load).

A metric of the platform switch becomes a switch. So does a select whose two
options are Enabled and Disabled, because a toggle fits it better than a
list. The select entity that an older version made for such a topic leaves
the entity registry.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import topoff_entities
from .const import DOMAIN
from .coordinator import SolarAssistantConfigEntry, SolarAssistantCoordinator
from .entity import SolarAssistantMetricEntity
from .precision import toggle_options

_LOGGER = logging.getLogger(__name__)


def _is_for_us(defn: dict[str, Any] | None) -> bool:
    return defn is not None and (defn.get("platform") == "switch" or toggle_options(defn) is not None)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarAssistantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(topoff_entities.switches(coordinator.topoff, entry))
    registry = er.async_get(hass)
    known: set[str] = set()

    @callback
    def _add(topic: str, defn: dict[str, Any]) -> None:
        known.add(topic)
        entity = SolarAssistantSwitch(coordinator, entry, topic, defn)
        if defn.get("platform") == "select":
            old = registry.async_get_entity_id("select", DOMAIN, entity.unique_id)
            if old is not None:
                _LOGGER.info("Replacing %s with a switch", old)
                registry.async_remove(old)
        async_add_entities([entity])

    @callback
    def _on_new_metric(topic: str) -> None:
        if topic in known:
            return
        defn = coordinator.definitions.get(topic)
        if _is_for_us(defn):
            _add(topic, defn)

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_metric, _on_new_metric)
    )

    for topic, defn in list(coordinator.definitions.items()):
        if topic not in known and _is_for_us(defn):
            _add(topic, defn)


class SolarAssistantSwitch(SolarAssistantMetricEntity, SwitchEntity):
    def __init__(
        self,
        coordinator: SolarAssistantCoordinator,
        entry: SolarAssistantConfigEntry,
        topic: str,
        defn: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry, topic, defn)
        toggle = toggle_options(defn)
        if toggle is not None:
            # A toggle select writes its option text, such as "Enabled".
            self._payload_on, self._payload_off = toggle
        else:
            self._payload_on = str(defn.get("payload_on") or "1")
            self._payload_off = str(defn.get("payload_off") or "0")

    @property
    def is_on(self) -> bool | None:
        v = self._coordinator.data.get(self._topic)
        if v is None:
            return None
        # Server may report the payload value, a bool, or a string like "Enabled"/"Disabled".
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s == self._payload_on.lower() or s in ("true", "1", "on", "enabled", "yes"):
            return True
        if s == self._payload_off.lower() or s in ("false", "0", "off", "disabled", "no"):
            return False
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._coordinator.set_setting(self._topic, self._payload_on)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._coordinator.set_setting(self._topic, self._payload_off)
