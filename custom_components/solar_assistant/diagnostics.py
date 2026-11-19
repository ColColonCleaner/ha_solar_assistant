"""Diagnostics download of a SolarAssistant entry: every topic with its definition and value.

The definitions carry the option list of each select, which the recorder does
not keep, so the download shows what each setting accepts.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, CONF_PASSWORD, CONF_SITE_KEY, CONF_TOKEN
from .coordinator import SolarAssistantConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_API_KEY, CONF_TOKEN, CONF_SITE_KEY}


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SolarAssistantConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    topoff = coordinator.topoff
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "connection": {
            "connected": coordinator.is_connected,
            "host": coordinator.connected_host,
            "last_connected_at": _iso(coordinator.last_connected_at),
            "last_error": coordinator.last_error,
        },
        "last_change": {k: _iso(v) for k, v in sorted(coordinator.last_change.items())},
        "topics": {
            topic: {"definition": coordinator.definitions[topic], "value": coordinator.data.get(topic)}
            for topic in sorted(coordinator.definitions)
        },
        "topoff": None if topoff is None else {
            "settings": {k: _iso(v) for k, v in asdict(topoff.settings).items()},
            "status": {k: _iso(v) for k, v in asdict(topoff.status).items()},
            "battery_soc": topoff.socs,
        },
    }
