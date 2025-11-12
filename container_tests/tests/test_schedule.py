from datetime import datetime, timedelta
from json import JSONDecodeError
from math import floor
import tempfile
import unittest
import logging
import os

import snapm
from snapm.manager._schedule import (
    Schedule,
    GcPolicy,
    GcPolicyType,
    GcPolicyParams,
    GcPolicyParamsAll,
    GcPolicyParamsCount,
    GcPolicyParamsAge,
    GcPolicyParamsTimeline,
)
from snapm.manager._calendar import CalendarSpec

log = logging.getLogger(__name__)

unexpected_kwarg = "got an unexpected keyword argument"

_VAR_TMP = "/var/tmp"

class MockSnapshotSet:

    boot_entry = None
    revert_entry = None

    def __init__(self, name, timestamp):
        self.name = name
        self.timestamp = timestamp
        self.deleted = False

    @property
    def datetime(self):
        return datetime.fromtimestamp(self.timestamp)

    def delete(self):
        self.deleted = True


class MockArgs(object):
    identifier = None
    debug = None
    name = None
    new_name = None
    name_prefixes = False
    no_headings = False
    options = ""
    members = False
    sort = ""
    rows = False
    separator = None
    uuid = None
    verbose = 0
    version = False
    json = False
    autoindex = False
    schedule_name = None
    policy_type = None
    keep_count = 0
    keep_years = 0
    keep_months = 0
    keep_weeks = 0
    keep_days = 0
    keep_yearly = 0
    keep_quarterly = 0
    keep_monthly = 0
    keep_weekly = 0
    keep_daily = 0
    keep_hourly = 0


# Return a list of six weeks of weekly MockSnapshotSet, sorted from
# oldest to newest.
def get_six_weeks():
    # Pretend the most recent MockSnapshotSet is ~5 minutes ago.
    nowish = datetime.now() - timedelta(minutes=5)
    return [
        MockSnapshotSet(f"test.{index}", (nowish - timedelta(weeks=weeks)).timestamp())
        for index, weeks in enumerate(range(5, -1, -1))
    ]


# Return a list of 18 months of hourly MockSnapshotSet, sorted from
# oldest to newest.
def get_eighteen_months():
    # Pretend the most recent MockSnapshotSet is ~midnight last Monday
    now = datetime.now()
    mon_midnight = now - timedelta(days=now.weekday(), hours=now.hour, minutes=now.minute)
    hours_in_eighteen_months = floor((365.25 + 6 * 30.44) * 24)
    return [
        MockSnapshotSet(f"test.{index}", (mon_midnight - timedelta(hours=hours)).timestamp())
        for index, hours in enumerate(range(hours_in_eighteen_months, -1, -1))
    ]


