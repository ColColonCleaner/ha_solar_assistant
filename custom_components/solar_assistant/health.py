"""Update throttling, data staleness, battery SOC spread and device details.

No Home Assistant imports, so the tests run without it.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .precision import parse_numeric

_DEVICE_PREFIX = re.compile(r"^(inverter|battery)_\d+$")
_PACK_SOC = re.compile(r"^battery_\d+/state_of_charge$")


def crosses_zero(previous: Any, value: Any) -> bool:
    """Whether a value moved onto zero or off it, such as a grid frequency of 0 at an outage."""
    a = parse_numeric(previous)
    b = parse_numeric(value)
    if a is None or b is None:
        return a is not b
    return (a == 0) != (b == 0)


def should_dispatch(
    now: float,
    last_dispatch: float | None,
    interval_s: float,
    previous: Any,
    value: Any,
    platform: str | None,
) -> bool:
    """Whether an update of a known topic reaches its entity now (upstream issue #24).

    A setting always does, so a write shows at once. A sensor waits for the
    interval since its last update, except when its value crosses zero.
    """
    if interval_s <= 0 or last_dispatch is None:
        return True
    if platform not in (None, "sensor"):
        return True
    if crosses_zero(previous, value):
        return True
    return now - last_dispatch >= interval_s


def device_prefix(topic: str) -> str | None:
    """``inverter_1/pv_power`` gives ``inverter_1``. A topic of the totals or the system gives None."""
    prefix = topic.split("/", 1)[0]
    return prefix if _DEVICE_PREFIX.match(prefix) else None


def is_stale(last_change: datetime | None, now: datetime, stale_after_min: float) -> bool | None:
    """Whether a device sent no changed value for the set time (upstream issue #14).

    SolarAssistant repeats the last values while a cable is out, so the time of
    the last changed value is the signal, not the time of the last message.
    None means that no value arrived yet, or that the check is off.
    """
    if last_change is None or stale_after_min <= 0:
        return None
    return now - last_change >= timedelta(minutes=stale_after_min)


def pack_socs(data: dict[str, Any]) -> dict[str, float]:
    """The SOC of each battery pack that reports a number, by its topic prefix."""
    socs: dict[str, float] = {}
    for topic, value in sorted(data.items()):
        if _PACK_SOC.match(topic):
            number = parse_numeric(value)
            if number is not None:
                socs[topic.split("/", 1)[0]] = float(number)
    return socs


def soc_spread(socs: dict[str, float]) -> float | None:
    """The largest pack SOC minus the smallest, or None with fewer than two packs."""
    if len(socs) < 2:
        return None
    return max(socs.values()) - min(socs.values())


_DETAIL_WORDS = (
    ("serial_number", ("serial",)),
    ("model", ("model", "type")),
    ("sw_version", ("firmware", "software version", "version")),
)


def device_details(definitions: dict[str, dict[str, Any]], data: dict[str, Any], prefix: str) -> dict[str, str]:
    """The model, serial number and firmware of a device, from its Info metrics (upstream issue #11).

    SolarAssistant names these metrics per driver, so the match goes by words in
    the metric name. The first match of each kind wins, in topic order.
    """
    details: dict[str, str] = {}
    for topic in sorted(definitions):
        if not topic.startswith(prefix + "/"):
            continue
        defn = definitions[topic]
        if defn.get("group") != "Info":
            continue
        value = data.get(topic)
        if value is None or isinstance(value, (dict, list)) or str(value).strip() == "":
            continue
        name = str(defn.get("name") or "").casefold()
        for key, words in _DETAIL_WORDS:
            if key not in details and any(w in name for w in words):
                details[key] = str(value).strip()
                break
    return details
