"""Value parsing, display precision and number steps for SolarAssistant metrics.

SolarAssistant sends a unit, a device class and the limits of each metric, but no
step and no precision. This module derives both. It imports nothing from Home
Assistant, so its tests run without it.
"""
from __future__ import annotations

import math
import re
from datetime import time
from typing import Any

# Decimals shown for a sensor, by its unit. A unit that is not listed keeps the
# default of Home Assistant.
_SENSOR_PRECISION_BY_UNIT: dict[str, int] = {
    "V": 1,
    "mV": 0,
    "A": 1,
    "mA": 0,
    "W": 0,
    "kW": 2,
    "VA": 0,
    "kVA": 2,
    "var": 0,
    "Wh": 0,
    "kWh": 2,
    "MWh": 3,
    "Hz": 2,
    "°C": 1,
    "°F": 1,
    "%": 0,
    "Ah": 1,
    "mAh": 0,
    "MB": 0,
    "GB": 1,
    "s": 0,
    "min": 0,
    "h": 1,
    "Ω": 3,
    "mΩ": 1,
}

# A cell voltage and a cell imbalance are a few volts or a few millivolts, so a
# volt topic with one of these words shows 3 decimals.
_FINE_VOLTAGE_WORDS = ("cell", "imbalance")

# The step of a number setting, by its unit. The decimals of the limits and of
# the value can make the step finer, never coarser.
_NUMBER_STEP_BY_UNIT: dict[str, float] = {
    "V": 0.1,
    "A": 1.0,
    "W": 1.0,
    "kW": 0.1,
    "kWh": 0.1,
    "Hz": 0.01,
    "%": 1.0,
    "°C": 1.0,
    "°F": 1.0,
    "s": 1.0,
    "min": 1.0,
    "h": 1.0,
}

_MAX_STEP_DECIMALS = 3

# Units that SolarAssistant gives to a text value.
_TEXT_UNITS = frozenset({"hhmm"})

# Units that the Luxpower driver of SolarAssistant 2026-08-25 labels wrong, by
# topic suffix: (the unit sent, the unit of the value). The smart load start PV
# power of an EG4 12000XP holds kW (register 217 _12K_HOLD_START_PV_POWER), and
# SolarAssistant sends W with the range 0 to 25.5.
_UNIT_FIXES: dict[str, tuple[str, str]] = {
    "/smart_load_start_pv_power": ("W", "kW"),
}

