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


class HasEntity(unittest.TestCase):
    def test_settings_always(self):
        for platform in ("number", "select", "switch"):
            self.assertTrue(h.has_entity("inverter_1/x", {"platform": platform}, lambda t: False))

    def test_sensor_when_enabled(self):
        self.assertTrue(h.has_entity("inverter_1/pv_power", {"platform": "sensor"}, lambda t: True))
        self.assertTrue(h.has_entity("inverter_1/pv_power", {}, lambda t: True))
        self.assertFalse(h.has_entity("inverter_1/battery_power", {"platform": "sensor"}, lambda t: False))

    def test_ignored_group(self):
        self.assertFalse(h.has_entity("inverter_1/x", {"platform": "sensor", "group": "Ignore"}, lambda t: True))


class MergedName(unittest.TestCase):
    DEFS = {
        "inverter_1/pv_power": {"name": "PV power"},
        "inverter_1/temperature": {"name": "Temperature"},
        "inverter_1/battery_state_of_charge": {"name": "Battery state of charge"},
        "total/pv_power": {"name": "PV power"},
        "total/battery_temperature": {"name": "Battery temperature"},
        "total/battery_state_of_charge": {"name": "Battery state of charge"},
    }
    ENTITIES = {"inverter_1/pv_power", "inverter_1/temperature"}

    def test_a_unique_name_stays(self):
        self.assertEqual(h.merged_name("Battery temperature", "inverter_1", self.DEFS, self.ENTITIES), "Battery temperature")
        self.assertEqual(h.merged_name("AC output frequency", "inverter_1", self.DEFS, self.ENTITIES), "AC output frequency")

    def test_a_name_of_an_inverter_entity_gets_the_prefix(self):
        self.assertEqual(h.merged_name("PV power", "inverter_1", self.DEFS, self.ENTITIES), "Total PV power")
        self.assertEqual(h.merged_name("pv POWER", "inverter_1", self.DEFS, self.ENTITIES), "Total pv POWER")

    def test_an_inverter_metric_without_an_entity_takes_no_name(self):
        self.assertEqual(h.merged_name("Battery state of charge", "inverter_1", self.DEFS, self.ENTITIES),
                         "Battery state of charge")
        self.assertEqual(h.merged_name("Battery state of charge", "inverter_1", self.DEFS,
                                       self.ENTITIES | {"inverter_1/battery_state_of_charge"}),
                         "Total Battery state of charge")


class DuplicateTotal(unittest.TestCase):
    INV = {"topic": "inverter_1/pv_power", "device": "inverters", "number": 0, "name": "PV power"}
    INV_SOC = {"topic": "inverter_1/battery_state_of_charge", "device": "inverters", "number": 0,
               "name": "Battery state of charge"}
    TOT_PV = {"topic": "total/pv_power", "device": "totals", "number": None, "name": "PV power"}
    TOT_SOC = {"topic": "total/battery_state_of_charge", "device": "totals", "number": None,
               "name": "Battery state of charge"}
    TOT_TEMP = {"topic": "total/battery_temperature", "device": "totals", "number": None, "name": "Battery temperature"}

    def defs(self):
        return {"inverter_1/pv_power": self.INV, "inverter_1/battery_state_of_charge": self.INV_SOC,
                "total/pv_power": self.TOT_PV, "total/battery_state_of_charge": self.TOT_SOC,
                "total/battery_temperature": self.TOT_TEMP}

    def test_one_inverter(self):
        entities = {"inverter_1/pv_power"}
        self.assertTrue(h.is_duplicate_total(self.TOT_PV, self.defs(), entities))
        self.assertFalse(h.is_duplicate_total(self.TOT_TEMP, self.defs(), entities))
        self.assertFalse(h.is_duplicate_total(self.INV, self.defs(), entities))

    def test_twin_without_an_entity_keeps_the_total(self):
        self.assertFalse(h.is_duplicate_total(self.TOT_PV, self.defs(), set()))

    def test_same_name_outside_the_compared_totals_keeps_the_total(self):
        # v0.5.2 removed total/battery_state_of_charge because an inverter
        # definition had its name, although that metric had no entity.
        self.assertFalse(h.is_duplicate_total(self.TOT_SOC, self.defs(), {"inverter_1/pv_power"}))
        self.assertFalse(h.is_duplicate_total(
            self.TOT_SOC, self.defs(), {"inverter_1/pv_power", "inverter_1/battery_state_of_charge"}))

    def test_two_inverters_keep_every_total(self):
        defs = {"inverter_1/pv_power": self.INV, "inverter_2/pv_power": dict(self.INV, number=1),
                "total/pv_power": self.TOT_PV}
        self.assertFalse(h.is_duplicate_total(self.TOT_PV, defs, {"inverter_1/pv_power", "inverter_2/pv_power"}))


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
