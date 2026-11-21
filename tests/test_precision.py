"""Tests of precision.py, which imports nothing from Home Assistant.

Run: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest
from datetime import time

_PATH = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "solar_assistant" / "precision.py"
_SPEC = importlib.util.spec_from_file_location("sa_precision", _PATH)
p = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(p)


class ParseNumeric(unittest.TestCase):
    def test_numbers_pass_through(self):
        self.assertEqual(p.parse_numeric(53.9), 53.9)
        self.assertEqual(p.parse_numeric(74), 74)

    def test_unit_inside_the_value_is_dropped(self):
        # upstream issue #31
        self.assertEqual(p.parse_numeric("230 V"), 230)
        self.assertEqual(p.parse_numeric("50 Hz"), 50)
        self.assertEqual(p.parse_numeric("59.98Hz"), 59.98)
        self.assertEqual(p.parse_numeric("-1105.5 W"), -1105.5)

    def test_text_gives_none(self):
        for value in (None, "", "Solar/Battery", "n/a", True, False, float("nan"), "inf"):
            self.assertIsNone(p.parse_numeric(value), value)


class SensorPrecision(unittest.TestCase):
    def test_voltage_shows_one_decimal(self):
        self.assertEqual(p.sensor_display_precision("total/battery_voltage", {"unit": "V"}), 1)

    def test_cell_voltage_and_imbalance_show_three(self):
        self.assertEqual(p.sensor_display_precision("total/battery_cell_voltage_-_highest", {"unit": "V"}), 3)
        self.assertEqual(p.sensor_display_precision("total/battery_cell_imbalance_-_average", {"unit": "V"}), 3)

    def test_units(self):
        cases = {"W": 0, "kWh": 2, "Hz": 2, "°C": 1, "%": 0, "A": 1, "MB": 0}
        for unit, places in cases.items():
            self.assertEqual(p.sensor_display_precision("total/x", {"unit_of_measurement": unit}), places, unit)

    def test_unknown_unit_keeps_the_default(self):
        self.assertIsNone(p.sensor_display_precision("total/x", {"unit": "furlong"}))
        self.assertIsNone(p.sensor_display_precision("total/x", {}))

    def test_text_unit_is_no_unit(self):
        self.assertIsNone(p.metric_unit({"unit": "hhmm"}))
        self.assertFalse(p.is_numeric_metric({"unit": "hhmm"}))
        self.assertTrue(p.is_numeric_metric({"unit": "V"}))
        self.assertTrue(p.is_numeric_metric({"state_class": "measurement"}))


class NumberStep(unittest.TestCase):
    def test_voltage_step(self):
        # upstream issue #21: 57.4V could not be set with a step of 1
        self.assertEqual(p.number_step({"unit": "V", "min": 40, "max": 59}, 57.4), 0.1)
        self.assertEqual(p.number_step({"unit": "V", "min": 40, "max": 59}, 54.0), 0.1)

    def test_whole_units(self):
        self.assertEqual(p.number_step({"unit": "A", "min": 0, "max": 1500}, 150.0), 1.0)
        self.assertEqual(p.number_step({"unit": "%", "min": 0, "max": 100}, 50), 1.0)

    def test_decimals_of_the_limits_make_the_step_finer(self):
        # inverter_1/smart_load_start_pv_power: unit W, range 0 to 25.5
        self.assertEqual(p.number_step({"unit": "W", "min": 0, "max": 25.5}, 0.0), 0.1)

    def test_decimals_of_the_value_make_the_step_finer(self):
        self.assertEqual(p.number_step({"unit": "A", "min": 0, "max": 100}, 12.5), 0.1)

    def test_cap(self):
        self.assertEqual(p.number_step({"unit": "V"}, 3.123456), 0.001)


class FormatSetting(unittest.TestCase):
    def test_whole_step_writes_an_integer(self):
        self.assertEqual(p.format_setting(100.0, 1.0), "100")

    def test_fine_step_rounds(self):
        self.assertEqual(p.format_setting(57.4000000001, 0.1), "57.4")
        self.assertEqual(p.format_setting(59.98, 0.01), "59.98")


class Times(unittest.TestCase):
    QUARTERS = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)] + ["23:59"]

    def test_time_metric(self):
        self.assertTrue(p.is_time_metric({"platform": "select", "options": self.QUARTERS}))
        self.assertFalse(p.is_time_metric({"platform": "select", "options": ["Enabled", "Disabled"]}))
        self.assertFalse(p.is_time_metric({"platform": "select", "options": []}))
        self.assertFalse(p.is_time_metric({"platform": "number", "options": self.QUARTERS}))

    def test_parse_and_format(self):
        self.assertEqual(p.parse_hhmm("07:05"), time(7, 5))
        self.assertEqual(p.parse_hhmm(" 23:59 "), time(23, 59))
        self.assertIsNone(p.parse_hhmm("24:00"))
        self.assertIsNone(p.parse_hhmm(None))
        self.assertEqual(p.format_hhmm(time(7, 5)), "07:05")


class Toggles(unittest.TestCase):
    def test_enabled_disabled_is_a_toggle(self):
        self.assertEqual(p.toggle_options({"platform": "select", "options": ["Disabled", "Enabled"]}),
                         ("Enabled", "Disabled"))
        self.assertTrue(p.is_toggle_metric({"platform": "select", "options": ["enabled", "DISABLED"]}))

    def test_other_selects_are_not(self):
        self.assertIsNone(p.toggle_options({"platform": "select", "options": ["Time", "State of charge"]}))
        self.assertIsNone(p.toggle_options({"platform": "select", "options": ["Enabled", "Disabled", "Auto"]}))
        self.assertIsNone(p.toggle_options({"platform": "select", "options": ["Enabled", "Enabled"]}))
        self.assertIsNone(p.toggle_options({"platform": "number", "options": ["Enabled", "Disabled"]}))


class UnitFixes(unittest.TestCase):
    def test_smart_load_pv_power_is_kw(self):
        defn = {"topic": "inverter_1/smart_load_start_pv_power", "unit": "W", "min": 0, "max": 25.5}
        self.assertEqual(p.metric_unit(defn), "kW")
        self.assertEqual(p.number_step(defn, 0.0), 0.1)

    def test_other_topics_keep_their_unit(self):
        self.assertEqual(p.metric_unit({"topic": "inverter_1/generator_power", "unit": "W"}), "W")


class SelectOptions(unittest.TestCase):
    def test_missing_value_is_added(self):
        # upstream issue #34
        self.assertEqual(p.select_options({"options": ["A", "B"]}, "C"), ["A", "B", "C"])

    def test_known_value_is_not_repeated(self):
        self.assertEqual(p.select_options({"options": ["A", "B"]}, "B"), ["A", "B"])

    def test_no_value(self):
        self.assertEqual(p.select_options({"options": ["A"]}, None), ["A"])


class BooleanMetrics(unittest.TestCase):
    # Rows of GET /api/v1/metrics of a Luxpower SNA unit, SolarAssistant 2026-08-25.
    QUICK = {"topic": "inverter_1/quick_charge", "unit": "Enabled", "group": "Settings", "platform": "sensor"}
    PEAK = {"topic": "inverter_1/grid_peak_shaving", "group": "Settings", "platform": "sensor"}
    MODE = {"topic": "inverter_1/device_mode", "group": "Status", "platform": "sensor"}

    def test_a_bool_value_is_boolean(self):
        self.assertTrue(p.is_boolean_metric(self.PEAK, False))
        self.assertTrue(p.is_boolean_metric(self.PEAK, True))
        self.assertTrue(p.is_boolean_metric({"topic": "inverter_1/ac_couple_active"}, False))

    def test_the_unit_enabled_is_boolean_before_a_value_arrives(self):
        self.assertTrue(p.is_boolean_metric(self.QUICK, None))
        self.assertTrue(p.is_boolean_metric(self.QUICK, False))

    def test_text_and_numbers_are_not_boolean(self):
        self.assertFalse(p.is_boolean_metric(self.MODE, "Solar/Battery"))
        self.assertFalse(p.is_boolean_metric({"topic": "inverter_1/battery_packs"}, 1))
        self.assertFalse(p.is_boolean_metric({"topic": "inverter_1/generator"}, "Disabled"))

    def test_a_setting_with_a_control_is_not_a_binary_sensor(self):
        for platform in ("number", "select", "switch"):
            self.assertFalse(p.is_boolean_metric(dict(self.QUICK, platform=platform), False))

    def test_the_unit_enabled_is_no_unit_for_home_assistant(self):
        # v0.5.5 and v0.5.6 passed the unit on, so the sensor took the bool as a number and showed unknown.
        self.assertIsNone(p.metric_unit(self.QUICK))
        self.assertFalse(p.is_numeric_metric(self.QUICK))

    def test_state(self):
        self.assertIs(p.boolean_state(True), True)
        self.assertIs(p.boolean_state(False), False)
        self.assertIs(p.boolean_state("Enabled"), True)
        self.assertIs(p.boolean_state("disabled"), False)
        self.assertIs(p.boolean_state(1), True)
        self.assertIs(p.boolean_state(0), False)
        self.assertIsNone(p.boolean_state(None))
        self.assertIsNone(p.boolean_state("Solar/Battery"))
        self.assertIsNone(p.boolean_state(2))


if __name__ == "__main__":
    unittest.main()
