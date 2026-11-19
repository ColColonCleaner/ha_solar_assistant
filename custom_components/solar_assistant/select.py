"""SolarAssistant enum Settings entities (e.g. Battery type, Work mode)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import SolarAssistantConfigEntry, SolarAssistantCoordinator
from .entity import SolarAssistantMetricEntity
from .precision import is_time_metric, is_toggle_metric, select_options

_LOGGER = logging.getLogger(__name__)


def _is_for_us(defn: dict[str, Any] | None) -> bool:
    """A select metric, except a time of day (time platform) and an Enabled/Disabled toggle (switch platform)."""
    return (
        defn is not None
        and defn.get("platform") == "select"
        and not is_time_metric(defn)
        and not is_toggle_metric(defn)
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarAssistantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _on_new_metric(topic: str) -> None:
        if topic in known:
            return
        defn = coordinator.definitions.get(topic)
        if not _is_for_us(defn):
            return
        known.add(topic)
        async_add_entities([SolarAssistantSelect(coordinator, entry, topic, defn)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_metric, _on_new_metric)
    )

    for topic, defn in list(coordinator.definitions.items()):
        if not _is_for_us(defn):
            continue
        if topic not in known:
            known.add(topic)
            async_add_entities([SolarAssistantSelect(coordinator, entry, topic, defn)])


class SolarAssistantSelect(SolarAssistantMetricEntity, SelectEntity):
    def __init__(
        self,
        coordinator: SolarAssistantCoordinator,
        entry: ConfigEntry,
        topic: str,
        defn: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry, topic, defn)
        self._defn = defn

    @property
    def options(self) -> list[str]:
        return select_options(self._defn, self._coordinator.data.get(self._topic))

    @property
    def current_option(self) -> str | None:
        v = self._coordinator.data.get(self._topic)
        return None if v is None else str(v)

    async def async_select_option(self, option: str) -> None:
        await self._coordinator.set_setting(self._topic, option)
