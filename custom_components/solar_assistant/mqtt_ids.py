"""Pairs of sensors of the entry and deleted SolarAssistant MQTT entities of the same topic.

A unit that published through MQTT before this integration left one MQTT entity
per topic. Once the MQTT device is deleted, the sensor of the same topic can take
over its entity ID, and the recorder then continues the history of that ID.

No Home Assistant imports, so the tests run without it.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

MQTT_PLATFORM = "mqtt"

# The MQTT discovery of SolarAssistant gives each entity this unique ID, such
# as SolarAssistant_inverter_1_pv_power for the topic inverter_1/pv_power.
_MQTT_UNIQUE_ID_PREFIX = "SolarAssistant_"


def mqtt_unique_id(topic: str) -> str:
    """The unique ID of the SolarAssistant MQTT entity of a topic."""
    return _MQTT_UNIQUE_ID_PREFIX + topic.replace("/", "_")


def plan_adoption(
    sensors: Iterable[tuple[str, str]],
    twins: dict[str, str],
    is_registered: Callable[[str], bool],
    done: set[str],
) -> tuple[list[tuple[str, str, str]], set[str]]:
    """The renames that give each sensor the entity ID of its deleted MQTT twin.

    ``sensors`` holds the entity ID and the topic of each sensor of the entry,
    and ``twins`` the entity ID of the deleted MQTT entity of each topic. A topic
    in ``done`` was adopted before and stays as it is, so a later rename by hand
    holds. A target ID that is registered, or that an earlier rename of the plan
    takes, is skipped.

    Returns the renames as (entity ID, new entity ID, topic), and the topics
    whose sensor already carries the ID of its twin.
    """
    renames: list[tuple[str, str, str]] = []
    matched: set[str] = set()
    planned: set[str] = set()
    for entity_id, topic in sorted(sensors, key=lambda s: s[1]):
        if topic in done:
            continue
        target = twins.get(topic)
        if target is None:
            continue
        if target == entity_id:
            matched.add(topic)
            continue
        if target in planned or is_registered(target):
            continue
        renames.append((entity_id, target, topic))
        planned.add(target)
    return renames, matched