class GcPolicyTests(unittest.TestCase):
    def test_gcpolicy_bad_name(self):
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            gcp = GcPolicy("", GcPolicyType.ALL, {})
        self.assertEqual(cm.exception.args, ("GcPolicy name cannot be empty",))

    def test_gcpolicy_bad_type(self):
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            gcp = GcPolicy("test", "quux", {})
        self.assertEqual(cm.exception.args, ("Invalid GcPolicyType: quux",))

    def test_GcPolicyParamsAll_str(self):
        gcparams = GcPolicyParamsAll()
        self.assertEqual(str(gcparams), "")

    def test_GcPolicyParamsCount_str(self):
        gcparams = GcPolicyParamsCount(keep_count=5)
        self.assertEqual(str(gcparams), "keep_count=5")

    def test_GcPolicyParamsAge_str(self):
        gcparams = GcPolicyParamsAge(
            keep_years=1,
            keep_months=1,
            keep_weeks=1,
            keep_days=1
        )
        xstr = "keep_years=1, keep_months=1, keep_weeks=1, keep_days=1"
        self.assertEqual(str(gcparams), xstr)

        gcparams = GcPolicyParamsAge(keep_days=14)
        self.assertEqual(str(gcparams), "keep_days=14")

        gcparams = GcPolicyParamsAge(keep_years=1, keep_days=1)
        self.assertEqual(str(gcparams), "keep_years=1, keep_days=1")

    def test_GcPolicyParamsTimeline_str(self):
        gcparams = GcPolicyParamsTimeline(
            keep_yearly=1,
            keep_monthly=1,
            keep_weekly=1,
            keep_daily=1
        )
        xstr = "keep_yearly=1, keep_monthly=1, keep_weekly=1, keep_daily=1"
        self.assertEqual(str(gcparams), xstr)

        gcparams = GcPolicyParamsTimeline(keep_daily=14)
        self.assertEqual(str(gcparams), "keep_daily=14")

        gcparams = GcPolicyParamsTimeline(keep_yearly=1, keep_daily=1)
        self.assertEqual(str(gcparams), "keep_yearly=1, keep_daily=1")

    def test_GcPolicyParamsAll_to_dict(self):
        gcparams = GcPolicyParamsAll()
        self.assertEqual(gcparams.to_dict(), {})

    def test_GcPolicyParamsCount_to_dict(self):
        gcparams = GcPolicyParamsCount(keep_count=5)
        self.assertEqual(gcparams.to_dict(), {"keep_count": 5})

    def test_GcPolicyParamsAge_to_dict(self):
        gcparams = GcPolicyParamsAge(
            keep_years=1,
            keep_months=1,
            keep_weeks=1,
            keep_days=1
        )
        xdict = {"keep_years": 1, "keep_months": 1, "keep_weeks": 1, "keep_days": 1}
        self.assertEqual(gcparams.to_dict(), xdict)

        gcparams = GcPolicyParamsAge(keep_days=14)
        xdict = {"keep_years": 0, "keep_months": 0, "keep_weeks": 0, "keep_days": 14}
        self.assertEqual(gcparams.to_dict(), xdict)

    def test_GcPolicyParamsTimeline_to_dict(self):
        gcparams = GcPolicyParamsTimeline(
            keep_yearly=1,
            keep_quarterly=1,
            keep_monthly=1,
            keep_weekly=1,
            keep_daily=1,
            keep_hourly=1,
        )
        xdict = {
            "keep_hourly": 1,
            "keep_daily": 1,
            "keep_weekly": 1,
            "keep_monthly": 1,
            "keep_quarterly": 1,
            "keep_yearly": 1
        }
        self.assertEqual(gcparams.to_dict(), xdict)

        gcparams = GcPolicyParamsTimeline(keep_daily=14)
        xdict = {
            "keep_hourly": 0,
            "keep_daily": 14,
            "keep_weekly": 0,
            "keep_monthly": 0,
            "keep_quarterly": 0,
            "keep_yearly": 0
        }
        self.assertEqual(gcparams.to_dict(), xdict)

    def test_GcPolicy_all(self):
        gcp = GcPolicy("test", GcPolicyType.ALL, {})
        self.assertEqual(GcPolicyType.ALL, gcp.type)
        self.assertIsInstance(gcp.params, GcPolicyParamsAll)

    def test_GcPolicy_all_bad_params(self):
        with self.assertRaises(TypeError) as cm:
            gcp = GcPolicy("test", GcPolicyType.ALL, {"quux": "foo"})
        self.assertTrue(unexpected_kwarg in cm.exception.args[0])

    def test_GcPolicy_count(self):
        gcp = GcPolicy("test", GcPolicyType.COUNT, {"keep_count": 5})
        self.assertEqual(GcPolicyType.COUNT, gcp.type)
        self.assertIsInstance(gcp.params, GcPolicyParamsCount)
        self.assertTrue(hasattr(gcp.params, "keep_count"))
        self.assertEqual(gcp.params.keep_count, 5)

    def test_GcPolicy_count_bad_params(self):
        with self.assertRaises(TypeError) as cm:
            gcp = GcPolicy("test", GcPolicyType.COUNT, {"keep_number": 42})
        self.assertTrue(unexpected_kwarg in cm.exception.args[0])

    def test_GcPolicy_age(self):
        gcp = GcPolicy(
            "test",
            GcPolicyType.AGE,
            {"keep_years": 1, "keep_months": 12, "keep_weeks": 4, "keep_days": 7}
        )
        self.assertEqual(GcPolicyType.AGE, gcp.type)
        self.assertIsInstance(gcp.params, GcPolicyParamsAge)
        self.assertTrue(hasattr(gcp.params, "keep_years"))
        self.assertTrue(hasattr(gcp.params, "keep_months"))
        self.assertTrue(hasattr(gcp.params, "keep_weeks"))
        self.assertTrue(hasattr(gcp.params, "keep_days"))
        self.assertEqual(gcp.params.keep_years, 1)
        self.assertEqual(gcp.params.keep_months, 12)
        self.assertEqual(gcp.params.keep_weeks, 4)
        self.assertEqual(gcp.params.keep_days, 7)

    def test_GcPolicy_age_bad_params(self):
        with self.assertRaises(TypeError) as cm:
            gcp = GcPolicy("test", GcPolicyType.AGE, {"keep_hourss": 42})
        self.assertTrue(unexpected_kwarg in cm.exception.args[0])

    def test_GcPolicy_timeline(self):
        gcp = GcPolicy(
            "test",
            GcPolicyType.TIMELINE,
            {"keep_yearly": 1, "keep_monthly": 2, "keep_weekly": 3, "keep_daily": 4}
        )
        self.assertEqual(GcPolicyType.TIMELINE, gcp.type)
        self.assertIsInstance(gcp.params, GcPolicyParamsTimeline)
        self.assertTrue(hasattr(gcp.params, "keep_yearly"))
        self.assertTrue(hasattr(gcp.params, "keep_monthly"))
        self.assertTrue(hasattr(gcp.params, "keep_weekly"))
        self.assertTrue(hasattr(gcp.params, "keep_daily"))
        self.assertEqual(gcp.params.keep_yearly, 1)
        self.assertEqual(gcp.params.keep_monthly, 2)
        self.assertEqual(gcp.params.keep_weekly, 3)
        self.assertEqual(gcp.params.keep_daily, 4)

    def test_GcPolicy_timeline_bad_params(self):
        with self.assertRaises(TypeError) as cm:
            gcp = GcPolicy("test", GcPolicyType.TIMELINE, {"keep_dayly": 7})
        self.assertTrue(unexpected_kwarg in cm.exception.args[0])

    def test_GcPolicyParamsAll_json(self):
        xjson = '{}'
        gcpp = GcPolicyParamsAll()
        self.assertEqual(gcpp.json(), xjson)

    def test_GcPolicyParamsCount_json(self):
        xjson = '{"keep_count": 4}'
        gcpp = GcPolicyParamsCount(keep_count=4)
        self.assertEqual(gcpp.json(), xjson)

    def test_GcPolicyParamsAge_json(self):
        xjson = '{"keep_years": 1, "keep_months": 6, "keep_weeks": 4, "keep_days": 0}'
        gcpp = GcPolicyParamsAge(keep_years=1, keep_months=6, keep_weeks=4)
        self.assertEqual(gcpp.json(), xjson)

    def test_GcPolicyParamsTimeline(self):
        xjson = (
            '{"keep_yearly": 1, "keep_quarterly": 0, "keep_monthly": 6, '
            '"keep_weekly": 4, "keep_daily": 0, "keep_hourly": 0}'
        )
        gcpp = GcPolicyParamsTimeline(keep_yearly=1, keep_monthly=6, keep_weekly=4)
        self.assertEqual(gcpp.json(), xjson)

    def test_GcPolicy_ALL_json(self):
        xjson = '{"policy_name": "test", "policy_type": "ALL"}'
        gcp = GcPolicy("test", GcPolicyType.ALL, {})
        self.assertEqual(gcp.json(), xjson)

    def test_GcPolicy_COUNT_json(self):
        xjson = '{"policy_name": "test", "policy_type": "COUNT", "keep_count": 4}'
        gcp = GcPolicy("test", GcPolicyType.COUNT, {"keep_count": 4})
        self.assertEqual(gcp.json(), xjson)

    def test_GcPolicy_AGE_json(self):
        xjson = (
            '{"policy_name": "test", "policy_type": "AGE", '
            '"keep_years": 0, "keep_months": 0, "keep_weeks": 4, "keep_days": 0}'
        )
        gcp = GcPolicy("test", GcPolicyType.AGE, {"keep_weeks": 4})
        self.assertEqual(gcp.json(), xjson)

    def test_GcPolicy_TIMELINE_json(self):
        xjson = (
            '{"policy_name": "test", "policy_type": "TIMELINE", '
            '"keep_yearly": 1, "keep_quarterly": 0, "keep_monthly": 3, '
            '"keep_weekly": 0, "keep_daily": 0, "keep_hourly": 0}'
        )
        gcp = GcPolicy("test", GcPolicyType.TIMELINE, {"keep_monthly": 3, "keep_yearly": 1})
        self.assertEqual(gcp.json(), xjson)

    def test_GcPolicy_ALL_from_json(self):
        json = '{"policy_name": "test", "policy_type": "ALL"}'
        gcp = GcPolicy.from_json(json)
        self.assertEqual(gcp, GcPolicy("test", GcPolicyType.ALL, {}))

    def test_GcPolicy_COUNT_from_json(self):
        json = '{"policy_name": "test", "policy_type": "COUNT", "keep_count": 4}'
        gcp = GcPolicy.from_json(json)
        self.assertEqual(gcp, GcPolicy("test", GcPolicyType.COUNT, {"keep_count": 4}))

    def test_GcPolicy_AGE_from_json(self):
        json = (
            '{"policy_name": "test", "policy_type": "AGE", '
            '"keep_years": 0, "keep_months": 0, "keep_weeks": 4, "keep_days": 0}'
        )
        gcp = GcPolicy.from_json(json)
        self.assertEqual(gcp, GcPolicy("test", GcPolicyType.AGE, {"keep_weeks": 4}))

    def test_GcPolicy_TIMELINE_from_json(self):
        json = (
            '{"policy_name": "test", "policy_type": "TIMELINE", '
            '"keep_yearly": 1, "keep_quarterly": 0, "keep_monthly": 3, '
            '"keep_weekly": 0, "keep_daily": 0, "keep_hourly": 0}'
        )
        gcp = GcPolicy.from_json(json)
        self.assertEqual(gcp, GcPolicy("test", GcPolicyType.TIMELINE, {"keep_monthly": 3, "keep_yearly": 1}))

    def test_GcPolicyParams_evaluate_raises(self):
        gcp = GcPolicyParams()
        with self.assertRaises(NotImplementedError) as cm:
            gcp.evaluate([])

    def test_GcPolicyParamsAll_evaluate(self):
        gcp = GcPolicy("test", GcPolicyType.ALL, {})
        to_delete = gcp.evaluate(get_six_weeks())
        self.assertEqual(to_delete, [])

    def test_GcPolicyParamsCount_evaluate(self):
        gcp = GcPolicy("test", GcPolicyType.COUNT, {"keep_count": 4})
        six_weeks = get_six_weeks()
        to_delete = gcp.evaluate(six_weeks)

        self.assertEqual(len(to_delete), 2)

        for i in range(0, 1):
            self.assertEqual(to_delete[i].timestamp, six_weeks[i].timestamp)

    def test_GcPolicyParamsAge_evaluate(self):
        gcp = GcPolicy("test", GcPolicyType.AGE, {"keep_weeks": 2})
        six_weeks = get_six_weeks()
        to_delete = gcp.evaluate(six_weeks)

        self.assertEqual(len(to_delete), 4)

        for i in range(0, 4):
            self.assertEqual(to_delete[i].timestamp, six_weeks[i].timestamp)

    def test_GcPolicyParamsTimeline_evaluate(self):
        gcp = GcPolicy("test", GcPolicyType.TIMELINE, {"keep_weekly": 4, "keep_monthly": 3})
        eighteen_months = get_eighteen_months()
        count = len(eighteen_months)

        to_delete = gcp.evaluate(eighteen_months)

        self.assertEqual(count - len(to_delete), 7)

    def test_GcPolicyParamsTimeline_evaluate_2(self):
        params = {"keep_yearly": 1, "keep_monthly": 6, "keep_weekly": 4, "keep_daily": 7}
        gcp = GcPolicy("test", GcPolicyType.TIMELINE, params)
        eighteen_months = get_eighteen_months()
        count = len(eighteen_months)

        to_delete = gcp.evaluate(eighteen_months)

        # Result is one less than the sum of retention counts since weekly/daily
        # overlap for Monday's first snapshot.
        self.assertEqual(count - len(to_delete), 17)

    def test_GcPolicyParamsTimeline_first_snapshot_bug(self):
        """
        Regression test for bug where first snapshot is deleted even with
        weekly/daily retention configured.

        Bug: First snapshot on arbitrary date classified as "yearly" and
        deleted when keep_yearly=0, even though keep_weekly and keep_daily
        are configured.

        Fix: Snapshot now classified into multiple categories (yearly, monthly,
        daily, hourly) and kept because daily wants to keep it.
        """
        params = {
            "keep_yearly": 0,
            "keep_quarterly": 0,
            "keep_monthly": 0,
            "keep_weekly": 4,
            "keep_daily": 3,
            "keep_hourly": 0
        }

        gcp = GcPolicy("test", GcPolicyType.TIMELINE, params)

        # Single snapshot on Nov 12, 2025 (Wednesday)
        snapshot = MockSnapshotSet("test.0", datetime(2025, 11, 12, 0, 0, 38).timestamp())

        to_delete = gcp.evaluate([snapshot])

        # Should NOT be deleted - it's the first weekly/daily snapshot
        self.assertEqual(len(to_delete), 0,
                        "First snapshot should not be deleted when weekly/daily retention configured")

    def test_GcPolicyParamsTimeline_jan_1_monday(self):
        """
        Test snapshot on Jan 1 (Monday) at midnight with selective retention.

        This snapshot qualifies for ALL categories but should be kept if
        any category has retention configured.
        """
        params = {
            "keep_yearly": 0,
            "keep_quarterly": 0,
            "keep_monthly": 0,
            "keep_weekly": 2,
            "keep_daily": 0,
            "keep_hourly": 0
        }

        gcp = GcPolicy("test", GcPolicyType.TIMELINE, params)

        # Jan 1, 2024 is a Monday at midnight
        snapshot_set = MockSnapshotSet("test.0", datetime(2024, 1, 1, 0, 0, 0).timestamp())

        to_delete = gcp.evaluate([snapshot_set])

        # Should NOT be deleted - it's also a weekly snapshot set
        self.assertEqual(len(to_delete), 0,
                        "Snapshot on Jan 1 should not be deleted when weekly retention > 0")


    def test_GcPolicyParamsTimeline_multiple_weekly_snapshots(self):
        """
        Test proper retention with multiple weekly snapshots.

        Ensures that the fix doesn't break normal retention behavior.
        """
        params = {
            "keep_yearly": 0,
            "keep_quarterly": 0,
            "keep_monthly": 0,
            "keep_weekly": 2,
            "keep_daily": 0,
            "keep_hourly": 0
        }

        gcp = GcPolicy("test", GcPolicyType.TIMELINE, params)

        # Three Monday snapshots, one week apart
        # Using recent dates to ensure they're after Jan 1
        base = datetime(2025, 11, 3, 0, 0, 0)  # Monday, Nov 3, 2025
        snapshot_sets = [
            MockSnapshotSet(f"test.{index}", (base + timedelta(weeks=week)).timestamp())
            for index, week in enumerate(range(3))
        ]

        to_delete = gcp.evaluate(snapshot_sets)

        # Should delete oldest snapshot_set only (keep 2 most recent)
        self.assertEqual(len(to_delete), 1,
                        "Should delete 1 snapshot_set when keep_weekly=2 and "
                        "we have 3 weekly snapshot sets")
        self.assertEqual(to_delete[0], snapshot_sets[0],
                        "Should delete the oldest snapshot set")

    def test_GcPolicy__str__(self):
        gcp = GcPolicy("test", GcPolicyType.ALL, {})
        xstr = "Name: test\nType: All\nParams: "
        self.assertEqual(str(gcp), xstr)

        gcp = GcPolicy("test", GcPolicyType.COUNT, {"keep_count": 4})
        xstr = "Name: test\nType: Count\nParams: keep_count=4"
        self.assertEqual(str(gcp), xstr)

        gcp = GcPolicy("test", GcPolicyType.AGE, {"keep_weeks": 4, "keep_days": 7})
        xstr = "Name: test\nType: Age\nParams: keep_weeks=4, keep_days=7"
        self.assertEqual(str(gcp), xstr)

        gcp = GcPolicy("test", GcPolicyType.TIMELINE, {"keep_weekly": 4, "keep_daily": 7})
        xstr = "Name: test\nType: Timeline\nParams: keep_weekly=4, keep_daily=7"
        self.assertEqual(str(gcp), xstr)

    def test_GcPolicy__repr__(self):
        gcp = GcPolicy("test", GcPolicyType.ALL, {})
        xrepr = "GcPolicy('test', GcPolicyType.ALL, {})"
        self.assertEqual(repr(gcp), xrepr)

        gcp = GcPolicy("test", GcPolicyType.COUNT, {"keep_count": 4})
        xrepr = "GcPolicy('test', GcPolicyType.COUNT, {'keep_count': 4})"
        self.assertEqual(repr(gcp), xrepr)

        gcp = GcPolicy("test", GcPolicyType.AGE, {"keep_weeks": 4, "keep_days": 7})
        xrepr = (
            "GcPolicy('test', GcPolicyType.AGE, {'keep_years': 0, 'keep_months': 0, "
            "'keep_weeks': 4, 'keep_days': 7})"
        )
        self.assertEqual(repr(gcp), xrepr)

        gcp = GcPolicy("test", GcPolicyType.TIMELINE, {"keep_weekly": 4, "keep_daily": 7})
        xrepr = (
            "GcPolicy('test', GcPolicyType.TIMELINE, {'keep_yearly': 0, 'keep_quarterly': 0, "
            "'keep_monthly': 0, 'keep_weekly': 4, 'keep_daily': 7, 'keep_hourly': 0})"
        )
        self.assertEqual(repr(gcp), xrepr)

    def test_GcPolicy_enable_start_stop_disable(self):
        gcp = GcPolicy("test", GcPolicyType.AGE, {"keep_weeks": 2})

        self.assertFalse(gcp.enabled)
        self.assertFalse(gcp.running)

        gcp.enable()
        self.assertTrue(gcp.enabled)
        self.assertFalse(gcp.running)

        gcp.start()
        self.assertTrue(gcp.enabled)
        self.assertTrue(gcp.running)

        gcp.stop()
        self.assertTrue(gcp.enabled)
        self.assertFalse(gcp.running)

        gcp.disable()
        self.assertFalse(gcp.enabled)
        self.assertFalse(gcp.running)

    def test_GcPolicy_from_cmd_args_ALL(self):
        args = MockArgs()
        args.policy_type = GcPolicyType.ALL.value
        args.schedule_name = "test"
        gcp = GcPolicy.from_cmd_args(args)
        self.assertEqual(gcp.type, GcPolicyType.ALL)
        self.assertEqual(gcp, GcPolicy("test", GcPolicyType.ALL, {}))

    def test_GcPolicy_from_cmd_args_COUNT(self):
        args = MockArgs()
        args.policy_type = GcPolicyType.COUNT.value
        args.schedule_name = "test"
        args.keep_count = 4
        gcp = GcPolicy.from_cmd_args(args)
        self.assertEqual(gcp.type, GcPolicyType.COUNT)
        self.assertEqual(gcp, GcPolicy("test", GcPolicyType.COUNT, {"keep_count": 4}))

    def test_GcPolicy_from_cmd_args_AGE(self):
        args = MockArgs()
        args.policy_type = GcPolicyType.AGE.value
        args.schedule_name = "test"
        args.keep_days = 7
        args.keep_weeks = 4
        gcp = GcPolicy.from_cmd_args(args)
        xparams = {
            'keep_years': 0,
            'keep_months': 0,
            'keep_weeks': 4,
            'keep_days': 7
        }
        self.assertEqual(gcp.type, GcPolicyType.AGE)
        self.assertEqual(gcp, GcPolicy("test", GcPolicyType.AGE, xparams))

    def test_GcPolicy_from_cmd_args_TIMELINE(self):
        args = MockArgs()
        args.policy_type = GcPolicyType.TIMELINE.value
        args.schedule_name = "test"
        args.keep_daily = 7
        args.keep_weekly = 4
        gcp = GcPolicy.from_cmd_args(args)
        xparams = {
            'keep_yearly': 0,
            'keep_quarterly': 0,
            'keep_monthly': 0,
            'keep_weekly': 4,
            'keep_daily': 7,
            'keep_hourly': 0
        }
        self.assertEqual(gcp.type, GcPolicyType.TIMELINE)
        self.assertEqual(gcp, GcPolicy("test", GcPolicyType.TIMELINE, xparams))

    def test_GcPolicy_from_cmd_args_bad_type(self):
        args = MockArgs()
        args.policy_type = "Quux"
        args.schedule_name = "test"
        with self.assertRaises(snapm.SnapmArgumentError) as cm:
            gcp = GcPolicy.from_cmd_args(args)


