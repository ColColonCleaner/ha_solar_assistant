"""Tests of health.py, which imports nothing from Home Assistant.

Run: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta

_DIR = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "solar_assistant"
_pkg = types.ModuleType("sa_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("sa_pure", _pkg)
h = importlib.import_module("sa_pure.health")

NOW = datetime(2026, 11, 19, 20, 0)


class Throttle(unittest.TestCase):
    def test_off(self):
        self.assertTrue(h.should_dispatch(10.0, 9.0, 0, 1, 2, "sensor"))

    def test_first_update(self):
        self.assertTrue(h.should_dispatch(10.0, None, 30, 1, 2, "sensor"))

    def test_inside_the_interval(self):
        self.assertFalse(h.should_dispatch(10.0, 0.0, 30, 100, 120, "sensor"))
        self.assertFalse(h.should_dispatch(10.0, 0.0, 30, 100, 120, None))

    def test_after_the_interval(self):
        self.assertTrue(h.should_dispatch(30.0, 0.0, 30, 100, 120, "sensor"))

    def test_zero_crossing_passes(self):
        self.assertTrue(h.should_dispatch(10.0, 0.0, 30, 59.98, 0, "sensor"))
        self.assertTrue(h.should_dispatch(10.0, 0.0, 30, "0", "12 W", "sensor"))

    def test_settings_always_pass(self):
        self.assertTrue(h.should_dispatch(10.0, 0.0, 30, 1, 2, "number"))

    def test_crosses_zero(self):
        self.assertFalse(h.crosses_zero(5, 6))
        self.assertFalse(h.crosses_zero(0, 0.0))
        self.assertTrue(h.crosses_zero(None, 3))
        self.assertFalse(h.crosses_zero(None, None))


class Staleness(unittest.TestCase):
    def test_prefix(self):
        self.assertEqual(h.device_prefix("inverter_1/pv_power"), "inverter_1")
        self.assertEqual(h.device_prefix("battery_12/voltage"), "battery_12")
        self.assertIsNone(h.device_prefix("total/pv_power"))
        self.assertIsNone(h.device_prefix("system/site_id"))

    def test_stale(self):
        self.assertTrue(h.is_stale(NOW - timedelta(minutes=10), NOW, 10))
        self.assertFalse(h.is_stale(NOW - timedelta(minutes=9), NOW, 10))
        self.assertIsNone(h.is_stale(None, NOW, 10))
        self.assertIsNone(h.is_stale(NOW, NOW, 0))


class Spread(unittest.TestCase):
    def test_spread(self):
        data = {"battery_1/state_of_charge": 74, "battery_2/state_of_charge": "89",
                "battery_3/state_of_charge": 76, "total/battery_state_of_charge": 80}
        socs = h.pack_socs(data)
        self.assertEqual(socs, {"battery_1": 74.0, "battery_2": 89.0, "battery_3": 76.0})
        self.assertEqual(h.soc_spread(socs), 15.0)

    def test_one_pack_has_no_spread(self):
        self.assertIsNone(h.soc_spread({"battery_1": 50.0}))

    def test_text_is_left_out(self):
        self.assertEqual(h.pack_socs({"battery_1/state_of_charge": "n/a"}), {})


class Owner(unittest.TestCase):
    INV = {"topic": "inverter_1/pv_power", "device": "inverters", "number": 0}
    TOT = {"topic": "total/pv_power", "device": "totals", "number": None}

    def test_one_inverter_takes_the_totals(self):
        defs = {"inverter_1/pv_power": self.INV, "total/pv_power": self.TOT}
        self.assertEqual(h.device_owner(self.TOT, defs), ({"device": "inverters", "number": 0}, "inverter_1"))

    def test_two_inverters_keep_the_totals_apart(self):
        defs = {"inverter_1/pv_power": self.INV, "inverter_2/pv_power": dict(self.INV, number=1),
                "total/pv_power": self.TOT}
        self.assertEqual(h.device_owner(self.TOT, defs), ({"device": "totals", "number": None}, "total"))

    def test_no_inverter_yet_keeps_the_totals_apart(self):
        self.assertEqual(h.device_owner(self.TOT, {"total/pv_power": self.TOT}),
                         ({"device": "totals", "number": None}, "total"))

    def test_other_devices_keep_their_own(self):
        bat = {"topic": "battery_2/voltage", "device": "batteries", "number": 1}
        self.assertEqual(h.device_owner(bat, {"inverter_1/pv_power": self.INV}),
                         ({"device": "batteries", "number": 1}, "battery_2"))


class MergedName(unittest.TestCase):
    DEFS = {
        "inverter_1/pv_power": {"name": "PV power"},
        "inverter_1/temperature": {"name": "Temperature"},
        "total/pv_power": {"name": "PV power"},
        "total/battery_temperature": {"name": "Battery temperature"},
    }

    def test_a_unique_name_stays(self):
        self.assertEqual(h.merged_name("Battery temperature", "inverter_1", self.DEFS), "Battery temperature")
        self.assertEqual(h.merged_name("AC output frequency", "inverter_1", self.DEFS), "AC output frequency")

    def test_a_name_of_the_inverter_gets_the_prefix(self):
        self.assertEqual(h.merged_name("PV power", "inverter_1", self.DEFS), "Total PV power")
        self.assertEqual(h.merged_name("pv POWER", "inverter_1", self.DEFS), "Total pv POWER")


class DuplicateTotal(unittest.TestCase):
    INV = {"topic": "inverter_1/pv_power", "device": "inverters", "number": 0, "name": "PV power"}
    TOT_PV = {"topic": "total/pv_power", "device": "totals", "number": None, "name": "PV power"}
    TOT_TEMP = {"topic": "total/battery_temperature", "device": "totals", "number": None, "name": "Battery temperature"}

    def test_one_inverter(self):
        defs = {"inverter_1/pv_power": self.INV, "total/pv_power": self.TOT_PV, "total/battery_temperature": self.TOT_TEMP}
        self.assertTrue(h.is_duplicate_total(self.TOT_PV, defs))
        self.assertFalse(h.is_duplicate_total(self.TOT_TEMP, defs))
        self.assertFalse(h.is_duplicate_total(self.INV, defs))

    def test_two_inverters_keep_every_total(self):
        defs = {"inverter_1/pv_power": self.INV, "inverter_2/pv_power": dict(self.INV, number=1),
                "total/pv_power": self.TOT_PV}
        self.assertFalse(h.is_duplicate_total(self.TOT_PV, defs))


class Details(unittest.TestCase):
    DEFS = {
        "inverter_1/serial_number": {"group": "Info", "name": "Serial number"},
        "inverter_1/model": {"group": "Info", "name": "Model"},
        "inverter_1/firmware_version": {"group": "Info", "name": "Firmware version"},
        "inverter_1/pv_power": {"group": "Status", "name": "PV power"},
        "inverter_10/serial_number": {"group": "Info", "name": "Serial number"},
    }
    DATA = {
        "inverter_1/serial_number": "1234567890",
        "inverter_1/model": "SNA-US 12000",
        "inverter_1/firmware_version": "FAAB-2525",
        "inverter_1/pv_power": 3167,
        "inverter_10/serial_number": "other",
    }

    def test_details(self):
        self.assertEqual(h.device_details(self.DEFS, self.DATA, "inverter_1"), {
            "serial_number": "1234567890", "model": "SNA-US 12000", "sw_version": "FAAB-2525"})

    def test_no_info(self):
        self.assertEqual(h.device_details(self.DEFS, {}, "inverter_1"), {})
        self.assertEqual(h.device_details(self.DEFS, self.DATA, "battery_1"), {})


if __name__ == "__main__":
    unittest.main()
