"""Runs the battery top-off of one config entry.

The manager records each full charge, asks topoff.decide() what to do each
minute, writes the grid-charge settings of the inverter through the
coordinator, and keeps its settings and state in a Store, so a restart in the
middle of a top-off still restores the inverter settings.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import topoff as t
from .const import DOMAIN
from .coordinator import SolarAssistantCoordinator

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
TICK = timedelta(seconds=60)
SAVE_DELAY_S = 10
_DATETIME_FIELDS = ("last_full", "started_at", "full_since", "discharging_since")
_POWER_TOPICS = (t.TOTAL_BATTERY_POWER, t.TOTAL_BATTERY_CURRENT)


class TopOffManager:
    """The battery top-off of one SolarAssistant unit."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: SolarAssistantCoordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.settings = t.Settings()
        self.status = t.Status()
        self.socs: list[float | None] = []
        self.signal = f"{DOMAIN}_{entry.entry_id}_topoff"
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.topoff")
        self._lock = asyncio.Lock()
        self._unsubs: list[Any] = []

    # -- persistence --

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        # A stored setting that the dataclass no longer has, such as the window of
        # 0.4, is left out.
        for f in fields(t.Settings):
            if f.name in data.get("settings", {}):
                setattr(self.settings, f.name, data["settings"][f.name])
        for f in fields(t.Status):
            if f.name in data.get("status", {}):
                value = data["status"][f.name]
                if f.name in _DATETIME_FIELDS and value:
                    value = dt_util.parse_datetime(value)
                setattr(self.status, f.name, value)
        if self.status.last_full is None:
            # No record yet: count from now, so an install never starts a
            # top-off at once. The start button runs one on demand.
            self.status.last_full = dt_util.utcnow()
            self._save()

    def _data(self) -> dict[str, Any]:
        settings = asdict(self.settings)
        status = asdict(self.status)
        for name in _DATETIME_FIELDS:
            status[name] = status[name].isoformat() if status[name] else None
        return {"settings": settings, "status": status}

    def _save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY_S)

    # -- life cycle --

    @callback
    def async_start(self) -> None:
        self._unsubs.append(async_track_time_interval(self.hass, self._async_interval, TICK))
        self._unsubs.append(
            async_dispatcher_connect(self.hass, self.coordinator.signal_metric_update, self._on_metric)
        )
        self.entry.async_create_background_task(self.hass, self.async_tick(), "solar_assistant_topoff_first_tick")

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    async def _async_interval(self, _now: datetime) -> None:
        await self.async_tick()

    @callback
    def _on_metric(self, topic: str) -> None:
        if "state_of_charge" in topic:
            self._record_full(dt_util.utcnow())
        elif topic in _POWER_TOPICS:
            self._record_discharge(dt_util.utcnow())

    def _record_discharge(self, now: datetime) -> None:
        """Keep the start of the running discharge, and save it when it starts or ends."""
        since = t.update_discharging(self.status.discharging_since, now, t.battery_power(self.coordinator.data))
        if since != self.status.discharging_since:
            self.status.discharging_since = since
            self._save()
            async_dispatcher_send(self.hass, self.signal)

    # -- settings, from the entities --

    async def async_set(self, **changes: Any) -> None:
        for name, value in changes.items():
            setattr(self.settings, name, value)
        if changes.get("enabled") is False and self.status.state == t.STATE_ERROR and not self.status.saved:
            self.status.last_error = None
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        self._save()
        await self.async_tick()

    # -- the cycle --

    def _record_full(self, now: datetime) -> bool:
        self.socs = t.battery_socs(self.coordinator.data)
        full = t.is_full(self.socs, self.settings.full_soc)
        if full:
            self.status.last_full = now
            self._save()
        async_dispatcher_send(self.hass, self.signal)
        return full

    async def async_tick(self, *, start_now: bool = False, stop_now: bool = False) -> None:
        async with self._lock:
            now = dt_util.utcnow()
            full = self._record_full(now)
            self._record_discharge(now)
            state, action = t.decide(
                self.status, self.settings, now, full,
                start_now=start_now, stop_now=stop_now,
            )
            if action == t.ACTION_START:
                await self._start(now)
            elif action == t.ACTION_RESTORE:
                if stop_now:
                    why = "stopped by Stop top-off"
                elif not self.settings.enabled:
                    why = "stopped by the switch"
                elif state == t.STATE_ERROR:
                    why = "stopped at the time limit"
                else:
                    why = "finished"
                await self._restore(state, now, why)
            else:
                if state == t.STATE_HOLDING and self.status.state == t.STATE_CHARGING:
                    self.status.full_since = now
                self.status.state = state
            self._save()
            async_dispatcher_send(self.hass, self.signal)

    async def _start(self, now: datetime) -> None:
        inv = self.status.inverter
        defs = self.coordinator.definitions
        data = self.coordinator.data
        missing = [k for k in t.REQUIRED if f"{inv}/{k}" not in defs]
        if missing:
            self._fail(f"the unit does not offer {', '.join(missing)} on {inv}")
            return
        if not self.coordinator.is_connected:
            self._fail("the unit is not connected")
            return
        time_opt = t.time_option(defs[f"{inv}/{t.ACCORDING_TO}"].get("options"))
        enable_opt = t.enabled_option(defs[f"{inv}/{t.GRID_CHARGE}"].get("options"))
        if time_opt is None or enable_opt is None:
            self._fail(
                "no time option in "
                f"{defs[f'{inv}/{t.ACCORDING_TO}'].get('options')} or no enable option in "
                f"{defs[f'{inv}/{t.GRID_CHARGE}'].get('options')}"
            )
            return
        saved = {k: str(data[f"{inv}/{k}"]) for k in t.REQUIRED if data.get(f"{inv}/{k}") is not None}
        if len(saved) != len(t.REQUIRED):
            self._fail("a current grid-charge setting is unknown, so it could not be restored")
            return
        # The saved values reach the Store before the first write, so a crash
        # after any write still restores them.
        self.status.saved = saved
        self.status.started_at = now
        self.status.full_since = None
        self.status.state = t.STATE_CHARGING
        self.status.last_error = None
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        await self._store.async_save(self._data())
        for topic, value in t.apply_values(inv, self.settings.current_a, time_opt, enable_opt):
            if str(data.get(topic)) == value:
                continue
            try:
                await self.coordinator.set_setting(topic, value)
            except HomeAssistantError as err:
                self._fail(f"the write {topic} = {value} failed: {err}")
                return
        self._notify(
            f"Battery top-off started: grid charge at {int(round(self.settings.current_a))}A until every battery "
            f"reports {int(self.settings.full_soc)}%, then {int(self.settings.hold_minutes)} min of hold. "
            f"Saved settings: {saved}"
        )

    async def _restore(self, state: str, now: datetime, why: str) -> None:
        failed: list[str] = []
        for topic, value in t.restore_values(self.status.inverter, self.status.saved):
            if str(self.coordinator.data.get(topic)) == value:
                continue
            try:
                await self.coordinator.set_setting(topic, value)
            except HomeAssistantError as err:
                failed.append(f"{topic} = {value}: {err}")
        if failed:
            self._fail("restore failed, retried each minute: " + "; ".join(failed))
            return
        started = self.status.started_at
        self.status.saved = {}
        self.status.started_at = None
        self.status.full_since = None
        self.status.state = state
        minutes = round((now - started).total_seconds() / 60) if started else None
        took = f" after {minutes} min" if minutes is not None else ""
        if state == t.STATE_ERROR:
            self.status.last_error = (
                f"the batteries did not reach {self.settings.full_soc:g}% within {self.settings.time_limit_hours:g} h"
            )
            self.status.last_result = f"{why}{took}: {self.status.last_error}"
            self._raise_issue(self.status.last_error)
        else:
            self.status.last_result = f"{why}{took}"
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        self._notify(f"Battery top-off {self.status.last_result}. The inverter settings are restored.")

    @property
    def _issue_id(self) -> str:
        return f"topoff_failed_{self.entry.entry_id}"

    def _raise_issue(self, reason: str) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="topoff_failed",
            translation_placeholders={"title": self.entry.title, "reason": reason},
        )

    def _fail(self, reason: str) -> None:
        _LOGGER.warning("Battery top-off: %s", reason)
        self.status.state = t.STATE_ERROR
        self.status.last_error = reason
        self._raise_issue(reason)
        self._notify(f"Battery top-off error: {reason}")

    def _notify(self, message: str) -> None:
        persistent_notification.async_create(
            self.hass, message, title=f"{self.entry.title}: battery top-off",
            notification_id=f"{DOMAIN}_{self.entry.entry_id}_topoff",
        )

    # -- read by the entities --

    @property
    def next_due(self) -> datetime | None:
        return t.due_at(self.status.last_full, self.settings.interval_days)
