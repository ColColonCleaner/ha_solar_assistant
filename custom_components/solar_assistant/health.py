"""Update throttling, data staleness, battery SOC spread and device details.

No Home Assistant imports, so the tests run without it.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Collection
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


_INVERTER_PREFIX = re.compile(r"^inverter_\d+$")


def device_owner(defn: dict[str, Any], definitions: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], str]:
    """The device that a metric belongs to, as a device/number pair, and the topic prefix of that device.

    A unit with one inverter puts its totals on the inverter device, because they
    repeat the inverter and add the battery and energy totals. With two or more
    inverters, or before any inverter metric is known, the totals keep a device
    of their own.
    """
    owner = {"device": defn.get("device"), "number": defn.get("number")}
    prefix = str(defn.get("topic") or "").split("/", 1)[0]
    if defn.get("device") != "totals":
        return owner, prefix
    inverters = sorted({
        t.split("/", 1)[0] for t in definitions if _INVERTER_PREFIX.match(t.split("/", 1)[0])
    })
    if len(inverters) != 1:
        return owner, prefix
    sample = next(d for t, d in sorted(definitions.items()) if t.startswith(inverters[0] + "/"))
    return {"device": sample.get("device"), "number": sample.get("number")}, inverters[0]


_SETTING_PLATFORMS = ("number", "select", "switch")

# Totals whose value equals the metric of the same topic name of the only
# inverter. Each pair agreed in every update of 24 h of recorder data on an
# EG4 12000XP (Projects T0323 A015). No other total was compared.
_SAME_AS_INVERTER = ("pv_power", "load_power", "grid_power")


def has_entity(topic: str, defn: dict[str, Any], sensor_enabled: Callable[[str], bool]) -> bool:
    """Whether a metric gets an entity of the entry.

    Each setting does. A read-only metric does when its topic is enabled, which
    ``sensor_enabled`` decides from the options of the entry.
    """
    platform = defn.get("platform")
    if platform in _SETTING_PLATFORMS:
        return True
    return platform in (None, "sensor") and defn.get("group") != "Ignore" and sensor_enabled(topic)


def is_duplicate_total(
    defn: dict[str, Any], definitions: dict[str, dict[str, Any]], entity_topics: Collection[str]
) -> bool:
    """Whether a total repeats an entity of the only inverter, such as total/pv_power.

    Only a total in ``_SAME_AS_INVERTER`` qualifies, and only while the inverter
    metric of the same topic name has an entity, so no value loses its entity.
    v0.5.2 compared names against every inverter definition instead, and so
    removed 13 totals whose inverter twin has no entity.
    """
    owner, prefix = device_owner(defn, definitions)
    if owner.get("device") == defn.get("device"):
        return False
    suffix = str(defn.get("topic") or "").split("/", 1)[-1]
    return suffix in _SAME_AS_INVERTER and f"{prefix}/{suffix}" in entity_topics


def merged_name(
    name: str, owner_prefix: str, definitions: dict[str, dict[str, Any]], entity_topics: Collection[str]
) -> str:
    """The name of a total on the inverter device.

    It keeps its own name, such as "Battery temperature". Only a name that an
    entity of the inverter already has reads "Total ...", so no two sensors of
    the device share a name. An inverter metric without an entity takes no name
    on the device, so it does not count.
    """
    taken = {
        str(d.get("name") or "").casefold()
        for t, d in definitions.items()
        if t.startswith(owner_prefix + "/") and t in entity_topics
    }
    return f"Total {name}" if name.casefold() in taken else name


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
