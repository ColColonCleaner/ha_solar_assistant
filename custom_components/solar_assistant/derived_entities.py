"""Entities that the integration derives from the metrics.

- Battery SOC spread: the largest pack SOC minus the smallest, on the Totals
  device, and a problem sensor when it exceeds the alert option.
- Data: one connectivity sensor per inverter and battery, off when the device
  sent no changed value for the stale-after option (upstream issue #14).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util.dt import utcnow

from .const import (
    CONF_SPREAD_ALERT,
    DEFAULT_SPREAD_ALERT,
    DERIVED_UNIQUE_PREFIX,
    DOMAIN,
    HEALTH_UNIQUE_PREFIX,
)
from .coordinator import SolarAssistantConfigEntry, SolarAssistantCoordinator
from .entity import device_label_and_id
from .health import device_prefix, is_stale, pack_socs, soc_spread

STALE_CHECK = timedelta(seconds=30)


class _DerivedEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, coordinator: SolarAssistantCoordinator, entry: SolarAssistantConfigEntry,
                 unique: str, device: dict[str, Any]) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_{unique}"
        label, device_id = device_label_and_id(device)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.unique_id}_{device_id}")},
            name=label,
            manufacturer="SolarAssistant",
        )

    @property
    def available(self) -> bool:
        return self._coordinator.is_connected

    def _wants(self, topic: str) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._coordinator.signal_metric_update, self._on_update)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._coordinator.signal_connection_state, self.async_write_ha_state)
        )

    @callback
    def _on_update(self, topic: str) -> None:
        if self._wants(topic):
            self.async_write_ha_state()


_TOTALS = {"device": "totals", "number": None}


class SocSpreadSensor(_DerivedEntity, SensorEntity):
    _attr_translation_key = "soc_spread"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:battery-sync-outline"

    def __init__(self, coordinator: SolarAssistantCoordinator, entry: SolarAssistantConfigEntry) -> None:
        super().__init__(coordinator, entry, f"{DERIVED_UNIQUE_PREFIX}soc_spread", _TOTALS)

    def _wants(self, topic: str) -> bool:
        return topic.endswith("/state_of_charge")

    @property
    def native_value(self) -> float | None:
        return soc_spread(pack_socs(self._coordinator.data))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        socs = pack_socs(self._coordinator.data)
        if not socs:
            return {}
        return {
            "packs": socs,
            "lowest": min(socs, key=socs.get),
            "highest": max(socs, key=socs.get),
        }


class SocSpreadAlert(_DerivedEntity, BinarySensorEntity):
    _attr_translation_key = "soc_spread_alert"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: SolarAssistantCoordinator, entry: SolarAssistantConfigEntry) -> None:
        super().__init__(coordinator, entry, f"{DERIVED_UNIQUE_PREFIX}soc_spread_alert", _TOTALS)

    def _wants(self, topic: str) -> bool:
        return topic.endswith("/state_of_charge")

    @property
    def _limit(self) -> float:
        return float(self._entry.options.get(CONF_SPREAD_ALERT, DEFAULT_SPREAD_ALERT))

    @property
    def is_on(self) -> bool | None:
        spread = soc_spread(pack_socs(self._coordinator.data))
        return None if spread is None else spread > self._limit

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"alert_above": self._limit}


class DeviceDataSensor(_DerivedEntity, BinarySensorEntity):
    """On while the device sends changed values, off when it went quiet for the stale-after option."""

    _attr_translation_key = "data"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarAssistantCoordinator, entry: SolarAssistantConfigEntry,
                 prefix: str, defn: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, f"{HEALTH_UNIQUE_PREFIX}{prefix}", defn)
        self._prefix = prefix

    def _wants(self, topic: str) -> bool:
        return device_prefix(topic) == self._prefix

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # A method marked @callback runs in the event loop. A plain function
        # would run in a worker thread, where async_write_ha_state must not run.
        self.async_on_remove(async_track_time_interval(self.hass, self._recheck, STALE_CHECK))

    @callback
    def _recheck(self, _now: Any) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        stale = is_stale(self._coordinator.last_change.get(self._prefix), utcnow(), self._coordinator.stale_after)
        return None if stale is None else not stale

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last = self._coordinator.last_change.get(self._prefix)
        return {
            "last_change": last.isoformat() if last else None,
            "stale_after_min": self._coordinator.stale_after,
        }


@callback
def spread_sensors(coordinator: SolarAssistantCoordinator, entry: SolarAssistantConfigEntry) -> list[Entity]:
    return [SocSpreadSensor(coordinator, entry)]


@callback
def spread_alerts(coordinator: SolarAssistantCoordinator, entry: SolarAssistantConfigEntry) -> list[Entity]:
    return [SocSpreadAlert(coordinator, entry)]
