"""Battery top-off from the grid: the decisions, with no Home Assistant imports.

When the batteries have not been full for a set number of days, the top-off
switches the grid charge of the inverter to a whole-day time slot at a set
current, waits until every battery reports full, holds there for a set time so
the BMS can balance the cells, and then writes back the settings it changed.

The writes target the grid-charge settings that SolarAssistant exposes for
Luxpower and EG4 inverters such as the EG4 12000XP. Its manual limits the AC
charge start SOC to 90%, so an SOC trigger cannot start a top-off from a higher
charge, and a time slot can.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

from .precision import parse_numeric

# Settings of the inverter that a top-off changes, relative to "inverter_N/".
GRID_CHARGE = "grid_charge"
CURRENT = "grid_charge_current"
ACCORDING_TO = "grid_charge_according_to"
SLOT_START = "grid_charge_slot_1_start"
SLOT_END = "grid_charge_slot_1_end"
REQUIRED = (GRID_CHARGE, CURRENT, ACCORDING_TO, SLOT_START, SLOT_END)

# The order of the writes. The slot and the current come first, so the inverter
# never charges with the old current. The restore switches the mode back first,
# so the charge stops before the rest changes.
APPLY_ORDER = (SLOT_START, SLOT_END, CURRENT, ACCORDING_TO, GRID_CHARGE)
RESTORE_ORDER = (ACCORDING_TO, CURRENT, SLOT_START, SLOT_END, GRID_CHARGE)

# A slot that starts at 00:00 reads as no slot on Luxpower and EG4 inverters,
# as an EG4 support reply relayed on the DIY Solar Power Forum and a Luxpower
# SNA owner report, so the whole-day slot starts at 00:01.
WHOLE_DAY = ("00:01", "23:59")

STATE_DISABLED = "disabled"
STATE_IDLE = "idle"
STATE_WAITING = "waiting"
STATE_CHARGING = "charging"
STATE_HOLDING = "holding"
STATE_ERROR = "error"
STATES = (STATE_DISABLED, STATE_IDLE, STATE_WAITING, STATE_CHARGING, STATE_HOLDING, STATE_ERROR)
ACTIVE_STATES = (STATE_CHARGING, STATE_HOLDING)

ACTION_NONE = "none"
ACTION_START = "start"
ACTION_RESTORE = "restore"

_PACK_SOC = re.compile(r"^battery_\d+/state_of_charge$")
TOTAL_SOC = "total/battery_state_of_charge"


@dataclass
class Settings:
    """The settings of the top-off, which entities of the integration edit."""

    enabled: bool = False
    interval_days: float = 7.0
    current_a: float = 100.0
    full_soc: float = 100.0
    hold_minutes: float = 30.0
    time_limit_hours: float = 8.0
    # A due top-off waits for the night, so it does not grid-charge while the
    # sun could charge.
    window_start: time = time(22, 0)
    window_end: time = time(6, 0)


@dataclass
class Status:
    """The state of the top-off, kept across restarts."""

    state: str = STATE_IDLE
    last_full: datetime | None = None
    started_at: datetime | None = None
    full_since: datetime | None = None
    saved: dict[str, str] = field(default_factory=dict)
    inverter: str = "inverter_1"
    last_error: str | None = None
    last_result: str | None = None


def battery_socs(data: dict[str, Any]) -> list[float | None]:
    """The SOC of each battery pack, or the total SOC when no pack reports one."""
    packs = [parse_numeric(v) for k, v in sorted(data.items()) if _PACK_SOC.match(k)]
    if packs:
        return [None if v is None else float(v) for v in packs]
    total = parse_numeric(data.get(TOTAL_SOC))
    return [] if total is None else [float(total)]


def is_full(socs: list[float | None], threshold: float) -> bool:
    """Whether every pack reports at least the threshold. A missing value is not full."""
    return bool(socs) and all(s is not None and s >= threshold for s in socs)


def in_window(now: time, start: time, end: time) -> bool:
    """Whether ``now`` is in the window. Equal ends mean the whole day. A window can wrap midnight."""
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def due_at(last_full: datetime | None, interval_days: float) -> datetime | None:
    """When the next top-off is due, or None without a last full charge."""
    if last_full is None:
        return None
    return last_full + timedelta(days=interval_days)


def pick_option(options: list[str] | None, wanted: str, avoid: tuple[str, ...] = ()) -> str | None:
    """The option equal to ``wanted``, ignoring case, else the one option that starts with it.

    An option that holds a word of ``avoid`` does not count, so ``Time`` never
    matches ``Time and state of charge``.
    """
    opts = [o for o in (options or []) if isinstance(o, str)]
    for o in opts:
        if o.strip().casefold() == wanted.casefold():
            return o
    starts = [
        o for o in opts
        if o.strip().casefold().startswith(wanted.casefold())
        and not any(a in o.casefold() for a in avoid)
    ]
    return starts[0] if len(starts) == 1 else None


def time_option(options: list[str] | None) -> str | None:
    """The option of ``grid_charge_according_to`` that charges by time alone."""
    return pick_option(options, "Time", avoid=(" and ", "+", "voltage", "soc", "state", "charge"))


def enabled_option(options: list[str] | None) -> str | None:
    """The option of ``grid_charge`` that enables it."""
    return pick_option(options, "Enabled") or pick_option(options, "Enable") or pick_option(options, "On")


def apply_values(inverter: str, current_a: float, time_opt: str, enable_opt: str) -> list[tuple[str, str]]:
    """The writes that start a top-off, in order."""
    values = {
        SLOT_START: WHOLE_DAY[0],
        SLOT_END: WHOLE_DAY[1],
        CURRENT: str(int(round(current_a))),
        ACCORDING_TO: time_opt,
        GRID_CHARGE: enable_opt,
    }
    return [(f"{inverter}/{key}", values[key]) for key in APPLY_ORDER]


def restore_values(inverter: str, saved: dict[str, str]) -> list[tuple[str, str]]:
    """The writes that undo a top-off, in order. Only saved settings are written."""
    return [(f"{inverter}/{key}", saved[key]) for key in RESTORE_ORDER if key in saved]


def decide(
    status: Status,
    settings: Settings,
    now: datetime,
    full: bool,
    *,
    start_now: bool = False,
    stop_now: bool = False,
) -> tuple[str, str]:
    """The next state and the action for the manager to take.

    ``now`` is local time, because the window is a time of day. The manager
    records a full charge before it calls this.
    """
    active = status.state in ACTIVE_STATES
    if active and (stop_now or not settings.enabled):
        return STATE_IDLE if settings.enabled else STATE_DISABLED, ACTION_RESTORE
    if active and status.started_at is not None:
        if now - status.started_at >= timedelta(hours=settings.time_limit_hours):
            return STATE_ERROR, ACTION_RESTORE
    if status.state == STATE_CHARGING:
        if full:
            if settings.hold_minutes <= 0:
                return STATE_IDLE, ACTION_RESTORE
            return STATE_HOLDING, ACTION_NONE
        return STATE_CHARGING, ACTION_NONE
    if status.state == STATE_HOLDING:
        since = status.full_since or now
        if now - since >= timedelta(minutes=settings.hold_minutes):
            return STATE_IDLE, ACTION_RESTORE
        return STATE_HOLDING, ACTION_NONE
    if status.state == STATE_ERROR and status.saved:
        # A restore that failed is retried before anything else.
        return STATE_ERROR, ACTION_RESTORE
    if not settings.enabled:
        return STATE_DISABLED, ACTION_NONE
    if start_now:
        return STATE_CHARGING, ACTION_START
    if status.state == STATE_ERROR:
        # A failed top-off would fail again. It waits for a manual start, or
        # for the switch to go off and on.
        return STATE_ERROR, ACTION_NONE
    due = due_at(status.last_full, settings.interval_days)
    if due is None or now < due or full:
        return STATE_IDLE, ACTION_NONE
    if not in_window(now.time(), settings.window_start, settings.window_end):
        return STATE_WAITING, ACTION_NONE
    return STATE_CHARGING, ACTION_START
