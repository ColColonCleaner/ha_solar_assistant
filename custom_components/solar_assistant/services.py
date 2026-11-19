"""Actions of the SolarAssistant integration.

They are registered once in async_setup, so an automation that names them
validates while no entry is loaded, and each call checks its entry.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_TOPIC,
    ATTR_VALUE,
    DOMAIN,
    SERVICE_SET_SETTING,
    SERVICE_START_TOPOFF,
    SERVICE_STOP_TOPOFF,
)

_ENTRY = {vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}
SET_SETTING_SCHEMA = vol.Schema(
    {**_ENTRY, vol.Required(ATTR_TOPIC): cv.string, vol.Required(ATTR_VALUE): vol.Any(cv.string, vol.Coerce(str))}
)
TOPOFF_SCHEMA = vol.Schema(_ENTRY)

_WRITABLE = ("number", "select", "switch")


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> Any:
    entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_loaded",
            translation_placeholders={"title": entry.title},
        )
    return entry.runtime_data


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    async def set_setting(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        topic = call.data[ATTR_TOPIC].strip()
        defn = coordinator.definitions.get(topic)
        if defn is None or defn.get("platform") not in _WRITABLE:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_a_setting",
                translation_placeholders={"topic": topic},
            )
        value = str(call.data[ATTR_VALUE])
        options = defn.get("options")
        if defn.get("platform") == "select" and options and value not in options:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_option",
                translation_placeholders={"topic": topic, "value": value, "options": ", ".join(map(str, options))},
            )
        await coordinator.set_setting(topic, value)

    async def start_topoff(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        if not coordinator.topoff.settings.enabled:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="topoff_disabled")
        await coordinator.topoff.async_tick(start_now=True)

    async def stop_topoff(call: ServiceCall) -> None:
        await _coordinator(hass, call).topoff.async_tick(stop_now=True)

    hass.services.async_register(DOMAIN, SERVICE_SET_SETTING, set_setting, schema=SET_SETTING_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_START_TOPOFF, start_topoff, schema=TOPOFF_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STOP_TOPOFF, stop_topoff, schema=TOPOFF_SCHEMA)
