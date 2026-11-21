"""SolarAssistant read-only sensor entities (Status + Info groups)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import derived_entities, topoff_entities
from .const import DOMAIN
from .coordinator import SolarAssistantConfigEntry, SolarAssistantCoordinator
from .entity import SolarAssistantMetricEntity
from .health import is_duplicate_total
from .precision import (
    is_boolean_metric,
    is_numeric_metric,
    metric_unit,
    parse_numeric,
    sensor_display_precision,
)

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
    registry = er.async_get(hass)
    known: set[str] = set()

    @callback
    def _remove_sensor(topic: str, why: str) -> None:
        """Remove the sensor that an older version made for a topic, if the registry holds one."""
        old = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}_{topic}")
        if old is not None:
            _LOGGER.info("Removing %s, which %s", old, why)
            registry.async_remove(old)

    @callback
    def _consider(topic: str) -> None:
        if topic in known:
            return
        defn = coordinator.definitions.get(topic)
        if defn is None or not _is_for_us(defn):
            return
        if not coordinator.should_create_sensor(topic):
            return
        known.add(topic)
        if is_duplicate_total(defn, coordinator.definitions, coordinator.entity_topics()):
            # On a unit with one inverter this total repeats an inverter
            # entity, so it gets no entity, and an entity of an older
            # version leaves the registry.
            _remove_sensor(topic, "repeats an inverter sensor")
            return
        if is_boolean_metric(defn, coordinator.data.get(topic)):
            # An on/off value is a binary sensor, which binary_sensor.py
            # makes. v0.5.5 and v0.5.6 made a sensor for it.
            _remove_sensor(topic, "is a binary sensor now")
            return
        async_add_entities([SolarAssistantSensor(coordinator, entry, topic, defn)])

    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_metric, _consider)
    )

    for topic in list(coordinator.definitions):
        _consider(topic)


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
