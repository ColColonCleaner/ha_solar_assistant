"""Entities of the battery top-off: its settings, its state and its buttons.

They sit on the device of the SolarAssistant unit, so the Home Assistant UI
edits every setting of the top-off.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity, EntityCategory

from . import topoff as t
from .const import TOPOFF_UNIQUE_PREFIX
from .entity import unit_device_info
from .topoff_manager import TopOffManager

# key, name, unit, device class, min, max, step
NUMBERS: tuple[tuple[str, str, str, NumberDeviceClass | None, float, float, float], ...] = (
    ("interval_days", "Top-off interval", "d", NumberDeviceClass.DURATION, 1, 60, 1),
    ("current_a", "Top-off charge current", "A", NumberDeviceClass.CURRENT, 1, 250, 1),
    ("full_soc", "Top-off full charge", "%", None, 50, 100, 1),
    ("hold_minutes", "Top-off hold time", "min", NumberDeviceClass.DURATION, 0, 480, 1),
    ("time_limit_hours", "Top-off time limit", "h", NumberDeviceClass.DURATION, 0.5, 48, 0.5),
    ("discharge_minutes", "Top-off after discharge", "min", NumberDeviceClass.DURATION, 5, 600, 5),
)
# The time entities of the window of 0.4, which the integration removes from the registry.
RETIRED_TIMES = ("window_start", "window_end")


class TopOffEntity(Entity):
    """Common wiring: the unit device, a stable unique ID, and updates from the manager."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, manager: TopOffManager, entry: ConfigEntry, key: str, name: str) -> None:
        # The name comes from strings.json, entity.<platform>.topoff_<key>. The
        # argument name only documents the English name at the call site.
        self._manager = manager
        self._attr_unique_id = f"{entry.unique_id}_{TOPOFF_UNIQUE_PREFIX}{key}"
        self._attr_translation_key = f"{TOPOFF_UNIQUE_PREFIX}{key}"
        self._attr_device_info = unit_device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._manager.signal, self.async_write_ha_state)
        )


class TopOffSwitch(TopOffEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:battery-sync"

    def __init__(self, manager: TopOffManager, entry: ConfigEntry) -> None:
        super().__init__(manager, entry, "enabled", "Battery top-off")

    @property
    def is_on(self) -> bool:
        return self._manager.settings.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._manager.async_set(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._manager.async_set(enabled=False)


class TopOffNumber(TopOffEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, manager: TopOffManager, entry: ConfigEntry, spec: tuple) -> None:
        key, name, unit, device_class, lo, hi, step = spec
        super().__init__(manager, entry, key, name)
        self._key = key
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_native_min_value = lo
        self._attr_native_max_value = hi
        self._attr_native_step = step

    @property
    def native_value(self) -> float:
        return float(getattr(self._manager.settings, self._key))

    async def async_set_native_value(self, value: float) -> None:
        await self._manager.async_set(**{self._key: value})


class TopOffStateSensor(TopOffEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(t.STATES)
    _attr_icon = "mdi:battery-charging-high"

    def __init__(self, manager: TopOffManager, entry: ConfigEntry) -> None:
        super().__init__(manager, entry, "state", "Top-off state")

    @property
    def native_value(self) -> str:
        return self._manager.status.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._manager.status
        return {
            "battery_soc": self._manager.socs,
            "inverter": s.inverter,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "full_since": s.full_since.isoformat() if s.full_since else None,
            "discharging_since": s.discharging_since.isoformat() if s.discharging_since else None,
            "saved_settings": s.saved,
            "last_error": s.last_error,
            "last_result": s.last_result,
        }


class TopOffTimestampSensor(TopOffEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: TopOffManager, entry: ConfigEntry, key: str, name: str) -> None:
        super().__init__(manager, entry, key, name)
        self._key = key

    @property
    def native_value(self) -> datetime | None:
        if self._key == "next_due":
            return self._manager.next_due
        return self._manager.status.last_full


class TopOffButton(TopOffEntity, ButtonEntity):
    def __init__(self, manager: TopOffManager, entry: ConfigEntry, start: bool) -> None:
        super().__init__(
            manager, entry, "start" if start else "stop", "Start top-off" if start else "Stop top-off"
        )
        self._start = start
        self._attr_icon = "mdi:play" if start else "mdi:stop"

    async def async_press(self) -> None:
        if self._start:
            await self._manager.async_tick(start_now=True)
        else:
            await self._manager.async_tick(stop_now=True)


@callback
def switches(manager: TopOffManager, entry: ConfigEntry) -> list[Entity]:
    return [TopOffSwitch(manager, entry)]


@callback
def numbers(manager: TopOffManager, entry: ConfigEntry) -> list[Entity]:
    return [TopOffNumber(manager, entry, spec) for spec in NUMBERS]


@callback
@callback
def sensors(manager: TopOffManager, entry: ConfigEntry) -> list[Entity]:
    return [
        TopOffStateSensor(manager, entry),
        TopOffTimestampSensor(manager, entry, "last_full", "Last full charge"),
        TopOffTimestampSensor(manager, entry, "next_due", "Next top-off due"),
    ]


@callback
def buttons(manager: TopOffManager, entry: ConfigEntry) -> list[Entity]:
    return [TopOffButton(manager, entry, True), TopOffButton(manager, entry, False)]
