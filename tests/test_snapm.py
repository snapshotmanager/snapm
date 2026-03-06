# Copyright Red Hat
#
# tests/test_snapm.py - snapm package unit tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import subprocess
import unittest
from unittest.mock import MagicMock, mock_open, patch
import logging
from uuid import UUID
import os

import snapm
import snapm._snapm

from tests import MockArgs, have_root, BOOT_ROOT_TEST
from ._util import LvmLoopBacked, StratisLoopBacked


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


class SnapmTestsSimple(unittest.TestCase):
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

    @patch("builtins.open", new_callable=mock_open, read_data="MemTotal:        16384000 kB\n")
    def test_get_total_memory_16GiB(self, mock_file):
        """Test reading total system memory."""
        # 16384000 kB * 1024 = 16777216000 bytes
        mem = snapm.get_total_memory()
        self.assertEqual(mem, 16777216000)

    @patch("builtins.open", new_callable=mock_open, read_data="MemTotal:   2048 kB\n")
    def test_get_total_memory_2MiB(self, mock_file):
        """Test parsing MemTotal from meminfo."""
        # 2048 kB = 2097152 bytes
        self.assertEqual(snapm.get_total_memory(), 2097152)

    def test_get_current_rss_real(self):
        """Test getting current RSS."""
        # This uses resource.getrusage, usually available.
        # We just want to ensure it returns an int > 0 or 0, not crash.
        rss = snapm.get_current_rss()
        self.assertIsInstance(rss, int)
        self.assertGreaterEqual(rss, 0)

    @patch("builtins.open", new_callable=mock_open, read_data="VmRSS:      1024 kB\n")
    def test_get_current_rss(self, mock_file):
        """Test parsing VmRSS from status file."""
        # 1024 kB = 1048576 bytes
        self.assertEqual(snapm.get_current_rss(), 1048576)

    @patch("builtins.open", side_effect=OSError)
    def test_get_current_rss_fail(self, mock_file):
        """Test VmRSS failure handling."""
        self.assertEqual(snapm.get_current_rss(), 0)

    @patch("builtins.open", side_effect=OSError)
    def test_get_total_memory_fail(self, mock_file):
        """Test MemTotal failure handling."""
        self.assertEqual(snapm.get_total_memory(), 0)

    def test_fstab_reader_malformed(self):
        """Test FsTabReader skipping malformed lines."""
        with patch("builtins.open", mock_open(read_data="valid / xfs defaults 0 0\nmalformed_line\n")):
            fstab = snapm.FsTabReader("/etc/fstab")
            self.assertEqual(len(fstab.entries), 1)
            self.assertEqual(fstab.entries[0].where, "/")

    def test_fstab_reader_io_error(self):
        """Test FsTabReader handling IO errors."""
        with patch("builtins.open", side_effect=IOError("Read error")):
            with self.assertRaises(snapm.SnapmSystemError):
                snapm.FsTabReader()

    @patch("subprocess.run")
    def test_get_device_path_errors(self, mock_run):
        """Test get_device_path error handling."""
        # Case 1: blkid not found
        mock_run.side_effect = FileNotFoundError
        with self.assertRaises(snapm.SnapmNotFoundError):
            snapm.get_device_path("uuid", "123")

        # Case 2: Unexpected error
        mock_run.side_effect = Exception("Boom")
        with self.assertRaises(snapm.SnapmCalloutError):
            snapm.get_device_path("uuid", "123")

    @unittest.mock.patch("snapm._snapm.get_device_path")
    @unittest.mock.patch("os.path.exists")
    def test_resolve_device_spec(self, mock_path_exists, mock_get_dev):
        """
        Tests the resolve_device_spec helper.
        """
        # 1. Test UUID
        mock_get_dev.return_value = "/dev/sda1"
        self.assertEqual(snapm.resolve_device_spec("UUID=some-uuid"), "/dev/sda1")
        mock_get_dev.assert_called_with("uuid", "some-uuid")

        # 2. Test LABEL
        mock_get_dev.return_value = "/dev/sda2"
        self.assertEqual(snapm.resolve_device_spec("LABEL=some-label"), "/dev/sda2")
        mock_get_dev.assert_called_with("label", "some-label")

        # 3. Test PARTUUID
        mock_path_exists.return_value = True
        self.assertEqual(snapm.resolve_device_spec("PARTUUID=part-uuid"), "/dev/disk/by-partuuid/part-uuid")

        # 4. Test PARTUUID not found
        mock_path_exists.return_value = False
        with self.assertRaises(snapm.SnapmNotFoundError):
            snapm.resolve_device_spec("PARTUUID=part-uuid-bad", raise_on_error=True)

        # 5. Test plain device path
        self.assertEqual(snapm.resolve_device_spec("/dev/vda1"), "/dev/vda1")

        # 6. Test UUID not found
        mock_get_dev.return_value = None
        with self.assertRaises(snapm.SnapmNotFoundError):
            snapm.resolve_device_spec("UUID=some-uuid-bad", raise_on_error=True)

        # 7. Test LABEL not found
        mock_get_dev.return_value = None
        with self.assertRaises(snapm.SnapmNotFoundError):
            snapm.resolve_device_spec("LABEL=some-label-bad", raise_on_error=True)

        # 8. Test PARTLABEL
        mock_path_exists.return_value = True
        self.assertEqual(
            snapm.resolve_device_spec("PARTLABEL=part-label", raise_on_error=True),
            "/dev/disk/by-partlabel/part-label",
        )

        # 9. Test PARTLABEL not found
        mock_path_exists.return_value = False
        with self.assertRaises(snapm.SnapmNotFoundError):
            snapm.resolve_device_spec("PARTLABEL=part-label-bad", raise_on_error=True)

    def test_snapshot_mounted_checks(self):
        """Test Snapshot mounted checks via /proc/self/mounts."""
        # Mock a snapshot
        snap = MagicMock(spec=snapm.Snapshot)
        snap.mount_point = "/mnt/origin"
        snap.devpath = "/dev/vg/lv-snap"
        snap.origin = "/dev/vg/lv"

        # We have to bind the property to the class or instance to test the property logic
        # explicitly if testing the base class logic, but Snapshot is abstract.
        # Ideally, we create a concrete subclass or rely on integration tests.
        # Here is a unit test approach using a temporary concrete class:
        class ConcreteSnapshot(snapm.Snapshot):
            """Minimal fulfillment of the ABC contract"""
            @property
            def origin(self): return "/dev/vg/lv"
            @property
            def origin_options(self): return ""
            @property
            def devpath(self): return "/dev/vg/lv-snap"
            @property
            def status(self): return snapm.SnapStatus.ACTIVE
            @property
            def size(self): return 0
            @property
            def free(self): return 0
            @property
            def autoactivate(self): return False
            def invalidate_cache(self): return

        s = ConcreteSnapshot("s1", "set1", "/dev/vg/lv", 0, "/mnt/origin", MagicMock())

        #mounts_data = "/dev/vg/lv-snap /mnt/snap xfs rw 0 0\n/dev/vg/lv /mnt/origin xfs rw 0 0\n"
        mounts_data = "/dev/vg/lv /mnt/origin xfs rw 0 0\n/dev/vg/lv-snap /mnt/snap xfs rw 0 0\n"

        def mock_samefile(path1, path2):
            # Snapshot device and mount point match
            if path1 == "/dev/vg/lv-snap" and path2 == "/dev/vg/lv-snap":
                return True
            # Origin device and mount point match
            if path1 == "/dev/vg/lv" and path2 == "/dev/vg/lv":
                return True
            return False

        with patch("builtins.open", mock_open(read_data=mounts_data)), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.samefile", side_effect=mock_samefile):

            self.assertTrue(s.snapshot_mounted)
            # Test case: Mount point has origin mounted (implies snapshot is NOT mounted there)
            # This happens if we haven't mounted the snapshot yet or reverted.
            self.assertTrue(s.origin_mounted)


