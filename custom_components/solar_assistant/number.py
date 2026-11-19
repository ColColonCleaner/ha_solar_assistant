"""SolarAssistant numeric Settings entities (e.g. Max charge current, Max solar power)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SolarAssistantCoordinator
from .entity import SolarAssistantMetricEntity
from .precision import format_setting, metric_unit, number_step, parse_numeric

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarAssistantCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _on_new_metric(topic: str) -> None:
        if topic in known:
            return
        defn = coordinator.definitions.get(topic)
        if defn is None or defn.get("platform") != "number":
            return
        known.add(topic)
        async_add_entities([SolarAssistantNumber(coordinator, entry, topic, defn)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_metric, _on_new_metric)
    )

    for topic, defn in list(coordinator.definitions.items()):
        if defn.get("platform") != "number":
            continue
        if topic not in known:
            known.add(topic)
            async_add_entities([SolarAssistantNumber(coordinator, entry, topic, defn)])


class SolarAssistantNumber(SolarAssistantMetricEntity, NumberEntity):
    """A setting of the inverter, shown as an input box with the step of its unit."""

    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: SolarAssistantCoordinator,
        entry: ConfigEntry,
        topic: str,
        defn: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry, topic, defn)
        self._attr_device_class = defn.get("device_class")
        self._attr_native_unit_of_measurement = metric_unit(defn)
        self._attr_native_step = number_step(defn, coordinator.data.get(topic))
        if defn.get("min") is not None:
            self._attr_native_min_value = float(defn["min"])
        if defn.get("max") is not None:
            self._attr_native_max_value = float(defn["max"])

    @property
    def native_value(self) -> float | None:
        value = parse_numeric(self._coordinator.data.get(self._topic))
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        await self._coordinator.set_setting(
            self._topic, format_setting(value, self._attr_native_step)
        )
