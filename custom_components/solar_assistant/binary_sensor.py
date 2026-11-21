"""SolarAssistant binary sensors: the connection of the unit, the SOC spread alert, the data of each device, and each read-only metric with an on/off value."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import derived_entities
from .coordinator import SolarAssistantConfigEntry, SolarAssistantCoordinator
from .entity import SolarAssistantMetricEntity, unit_device_info
from .health import device_prefix
from .precision import boolean_state, is_boolean_metric


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarAssistantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([SolarAssistantConnectionSensor(coordinator, entry)])
    derived_entities.add_with_first_pack(
        hass, entry, coordinator, async_add_entities, derived_entities.spread_alerts
    )
    known: set[str] = set()
    metrics: set[str] = set()

    @callback
    def _on_new_metric(topic: str) -> None:
        defn = coordinator.definitions.get(topic)
        if defn is None:
            return
        if (
            topic not in metrics
            and defn.get("group") != "Ignore"
            and is_boolean_metric(defn, coordinator.data.get(topic))
            and coordinator.should_create_sensor(topic)
        ):
            # The same conditions as a sensor of sensor.py, which leaves an
            # on/off value to this platform.
            metrics.add(topic)
            async_add_entities([SolarAssistantBinarySensor(coordinator, entry, topic, defn)])
        prefix = device_prefix(topic)
        if prefix is None or prefix in known:
            return
        known.add(prefix)
        async_add_entities([derived_entities.DeviceDataSensor(coordinator, entry, prefix, defn)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.signal_new_metric, _on_new_metric)
    )
    for topic in list(coordinator.definitions):
        _on_new_metric(topic)


class SolarAssistantBinarySensor(SolarAssistantMetricEntity, BinarySensorEntity):
    """A read-only metric with an on/off value, such as inverter_1/quick_charge."""

    @property
    def is_on(self) -> bool | None:
        return boolean_state(self._coordinator.data.get(self._topic))


class SolarAssistantConnectionSensor(BinarySensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connection"

    def __init__(
        self,
        coordinator: SolarAssistantCoordinator,
        entry: ConfigEntry,
    ) -> None:
        self._coordinator = coordinator
        # See entity.py for why we key on entry.unique_id rather than entry.entry_id.
        self._attr_unique_id = f"{entry.unique_id}_connection"
        self._attr_device_info = unit_device_info(entry)

    @property
    def is_on(self) -> bool:
        return self._coordinator.is_connected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ts = self._coordinator.last_connected_at
        return {
            "connected_host": self._coordinator.connected_host,
            "last_connected_at": ts.isoformat() if ts else None,
            "last_error": self._coordinator.last_error,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._coordinator.signal_connection_state,
                self.async_write_ha_state,
            )
        )
