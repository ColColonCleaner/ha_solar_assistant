"""Tests of topoff.py, which imports nothing from Home Assistant.

Run: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import types
import unittest
from datetime import datetime, time, timedelta

_DIR = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "solar_assistant"
# A bare package object, so the relative imports resolve without __init__.py,
# which imports Home Assistant.
_pkg = types.ModuleType("sa_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("sa_pure", _pkg)
t = importlib.import_module("sa_pure.topoff")

NOW = datetime(2026, 11, 19, 23, 0)
QUARTERS = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)] + ["23:59"]


def status(**kw):
    return t.Status(**kw)


def settings(**kw):
    kw.setdefault("enabled", True)
    return t.Settings(**kw)


class Socs(unittest.TestCase):
    def test_packs_win_over_the_total(self):
        data = {"battery_1/state_of_charge": 74, "battery_2/state_of_charge": "89",
                "battery_3/state_of_charge": 76, "total/battery_state_of_charge": 80.0}
        self.assertEqual(t.battery_socs(data), [74.0, 89.0, 76.0])

    def test_total_without_packs(self):
        self.assertEqual(t.battery_socs({"total/battery_state_of_charge": 80.0}), [80.0])
        self.assertEqual(t.battery_socs({}), [])

    def test_full_needs_every_pack(self):
        self.assertTrue(t.is_full([100.0, 100.0, 100.0], 100))
        self.assertFalse(t.is_full([100.0, 99.0, 100.0], 100))
        self.assertFalse(t.is_full([100.0, None], 100))
        self.assertFalse(t.is_full([], 100))


class Window(unittest.TestCase):
    def test_equal_ends_mean_all_day(self):
        self.assertTrue(t.in_window(time(13, 0), time(0, 0), time(0, 0)))

    def test_plain_window(self):
        self.assertTrue(t.in_window(time(2, 0), time(1, 0), time(5, 0)))
        self.assertFalse(t.in_window(time(5, 0), time(1, 0), time(5, 0)))

    def test_window_over_midnight(self):
        self.assertTrue(t.in_window(time(23, 0), time(22, 0), time(6, 0)))
        self.assertTrue(t.in_window(time(3, 0), time(22, 0), time(6, 0)))
        self.assertFalse(t.in_window(time(12, 0), time(22, 0), time(6, 0)))


class Options(unittest.TestCase):
    def test_time_option(self):
        opts = ["Time", "Voltage", "State of charge", "Time and voltage", "Time and state of charge"]
        self.assertEqual(t.time_option(opts), "Time")
        self.assertEqual(t.time_option(["According to time", "State of charge"]), None)
        self.assertEqual(t.time_option(["Time only", "State of charge"]), "Time only")
        self.assertIsNone(t.time_option(["Time and voltage", "State of charge"]))
        self.assertIsNone(t.time_option(None))

    def test_enabled_option(self):
        self.assertEqual(t.enabled_option(["Disabled", "Enabled"]), "Enabled")
        self.assertIsNone(t.enabled_option(["Disabled"]))


class Writes(unittest.TestCase):
    def test_apply_order_and_values(self):
        writes = t.apply_values("inverter_1", 100.0, "Time", "Enabled")
        self.assertEqual(writes, [
            ("inverter_1/grid_charge_slot_1_start", "00:00"),
            ("inverter_1/grid_charge_slot_1_end", "23:59"),
            ("inverter_1/grid_charge_current", "100"),
            ("inverter_1/grid_charge_according_to", "Time"),
            ("inverter_1/grid_charge", "Enabled"),
        ])

    def test_restore_switches_the_mode_back_first(self):
        saved = {"grid_charge": "Enabled", "grid_charge_current": "0", "grid_charge_according_to": "State of charge",
                 "grid_charge_slot_1_start": "00:00", "grid_charge_slot_1_end": "00:00"}
        writes = t.restore_values("inverter_1", saved)
        self.assertEqual(writes[0], ("inverter_1/grid_charge_according_to", "State of charge"))
        self.assertEqual(writes[1], ("inverter_1/grid_charge_current", "0"))
        self.assertEqual(len(writes), 5)

    def test_restore_writes_only_saved_settings(self):
        self.assertEqual(t.restore_values("inverter_1", {"grid_charge_current": "0"}),
                         [("inverter_1/grid_charge_current", "0")])


class Decide(unittest.TestCase):
    def test_disabled(self):
        self.assertEqual(t.decide(status(), settings(enabled=False), NOW, False), (t.STATE_DISABLED, t.ACTION_NONE))

    def test_not_due(self):
        s = status(last_full=NOW - timedelta(days=3))
        self.assertEqual(t.decide(s, settings(), NOW, False), (t.STATE_IDLE, t.ACTION_NONE))

    def test_no_last_full_is_not_due(self):
        self.assertEqual(t.decide(status(), settings(), NOW, False), (t.STATE_IDLE, t.ACTION_NONE))

    def test_due_starts(self):
        s = status(last_full=NOW - timedelta(days=7, minutes=1))
        self.assertEqual(t.decide(s, settings(), NOW, False), (t.STATE_CHARGING, t.ACTION_START))

    def test_due_outside_the_window_waits(self):
        s = status(last_full=NOW - timedelta(days=8))
        cfg = settings(window_start=time(1, 0), window_end=time(5, 0))
        self.assertEqual(t.decide(s, cfg, NOW, False), (t.STATE_WAITING, t.ACTION_NONE))

    def test_start_now(self):
        self.assertEqual(t.decide(status(), settings(), NOW, False, start_now=True), (t.STATE_CHARGING, t.ACTION_START))

    def test_start_now_needs_the_switch(self):
        self.assertEqual(t.decide(status(), settings(enabled=False), NOW, False, start_now=True),
                         (t.STATE_DISABLED, t.ACTION_NONE))

    def test_full_moves_to_hold(self):
        s = status(state=t.STATE_CHARGING, started_at=NOW - timedelta(hours=1))
        self.assertEqual(t.decide(s, settings(), NOW, True), (t.STATE_HOLDING, t.ACTION_NONE))

    def test_full_with_no_hold_restores(self):
        s = status(state=t.STATE_CHARGING, started_at=NOW - timedelta(hours=1))
        self.assertEqual(t.decide(s, settings(hold_minutes=0), NOW, True), (t.STATE_IDLE, t.ACTION_RESTORE))

    def test_hold_ends(self):
        s = status(state=t.STATE_HOLDING, started_at=NOW - timedelta(hours=2), full_since=NOW - timedelta(minutes=30))
        self.assertEqual(t.decide(s, settings(hold_minutes=30), NOW, True), (t.STATE_IDLE, t.ACTION_RESTORE))
        s.full_since = NOW - timedelta(minutes=29)
        self.assertEqual(t.decide(s, settings(hold_minutes=30), NOW, False), (t.STATE_HOLDING, t.ACTION_NONE))

    def test_time_limit_restores_with_an_error(self):
        s = status(state=t.STATE_CHARGING, started_at=NOW - timedelta(hours=8))
        self.assertEqual(t.decide(s, settings(), NOW, False), (t.STATE_ERROR, t.ACTION_RESTORE))

    def test_switch_off_while_charging_restores(self):
        s = status(state=t.STATE_CHARGING, started_at=NOW)
        self.assertEqual(t.decide(s, settings(enabled=False), NOW, False), (t.STATE_DISABLED, t.ACTION_RESTORE))

    def test_stop_now(self):
        s = status(state=t.STATE_HOLDING, started_at=NOW, full_since=NOW)
        self.assertEqual(t.decide(s, settings(), NOW, True, stop_now=True), (t.STATE_IDLE, t.ACTION_RESTORE))

    def test_error_retries_a_failed_restore(self):
        s = status(state=t.STATE_ERROR, saved={"grid_charge_current": "0"})
        self.assertEqual(t.decide(s, settings(enabled=False), NOW, False), (t.STATE_ERROR, t.ACTION_RESTORE))

    def test_error_does_not_restart_by_itself(self):
        s = status(state=t.STATE_ERROR, last_full=NOW - timedelta(days=9))
        self.assertEqual(t.decide(s, settings(), NOW, False), (t.STATE_ERROR, t.ACTION_NONE))
        self.assertEqual(t.decide(s, settings(), NOW, False, start_now=True), (t.STATE_CHARGING, t.ACTION_START))

    def test_error_clears_when_the_switch_goes_off(self):
        s = status(state=t.STATE_ERROR)
        self.assertEqual(t.decide(s, settings(enabled=False), NOW, False), (t.STATE_DISABLED, t.ACTION_NONE))


if __name__ == "__main__":
    unittest.main()
