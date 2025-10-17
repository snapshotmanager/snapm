# Copyright Red Hat
#
# tests/test_snapm.py - snapm package unit tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
import logging
from uuid import UUID

import snapm
import snapm._snapm

from tests import MockArgs

log = logging.getLogger()

ONE_GIB = 2**30
TWO_GIB = 2 * 2**30
FOUR_GIB = 4 * 2**30
FIVE_GIB = 5 * 2**30
EIGHT_GIB = 8 * 2**30
TEN_GIB = 10 * 2**30

ONE_TIB = 2**40
TWO_TIB = 2 * 2**40
FOUR_TIB = 4 * 2**40
FIVE_TIB = 5 * 2**40
EIGHT_TIB = 8 * 2**40
TEN_TIB = 10 * 2**40


class SnapmTests(unittest.TestCase):
    """Test snapm module"""

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)

    def tearDown(self):
        log.debug("Tearing down (%s)", self._testMethodName)

    def test_set_debug_mask(self):
        snapm.set_debug_mask(snapm.SNAPM_DEBUG_ALL)

    def test_set_debug_mask_bad_mask(self):
        with self.assertRaises(ValueError) as cm:
            snapm.set_debug_mask(snapm.SNAPM_DEBUG_ALL + 1)

    def test_SubsystemFilter(self):
        # Start with no subsystems enabled
        snapm.set_debug_mask(0)
        sf = snapm.SubsystemFilter("snapm")
        self.assertEqual(sf.enabled_subsystems, set())
        # Enable a couple and ensure new filters initialise from cache
        snapm.set_debug_mask(snapm.SNAPM_DEBUG_COMMAND | snapm.SNAPM_DEBUG_MANAGER)
        sf2 = snapm.SubsystemFilter("snapm")
        self.assertIn(snapm.SNAPM_SUBSYSTEM_COMMAND, sf2.enabled_subsystems)
        self.assertIn(snapm.SNAPM_SUBSYSTEM_MANAGER, sf2.enabled_subsystems)

    def test_Selection_is_null(self):
        s = snapm.Selection()
        self.assertTrue(s.is_null())

    def test_Selection_is_single_name(self):
        s = snapm.Selection(name="name")
        self.assertTrue(s.is_single())

    def test_Selection_is_single_uuid(self):
        s = snapm.Selection(uuid=UUID("2a6fd226-b400-577f-afe7-0c1a39c78488"))
        self.assertTrue(s.is_single())

    def test_Selection_is_not_single(self):
        s = snapm.Selection(origin="/dev/fedora/root")
        self.assertFalse(s.is_single())

    def test_Selection_is_not_null(self):
        s = snapm.Selection(name="name")
        self.assertFalse(s.is_null())

    def test_Selection_from_cmd_args_identifier_name(self):
        cmd_args = MockArgs()
        cmd_args.identifier = "name"
        s = snapm.Selection.from_cmd_args(cmd_args)
        self.assertEqual(cmd_args.identifier, s.name)

    def test_Selection_from_cmd_args_identifier_uuid(self):
        cmd_args = MockArgs()
        cmd_args.identifier = "2a6fd226-b400-577f-afe7-0c1a39c78488"
        s = snapm.Selection.from_cmd_args(cmd_args)
        self.assertEqual(UUID(cmd_args.identifier), s.uuid)

    def test_Selection_from_cmd_args_name(self):
        cmd_args = MockArgs()
        cmd_args.name = "name"
        s = snapm.Selection.from_cmd_args(cmd_args)
        self.assertEqual(cmd_args.name, s.name)

    def test_Selection_from_cmd_args_uuid(self):
        cmd_args = MockArgs()
        cmd_args.uuid = UUID("2a6fd226-b400-577f-afe7-0c1a39c78488")
        s = snapm.Selection.from_cmd_args(cmd_args)
        self.assertEqual(cmd_args.uuid, s.uuid)

    def test_Selection_from_cmd_args_schedule_name(self):
        cmd_args = MockArgs()
        cmd_args.schedule_name = "hourly"
        s = snapm.Selection.from_cmd_args(cmd_args)
        self.assertEqual(cmd_args.schedule_name, s.sched_name)

    def test_valid_selection_snapset(self):
        s = snapm.Selection(name="testset0", timestamp=1693921253)
        s.check_valid_selection(snapshot_set=True)

    def test_valid_selection_snapshot(self):
        s = snapm.Selection(origin="/dev/fedora/home", mount_point="/home")
        s.check_valid_selection(snapshot=True)

    def test_valid_selection_snapset_invalid(self):
        s = snapm.Selection(origin="/dev/fedora/home")
        with self.assertRaises(ValueError) as cm:
            s.check_valid_selection(snapshot_set=True)

    def test_valid_selection_snapshot_invalid(self):
        s = snapm.Selection(name="testset0")
        with self.assertRaises(ValueError) as cm:
            s.check_valid_selection(snapshot=True)

    def test_valid_size_policy_fixed(self):
        policy = snapm.SizePolicy("/", "/", TEN_GIB, FOUR_GIB, TEN_GIB, "2G")
        self.assertEqual(policy.size, TWO_GIB)
        policy = snapm.SizePolicy("/", "/", TEN_TIB, FOUR_TIB, TEN_TIB, "2T")
        self.assertEqual(policy.size, TWO_TIB)
        policy = snapm.SizePolicy("/", "/", TEN_GIB, FOUR_GIB, TEN_GIB, "2GiB")
        self.assertEqual(policy.size, TWO_GIB)
        policy = snapm.SizePolicy("/", "/", TEN_TIB, FOUR_TIB, TEN_TIB, "2TiB")
        self.assertEqual(policy.size, TWO_TIB)

    def test_valid_size_policy_free(self):
        policy = snapm.SizePolicy("/", "/", TEN_GIB, FOUR_GIB, TEN_GIB, "10%FREE")
        self.assertEqual(policy.size, ONE_GIB)
        policy = snapm.SizePolicy("/", "/", TEN_GIB, FOUR_GIB, TEN_GIB, "50%FREE")
        self.assertEqual(policy.size, FIVE_GIB)
        policy = snapm.SizePolicy("/", "/", TEN_TIB, FOUR_TIB, TEN_TIB, "80%FREE")
        self.assertEqual(policy.size, EIGHT_TIB)
        policy = snapm.SizePolicy("/", "/", TEN_TIB, FOUR_TIB, TEN_TIB, "100%FREE")
        self.assertEqual(policy.size, TEN_TIB)

    def test_valid_size_policy_used(self):
        policy = snapm.SizePolicy("/", "/", TEN_GIB, FOUR_GIB, TEN_GIB, "25%USED")
        self.assertEqual(policy.size, ONE_GIB)
        policy = snapm.SizePolicy("/", "/", TEN_GIB, FOUR_GIB, TEN_GIB, "50%USED")
        self.assertEqual(policy.size, TWO_GIB)
        policy = snapm.SizePolicy("/", "/", TEN_TIB, FOUR_TIB, TEN_TIB, "100%USED")
        self.assertEqual(policy.size, FOUR_TIB)
        policy = snapm.SizePolicy("/", "/", TEN_TIB, FOUR_TIB, TEN_TIB, "200%USED")
        self.assertEqual(policy.size, EIGHT_TIB)

    def test_valid_size_policy_size(self):
        policy = snapm.SizePolicy("/", "/", TEN_GIB, FOUR_GIB, TEN_GIB, "20%SIZE")
        self.assertEqual(policy.size, TWO_GIB)
        policy = snapm.SizePolicy("/", "/", TEN_GIB, FOUR_GIB, TEN_GIB, "50%SIZE")
        self.assertEqual(policy.size, FIVE_GIB)
        policy = snapm.SizePolicy("/", "/", TEN_TIB, FOUR_TIB, TEN_TIB, "80%SIZE")
        self.assertEqual(policy.size, EIGHT_TIB)
        policy = snapm.SizePolicy("/", "/", TEN_TIB, FOUR_TIB, TEN_TIB, "100%SIZE")
        self.assertEqual(policy.size, TEN_TIB)

    def test_size_policy_size_over_limit_raises(self):
        with self.assertRaises(snapm.SnapmSizePolicyError) as cm:
            policy = snapm.SizePolicy("/", "/", TEN_TIB, FOUR_TIB, TEN_TIB, "200%SIZE")

    def test_size_policy_fixed_bad_unit_raises(self):
        with self.assertRaises(snapm.SnapmSizePolicyError) as cm:
            policy = snapm.SizePolicy("/", "/", TEN_GIB, FOUR_GIB, TEN_GIB, "2A")

    def test_size_policy_invalid_policy_raises(self):
        with self.assertRaises(snapm.SnapmSizePolicyError) as cm:
            policy = snapm.SizePolicy("/", "/", TEN_GIB, FOUR_GIB, TEN_GIB, "100%QUX")

    def test_is_size_policy_valid(self):
        self.assertEqual(True, snapm.is_size_policy("2G"))
        self.assertEqual(True, snapm.is_size_policy("100%FREE"))
        self.assertEqual(True, snapm.is_size_policy("50%USED"))
        self.assertEqual(True, snapm.is_size_policy("100%SIZE"))

    def test_is_size_policy_invalid(self):
        self.assertEqual(False, snapm.is_size_policy("2A"))
        self.assertEqual(False, snapm.is_size_policy("100%QUUX"))
        self.assertEqual(False, snapm.is_size_policy("foo"))
        self.assertEqual(False, snapm.is_size_policy("100%"))

    def test_size_fmt_zero(self):
        self.assertEqual(snapm.size_fmt(0), "0B")

    def test_size_fmt_yib(self):
        self.assertEqual(snapm.size_fmt(1000000000000000000000000000), "827.2YiB")

    def test__unescape_mounts(self):
        unesc_strings = {
            "/foo/bar\\040baz": "/foo/bar baz",
            "/path/with/tab\\011chars": "/path/with/tab\tchars",
            "embedded\\012newline": "embedded\nnewline",
            "back\\134slash": "back\\slash",
        }
        for to_unesc in unesc_strings:
            self.assertEqual(snapm._snapm._unescape_mounts(to_unesc), unesc_strings[to_unesc])

    def test__unescape_mounts_None(self):
        with self.assertRaises(AttributeError):
            snapm._snapm._unescape_mounts(None)

    def test__unescape_mounts_not_a_string(self):
        with self.assertRaises(AttributeError):
            snapm._snapm._unescape_mounts(1)