_NUMBER_PREFIX = re.compile(r"^\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_numeric(value: Any) -> float | int | None:
    """Return the number in ``value``, or None.

    A unit inside the value, as in ``"230 V"`` or ``"50 Hz"``, is dropped
    (upstream issue #31). A bool, a blank string or a string with no leading
    number gives None, so Home Assistant shows ``unknown`` instead of raising
    on every update.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, str):
        m = _NUMBER_PREFIX.match(value)
        if not m:
            return None
        text = m.group(1)
        try:
            number = float(text)
        except ValueError:
            return None
        if not math.isfinite(number):
            return None
        if re.fullmatch(r"[-+]?\d+", text):
            return int(text)
        return number
    return None


def metric_unit(defn: dict[str, Any]) -> str | None:
    """The unit of a metric, preferring its Home Assistant unit.

    A text unit such as ``hhmm`` is no unit for Home Assistant, because a unit
    makes Home Assistant require a numeric state.
    """
    unit = defn.get("unit_of_measurement") or defn.get("unit") or None
    if unit in _TEXT_UNITS:
        return None
    topic = str(defn.get("topic") or "")
    for suffix, (wrong, right) in _UNIT_FIXES.items():
        if topic.endswith(suffix) and unit == wrong:
            return right
    return unit


def is_numeric_metric(defn: dict[str, Any]) -> bool:
    """Whether Home Assistant treats a sensor of this metric as numeric."""
    return bool(metric_unit(defn) or defn.get("state_class"))


def sensor_display_precision(topic: str, defn: dict[str, Any]) -> int | None:
    """Decimals to show for a sensor, or None for the Home Assistant default."""
    unit = metric_unit(defn)
    if unit is None:
        return None
    if unit == "V" and any(w in topic.lower() for w in _FINE_VOLTAGE_WORDS):
        return 3
    return _SENSOR_PRECISION_BY_UNIT.get(unit)


def decimals(value: Any) -> int:
    """Decimals that a number needs, from its shortest text form, up to a cap."""
    number = parse_numeric(value)
    if number is None or isinstance(number, int):
        return 0
    text = repr(float(number))
    if "e" in text or "E" in text:
        return _MAX_STEP_DECIMALS
    if "." not in text:
        return 0
    frac = text.split(".", 1)[1].rstrip("0")
    return min(len(frac), _MAX_STEP_DECIMALS)


def number_step(defn: dict[str, Any], value: Any = None) -> float:
    """The step of a number setting.

    The unit gives the base step (upstream issue #21). The decimals of the
    limits and of the current value make it finer: a range of 0 to 25.5 gets a
    step of 0.1 whatever its unit says.
    """
    unit = metric_unit(defn)
    step = _NUMBER_STEP_BY_UNIT.get(unit or "", 1.0)
    places = max(decimals(defn.get("min")), decimals(defn.get("max")), decimals(value))
    if places:
        step = min(step, 10.0 ** -places)
    return round(step, _MAX_STEP_DECIMALS)


def format_setting(value: float, step: float) -> str:
    """The text of a number write, rounded to the decimals of its step.

    A whole step writes an integer, ``"100"`` and not ``"100.0"``.
    """
    places = decimals(step)
    if places == 0:
        return str(int(round(value)))
    return f"{round(value, places):.{places}f}"


def is_time_metric(defn: dict[str, Any]) -> bool:
    """Whether a select metric holds a time of day.

    SolarAssistant sends each time setting as a select whose options are
    ``HH:MM`` strings (upstream issue #28).
    """
    if defn.get("platform") != "select":
        return False
    options = defn.get("options") or []
    return bool(options) and all(isinstance(o, str) and _HHMM.match(o) for o in options)


_ON_WORDS = ("enabled", "enable", "on", "yes", "true")
_OFF_WORDS = ("disabled", "disable", "off", "no", "false")


def toggle_options(defn: dict[str, Any]) -> tuple[str, str] | None:
    """The (on, off) options of a select that is a toggle, or None.

    A select with exactly two options, one of them Enabled and the other
    Disabled, ignoring case, is a switch in Home Assistant.
    """
    if defn.get("platform") != "select":
        return None
    options = [o for o in (defn.get("options") or []) if isinstance(o, str)]
    if len(options) != 2:
        return None
    on = [o for o in options if o.strip().casefold() in _ON_WORDS]
    off = [o for o in options if o.strip().casefold() in _OFF_WORDS]
    if len(on) == 1 and len(off) == 1:
        return on[0], off[0]
    return None


def is_toggle_metric(defn: dict[str, Any]) -> bool:
    """Whether a select metric is a toggle, which the switch platform owns."""
    return toggle_options(defn) is not None


def parse_hhmm(value: Any) -> time | None:
    """``"07:30"`` to a time, or None."""
    if not isinstance(value, str):
        return None
    m = _HHMM.match(value.strip())
    if not m:
        return None
    return time(int(m.group(1)), int(m.group(2)))


def format_hhmm(value: time) -> str:
    """A time to ``"HH:MM"``, the form that SolarAssistant writes."""
    return f"{value.hour:02d}:{value.minute:02d}"


def select_options(defn: dict[str, Any], value: Any) -> list[str]:
    """The options of a select, with the current value added when it is missing.

    A value outside the list made the select ``unknown`` (upstream issue #34).
    """
    options = [str(o) for o in (defn.get("options") or [])]
    if value is not None:
        current = str(value)
        if current and current not in options:
            options.append(current)
    return options
