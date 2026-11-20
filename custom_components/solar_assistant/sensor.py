"""SolarAssistant read-only sensor entities (Status + Info groups)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import derived_entities, topoff_entities
from .const import DOMAIN
from .coordinator import SolarAssistantConfigEntry, SolarAssistantCoordinator
from .entity import SolarAssistantMetricEntity
from .precision import is_numeric_metric, metric_unit, parse_numeric, sensor_display_precision

_LOGGER = logging.getLogger(__name__)


def _is_for_us(defn: dict[str, Any]) -> bool:
    """Sensor platform handles read-only metrics only.

    Anything with platform ∈ {number, select, switch} is owned by the
    matching writable platform. Older SA builds without a `platform`
    field (None) fall through to sensor as the safe default.
    """
    if defn.get("group") == "Ignore":
        return False
    platform = defn.get("platform")
    return platform is None or platform == "sensor"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarAssistantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(topoff_entities.sensors(coordinator.topoff, entry))
    derived_entities.add_with_first_pack(
        hass, entry, coordinator, async_add_entities, derived_entities.spread_sensors
    )
    known: set[str] = set()

    @callback
    def _on_new_metric(topic: str) -> None:
        if topic in known:
            return
        defn = coordinator.definitions.get(topic)
        if defn is None or not _is_for_us(defn):
            return
        if not coordinator.should_create_sensor(topic):
            return
        known.add(topic)
        async_add_entities([SolarAssistantSensor(coordinator, entry, topic, defn)])

    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_metric, _on_new_metric)
    )

    for topic, defn in list(coordinator.definitions.items()):
        if not _is_for_us(defn):
            continue
        if not coordinator.should_create_sensor(topic):
            continue
        if topic not in known:
            known.add(topic)
            async_add_entities([SolarAssistantSensor(coordinator, entry, topic, defn)])


class SolarAssistantSensor(SolarAssistantMetricEntity, SensorEntity):
    def __init__(
        self,
        coordinator: SolarAssistantCoordinator,
        entry: ConfigEntry,
        topic: str,
        defn: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry, topic, defn)
        self._attr_native_unit_of_measurement = metric_unit(defn)
        self._attr_device_class = defn.get("device_class")
        self._attr_state_class = defn.get("state_class")
        self._attr_suggested_display_precision = sensor_display_precision(topic, defn)
        self._numeric = is_numeric_metric(defn)

    @property
    def native_value(self) -> Any:
        value = self._coordinator.data.get(self._topic)
        if self._numeric:
            return parse_numeric(value)
        return value