class ScheduleTests(unittest.TestCase):
    def test_Schedule(self):
        gcp = GcPolicy("test", GcPolicyType.ALL, {})
        sched = Schedule("test", ["/", "/var"], "10%SIZE", True, "hourly", gcp)
        self.assertEqual(sched.name, "test")
        self.assertEqual(sched.sources, ["/", "/var"])
        self.assertEqual(sched.default_size_policy, "10%SIZE")
        self.assertEqual(sched.autoindex, True)
        self.assertEqual(sched.calendarspec, "hourly")
        self.assertEqual(sched.gc_policy, gcp)

    def test_Schedule_from_dict(self):
        sched_dict = {
            "name": "test",
            "sources": ["/", "/var"],
            "default_size_policy": "10%SIZE",
            "autoindex": True,
            "calendarspec": "hourly",
            "gc_policy": {'policy_name': 'test', 'policy_type': 'COUNT', 'keep_count': 4},
            "boot": False,
            "revert": False,
        }
        sched = Schedule.from_dict(sched_dict)
        self.assertEqual(sched.name, "test")
        self.assertEqual(sched.sources, ["/", "/var"])
        self.assertEqual(sched.default_size_policy, "10%SIZE")
        self.assertEqual(sched.autoindex, True)
        self.assertEqual(sched.calendarspec, "hourly")
        self.assertEqual(sched.boot, False)
        self.assertEqual(sched.revert, False)
        self.assertEqual(sched.gc_policy, GcPolicy("test", GcPolicyType.COUNT, {"keep_count": 4}))

    def test_Schedule_from_dict_bad_keys(self):
        sched_dict = {
            "badname": "test",
            "badsources": ["/", "/var"],
            "baddefault_size_policy": "10%SIZE",
            "badautoindex": True,
            "badcalendarspec": "hourly",
            "badgc_policy": {'policy_name': 'test', 'policy_type': 'COUNT', 'keep_count': 4},
        }
        with self.assertRaises(KeyError) as cm:
            sched = Schedule.from_dict(sched_dict)

    def test_Schedule_from_file(self):
        sched = Schedule.from_file("/src/container_tests/test.json")
        self.assertEqual(sched.name, "test")
        self.assertEqual(sched.sources, ["/", "/var"])
        self.assertEqual(sched.default_size_policy, "10%SIZE")
        self.assertEqual(sched.autoindex, True)
        self.assertEqual(sched.calendarspec, "hourly")
        self.assertEqual(sched.boot, True)
        self.assertEqual(sched.revert, True)
        self.assertEqual(sched.gc_policy, GcPolicy("test", GcPolicyType.ALL, {}))

    def test_Schedule_from_file_bad_file(self):
        with self.assertRaises(snapm.SnapmSystemError) as cm:
            Schedule.from_file("/src/container_tests/nosuch.json")

    def test_Schedule_from_file_bad_json(self):
        with self.assertRaises(JSONDecodeError) as cm:
            Schedule.from_file("/src/container_tests/badjson.json")

    def test_Schedule_from_file_bad_keys(self):
        with self.assertRaises(KeyError) as cm:
            Schedule.from_file("/src/container_tests/badkeys.json")

    def test_Schedule_write_config(self):
        xconf = """{
    "name": "test",
    "sources": [
        "/",
        "/var"
    ],
    "default_size_policy": "10%SIZE",
    "autoindex": true,
    "calendarspec": "hourly",
    "boot": false,
    "revert": false,
    "gc_policy": {
        "policy_name": "test",
        "policy_type": "ALL"
    }
}"""
        conf_dir = tempfile.mkdtemp("_sched_conf", dir=_VAR_TMP)
        conf_file = os.path.join(conf_dir, "test.json")

        def cleanup():
            os.unlink(conf_file)
            os.rmdir(conf_dir)
        self.addCleanup(cleanup)

        gcp = GcPolicy("test", GcPolicyType.ALL, {})
        sched = Schedule("test", ["/", "/var"], "10%SIZE", True, "hourly", gcp)
        sched.write_config(conf_dir)
        self.assertTrue(os.path.exists(conf_file))
        conf = None
        with open(conf_file, "r", encoding="utf8") as fp:
            conf = fp.read()
        self.assertEqual(conf, xconf)

    def test_Schedule_delete(self):
        conf_dir = tempfile.mkdtemp("_sched_conf", dir=_VAR_TMP)
        conf_file = os.path.join(conf_dir, "test.json")

        def cleanup():
            os.rmdir(conf_dir)
        self.addCleanup(cleanup)

        gcp = GcPolicy("test", GcPolicyType.ALL, {})
        sched = Schedule("test", ["/", "/var"], "10%SIZE", True, "hourly", gcp)
        sched.write_config(conf_dir)
        self.assertTrue(os.path.exists(conf_file))
        sched.delete_config()
        self.assertFalse(os.path.exists(conf_file))

    def test_Schedule_delete_unwritten(self):
        gcp = GcPolicy("test", GcPolicyType.ALL, {})
        sched = Schedule("test", ["/", "/var"], "10%SIZE", True, "hourly", gcp)
        sched.delete_config()

    def test_Schedule_gc_ALL(self):
        gcp = GcPolicy("test", GcPolicyType.ALL, {})
        sched = Schedule("test", ["/", "/var"], "10%SIZE", True, "weekly", gcp)
        six_weeks = get_six_weeks()

        sched.gc(six_weeks)

        self.assertFalse(any(mss.deleted for mss in six_weeks))

    def test_Schedule_gc_COUNT(self):
        gcp = GcPolicy("test", GcPolicyType.COUNT, {"keep_count": 4})
        sched = Schedule("test", ["/", "/var"], "10%SIZE", True, "weekly", gcp)
        six_weeks = get_six_weeks()

        sched.gc(six_weeks)

        self.assertTrue(all(mss.deleted for mss in six_weeks[0:1]))
        self.assertFalse(any(mss.deleted for mss in six_weeks[2:]))

    def test_Schedule_gc_AGE(self):
        gcp = GcPolicy("test", GcPolicyType.AGE, {"keep_weeks": 2})
        sched = Schedule("test", ["/", "/var"], "10%SIZE", True, "weekly", gcp)
        six_weeks = get_six_weeks()

        sched.gc(six_weeks)

        self.assertTrue(all(mss.deleted for mss in six_weeks[0:3]))
        self.assertFalse(any(mss.deleted for mss in six_weeks[4:]))

    def test_Schedule_enable_start_stop_disable(self):
        gcp = GcPolicy("test", GcPolicyType.AGE, {"keep_weeks": 2})
        sched = Schedule("test", ["/", "/var"], "10%SIZE", True, "weekly", gcp)
        self.assertFalse(sched.enabled)
        self.assertFalse(sched.running)

        sched.enable()
        self.assertTrue(sched.enabled)
        self.assertFalse(sched.running)

        sched.start()
        self.assertTrue(sched.enabled)
        self.assertTrue(sched.running)

        sched.stop()
        self.assertTrue(sched.enabled)
        self.assertFalse(sched.running)

        sched.disable()
        self.assertFalse(sched.enabled)
        self.assertFalse(sched.running)

    def test_Schedule__str__(self):
        xstr= (
            "Name: test\nSources: /, /var\nDefaultSizePolicy: 10%SIZE\n"
            "Calendarspec: weekly\n"
            "Boot: no\nRevert: no\nGcPolicy: \n"
            "    Name: test\n    Type: Age\n    Params: keep_weeks=2\n"
            "Enabled: no\nRunning: no"
        )
        gcp = GcPolicy("test", GcPolicyType.AGE, {"keep_weeks": 2})
        sched = Schedule("test", ["/", "/var"], "10%SIZE", True, "weekly", gcp)
        print("xxx" + str(sched) + "xxx")
        print("yyy" + xstr + "yyy")
        self.assertTrue(str(sched).startswith(xstr))

    def test_Schedule__repr__(self):
        xrepr = (
            "Schedule('test', ['/', '/var'], '10%SIZE', True, 'hourly', "
            "GcPolicy('test', GcPolicyType.AGE, "
            "{'keep_years': 0, 'keep_months': 0, 'keep_weeks': 2, 'keep_days': 0}))"
        )
        gcp = GcPolicy("test", GcPolicyType.AGE, {"keep_weeks": 2})
        sched = Schedule("test", ["/", "/var"], "10%SIZE", True, "hourly", gcp)
        self.assertEqual(repr(sched), xrepr)

    def test_Schedule__eq__(self):
        sched1 = Schedule(
            "test", ["/", "/var"], "10%SIZE", True, "hourly",
            GcPolicy("test", GcPolicyType.ALL, {}),
            boot=True, revert=True,
        )
        sched2 = Schedule.from_file("/src/container_tests/test.json")
        self.assertEqual(sched1, sched2)

        sched3 = Schedule(
            "othertest", ["/", "/var"], "50%SIZE", True, "weekly",
            GcPolicy("othertest", GcPolicyType.AGE, {"keep_days": 7})
        )

        self.assertNotEqual(sched1, sched3)
        self.assertNotEqual(sched2, sched3)
