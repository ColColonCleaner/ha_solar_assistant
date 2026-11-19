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


class SelectOptions(unittest.TestCase):
    def test_missing_value_is_added(self):
        # upstream issue #34
        self.assertEqual(p.select_options({"options": ["A", "B"]}, "C"), ["A", "B", "C"])

    def test_known_value_is_not_repeated(self):
        self.assertEqual(p.select_options({"options": ["A", "B"]}, "B"), ["A", "B"])

    def test_no_value(self):
        self.assertEqual(p.select_options({"options": ["A"]}, None), ["A"])


if __name__ == "__main__":
    unittest.main()
