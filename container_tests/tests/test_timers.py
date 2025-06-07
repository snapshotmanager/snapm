import unittest
import logging
import os.path

import snapm
from snapm.manager._timers import (
    # low-level interface
    _timer,
    _TIMER_ENABLE,
    _TIMER_DISABLE,
    _TIMER_START,
    _TIMER_STOP,
    _UNIT_CREATE,
    _UNIT_GC,
    # public interface
    TimerType,
    TimerStatus,
    Timer,
)
from snapm.manager._calendar import CalendarSpec

log = logging.getLogger(__name__)


class TimerTests(unittest.TestCase):
    def assert_paths(self, unit: str, instance: str, enabled: bool):
        unit_path = (
            f"/etc/systemd/system/timers.target.wants/snapm-{unit}@{instance}.timer"
        )
        drop_in_dir = f"/etc/systemd/system/snapm-{unit}@{instance}.timer.d"
        drop_in_file = os.path.join(drop_in_dir, "10-oncalendar.conf")

        self.assertEqual(enabled, os.path.exists(unit_path))
        self.assertEqual(enabled, os.path.exists(drop_in_dir))
        self.assertEqual(enabled, os.path.exists(drop_in_file))

    def enable_assert_disable_timer(self, unit: str, instance: str, calendarspec: str):
        _timer(_TIMER_ENABLE, unit, instance, calendarspec=CalendarSpec(calendarspec))
        self.assert_paths(unit, instance, True)

        _timer(_TIMER_START, unit, instance)
        # assert unit status

        _timer(_TIMER_STOP, unit, instance)
        # assert unit status

        _timer(_TIMER_DISABLE, unit, instance)
        self.assert_paths(unit, instance, False)

    def test_create_timer_enable_daily(self):
        self.enable_assert_disable_timer(_UNIT_CREATE, "daily", "daily")

    def test_gc_timer_enable_daily(self):
        self.enable_assert_disable_timer(_UNIT_GC, "daily", "daily")

    def test_create_timer_enable_hourly(self):
        self.enable_assert_disable_timer(_UNIT_CREATE, "hourly", "hourly")

    def test_gc_timer_enable_hourly(self):
        self.enable_assert_disable_timer(_UNIT_GC, "hourly", "hourly")

    def test_create_timer_enable_weekly(self):
        self.enable_assert_disable_timer(_UNIT_CREATE, "weekly", "weekly")

    def test_gc_timer_enable_weekly(self):
        self.enable_assert_disable_timer(_UNIT_GC, "weekly", "weekly")

    def test_create_timer_enable_monthly(self):
        self.enable_assert_disable_timer(_UNIT_CREATE, "monthly", "monthly")

    def test_gc_timer_enable_monthly(self):
        self.enable_assert_disable_timer(_UNIT_GC, "monthly", "monthly")

    def test_create_timer_enable_thu_fri_1_5_11(self):
        self.enable_assert_disable_timer(
            _UNIT_CREATE, "thu_fri_1_5_11", "Thu,Fri *-*-1,5 11:12:13"
        )

    def test_gc_timer_enable_thu_fri_1_5_11(self):
        self.enable_assert_disable_timer(
            _UNIT_GC, "thu_fri_1_5_11", "Thu,Fri *-*-1,5 11:12:13"
        )

    def test_create_timer_enable_sat_sun_8(self):
        self.enable_assert_disable_timer(_UNIT_CREATE, "sat_sun_8", "Sat,Sun 08:05:40")

    def test_gc_timer_enable_sat_sun_8(self):
        self.enable_assert_disable_timer(_UNIT_GC, "sat_sun_8", "Sat,Sun 08:05:40")

    def test_timer_enable_empty_calendarspec_raises(self):
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            _timer(_TIMER_ENABLE, _UNIT_CREATE, "hourly", calendarspec="")
        self.assertEqual(cm.exception.args, ("Timer ENABLE requires non-empty calendarspec",))

    def test_timer_enable_bad_calendarspec_raises(self):
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            _timer(_TIMER_ENABLE, _UNIT_CREATE, "hourly", calendarspec="quux:foo")
        self.assertEqual(cm.exception.args, ("Timer ENABLE: invalid calendarspec string",))

    def test_timer_bad_op_raises(self):
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            _timer("QUUX", _UNIT_CREATE, "hourly", calendarspec="hourly")
        self.assertEqual(cm.exception.args, ("Invalid timer operation: QUUX",))

    def test_timer_bad_unit_raises(self):
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            _timer(_TIMER_ENABLE, "quux", "hourly", calendarspec="hourly")
        self.assertEqual(cm.exception.args, ("Invalid timer unit: quux",))

    def test_timer_empty_instance_raises(self):
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            _timer(_TIMER_ENABLE, _UNIT_CREATE, "", calendarspec="hourly")
        self.assertEqual(cm.exception.args, ("Timer instance cannot be empty",))

    def test_timer_none_instance_raises(self):
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            _timer(_TIMER_ENABLE, _UNIT_CREATE, None, calendarspec="hourly")
        self.assertEqual(cm.exception.args, ("Timer instance cannot be empty",))

    def test_timer_START_with_calendarspec_raises(self):
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            _timer(_TIMER_START, _UNIT_CREATE, "hourly", calendarspec="hourly")
        self.assertEqual(cm.exception.args, ("Timer START does not accept calendarspec=",))

    def test_timer_STOP_with_calendarspec_raises(self):
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            _timer(_TIMER_STOP, _UNIT_CREATE, "hourly", calendarspec="hourly")
        self.assertEqual(cm.exception.args, ("Timer STOP does not accept calendarspec=",))

    def test_timer_DISABLE_with_calendarspec_raises(self):
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            _timer(_TIMER_DISABLE, _UNIT_CREATE, "hourly", calendarspec="hourly")
        self.assertEqual(cm.exception.args, ("Timer DISABLE does not accept calendarspec=",))

    def test_Timer_valid_type_and_calendar_string(self):
        t = Timer(TimerType.CREATE, "hourly", "hourly")
        self.assertTrue(t is not None)
        self.assertEqual(t.status, TimerStatus.DISABLED)

    def test_Timer_valid_type_and_CalendarSpec(self):
        cs = CalendarSpec("hourly")
        t = Timer(TimerType.CREATE, "hourly", cs)
        self.assertTrue(t is not None)
        self.assertEqual(t.status, TimerStatus.DISABLED)

    def test_Timer_valid_type_invalid_calendar_string_raises(self):
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            t = Timer(TimerType.CREATE, "hourly", "quux")
            self.assertTrue("invalid calendarspec string" in cm.exception)

    def test_Timer_invalid_type_raises(self):
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            t = Timer("NotATimerType", "hourly", "hourly")
            self.assertTrue("Invalid timer type" in cm.exception)

    def timer_assert_enable_start_stop_disable(self, t):
        self.assertEqual(t.status, TimerStatus.DISABLED)

        t.enable()
        self.assertEqual(t.status, TimerStatus.ENABLED)
        self.assertTrue(t.enabled)

        t.start()
        self.assertEqual(t.status, TimerStatus.RUNNING)
        self.assertTrue(t.running)

        self.assertTrue(t.next_elapse is not None)
        self.assertTrue(t.from_now is not None)
        self.assertTrue(t.occurs)

        t.stop()
        self.assertEqual(t.status, TimerStatus.ENABLED)

        t.disable()
        self.assertEqual(t.status, TimerStatus.DISABLED)

    def test_Timer_CREATE_enable_start_stop_disable_hourly(self):
        t = Timer(TimerType.CREATE, "hourly", "hourly")
        self.timer_assert_enable_start_stop_disable(t)

    def test_Timer_GC_enable_start_stop_disable_hourly(self):
        t = Timer(TimerType.GC, "hourly", "hourly")
        self.timer_assert_enable_start_stop_disable(t)

    def test_Timer_CREATE_enable_start_stop_disable_daily(self):
        t = Timer(TimerType.CREATE, "daily", "daily")
        self.timer_assert_enable_start_stop_disable(t)

    def test_Timer_GC_enable_start_stop_disable_daily(self):
        t = Timer(TimerType.GC, "daily", "daily")
        self.timer_assert_enable_start_stop_disable(t)

    def test_Timer_CREATE_enable_start_stop_disable_weekly(self):
        t = Timer(TimerType.CREATE, "weekly", "weekly")
        self.timer_assert_enable_start_stop_disable(t)

    def test_Timer_GC_enable_start_stop_disable_weekly(self):
        t = Timer(TimerType.GC, "weekly", "weekly")
        self.timer_assert_enable_start_stop_disable(t)

    def test_Timer_CREATE_enable_start_stop_disable_monthly(self):
        t = Timer(TimerType.CREATE, "monthly", "monthly")
        self.timer_assert_enable_start_stop_disable(t)

    def test_Timer_GC_enable_start_stop_disable_monthly(self):
        t = Timer(TimerType.GC, "monthly", "monthly")
        self.timer_assert_enable_start_stop_disable(t)
