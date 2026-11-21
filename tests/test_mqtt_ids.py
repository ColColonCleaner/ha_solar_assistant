"""Tests of mqtt_ids.py, which imports nothing from Home Assistant.

Run: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import types
import unittest

_DIR = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "solar_assistant"
_pkg = types.ModuleType("sa_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("sa_pure", _pkg)
m = importlib.import_module("sa_pure.mqtt_ids")


def nothing_registered(_entity_id: str) -> bool:
    return False


class UniqueId(unittest.TestCase):
    def test_topic_to_unique_id(self):
        self.assertEqual(m.mqtt_unique_id("inverter_1/pv_power"), "SolarAssistant_inverter_1_pv_power")
        self.assertEqual(m.mqtt_unique_id("total/battery_energy_out"), "SolarAssistant_total_battery_energy_out")


class Plan(unittest.TestCase):
    SENSORS = [
        ("sensor.eg4_inverter_pv_power", "inverter_1/pv_power"),
        ("sensor.ecoworthy_battery_2_power", "battery_2/power"),
        ("sensor.eg4_battery_energy_out", "total/battery_energy_out"),
        ("sensor.solarassistant_soc_spread", "soc_spread"),
    ]
    TWINS = {
        "inverter_1/pv_power": "sensor.eg4_pv_power",
        "battery_2/power": "sensor.eg4_power_2",
        "total/battery_energy_out": "sensor.eg4_battery_energy_out",
    }

    def test_renames_and_matches(self):
        renames, matched = m.plan_adoption(self.SENSORS, self.TWINS, nothing_registered, set())
        self.assertEqual(renames, [
            ("sensor.ecoworthy_battery_2_power", "sensor.eg4_power_2", "battery_2/power"),
            ("sensor.eg4_inverter_pv_power", "sensor.eg4_pv_power", "inverter_1/pv_power"),
        ])
        self.assertEqual(matched, {"total/battery_energy_out"})

    def test_done_topic_stays(self):
        # A sensor renamed by hand after its adoption keeps the new ID.
        sensors = [("sensor.pv_main", "inverter_1/pv_power")]
        renames, matched = m.plan_adoption(sensors, self.TWINS, nothing_registered, {"inverter_1/pv_power"})
        self.assertEqual((renames, matched), ([], set()))

    def test_registered_target_is_skipped(self):
        renames, _ = m.plan_adoption(self.SENSORS, self.TWINS, lambda e: e == "sensor.eg4_pv_power", set())
        self.assertEqual([r[0] for r in renames], ["sensor.ecoworthy_battery_2_power"])

    def test_one_target_once(self):
        sensors = [("sensor.a", "inverter_1/pv_power"), ("sensor.b", "inverter_1/pv_power")]
        renames, _ = m.plan_adoption(sensors, self.TWINS, nothing_registered, set())
        self.assertEqual(len(renames), 1)

    def test_no_twin_no_rename(self):
        renames, matched = m.plan_adoption([("sensor.x", "battery_9/voltage")], self.TWINS, nothing_registered, set())
        self.assertEqual((renames, matched), ([], set()))


if __name__ == "__main__":
    unittest.main()