@unittest.skipIf(not have_root(), "requires root privileges")
class SnapmTests(unittest.TestCase):
    """Test snapm module with devices"""
    volumes = ["root"]
    thin_volumes = ["opt"]
    stratis_volumes = ["fs1"]

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)
        def cleanup_lvm():
            log.debug("Cleaning up LVM (%s)", self._testMethodName)
            if hasattr(self, "_lvm"):
                self._lvm.destroy()

        def cleanup_stratis():
            log.debug("Cleaning up Stratis (%s)", self._testMethodName)
            if hasattr(self, "_stratis"):
                self._stratis.destroy()

        self.addCleanup(cleanup_lvm)
        self.addCleanup(cleanup_stratis)

        self._lvm = LvmLoopBacked(self.volumes, thin_volumes=self.thin_volumes)
        self._stratis = StratisLoopBacked(self.stratis_volumes)

    def test_get_device_path_uuid(self):
        dev_path = self._lvm.block_devs()[0]
        blkid_cmd = ["blkid", "--match-tag=UUID", "--output=value", dev_path]
        result = subprocess.run(blkid_cmd, check=True, capture_output=True, encoding="utf8")
        dev_uuid = result.stdout.strip()
        self.assertTrue(os.path.samefile(dev_path, snapm.get_device_path("uuid", dev_uuid)))

    def test_get_device_path_no_such_uuid(self):
        dev_uuid = "f00f00f0-baaa-cafe-f000-b4f67cea90d5"
        self.assertEqual(None, snapm.get_device_path("uuid", dev_uuid))

    def test_get_device_path_label(self):
        dev_path = self._lvm.block_devs()[0]
        blkid_cmd = ["blkid", "--match-tag=LABEL", "--output=value", dev_path]
        result = subprocess.run(blkid_cmd, check=True, capture_output=True, encoding="utf8")
        dev_label = result.stdout.strip()
        self.assertTrue(os.path.samefile(dev_path, snapm.get_device_path("label", dev_label)))

    def test_get_device_path_no_such_label(self):
        dev_label = "ThisIsNotALabel"
        self.assertEqual(None, snapm.get_device_path("label", dev_label))

    def test_get_device_fstype_ext4(self):
        dev_path = self._lvm.block_devs()[0]
        fstype = snapm.get_device_fstype(dev_path)
        self.assertEqual("ext4", fstype)

    def test_get_device_fstype_empty(self):
        with self.assertRaises(ValueError):
            snapm.get_device_fstype("")

    def test_get_device_fstype_no_such_path(self):
        with self.assertRaises(snapm.SnapmNotFoundError):
            snapm.get_device_fstype("/whats/the/frequency/kenneth/?")

    def test_get_device_fstype_not_a_blockdev(self):
        with self.assertRaises(snapm.SnapmPathError):
            snapm.get_device_fstype("/dev/null")
