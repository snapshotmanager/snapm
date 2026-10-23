# Copyright Red Hat
#
# tests/test_snapm.py - snapm package unit tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
from contextlib import contextmanager
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, mock_open, patch
import logging
import stat
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

    def test_unescape_mounts(self):
        unesc_strings = {
            "/foo/bar\\040baz": "/foo/bar baz",
            "/path/with/tab\\011chars": "/path/with/tab\tchars",
            "embedded\\012newline": "embedded\nnewline",
            "hash\\043char": "hash#char",
            "back\\134slash": "back\\slash",
        }
        for to_unesc in unesc_strings:
            self.assertEqual(snapm.unescape_mounts(to_unesc), unesc_strings[to_unesc])

    def test_unescape_mounts_non_latin1(self):
        # The kernel only escapes " \t\n\\#": every other byte, including
        # multi-byte UTF-8 sequences, must pass through untouched.
        unesc_strings = {
            "/mnt/日本/data": "/mnt/日本/data",
            "/mnt/данные": "/mnt/данные",
            "/mnt/café": "/mnt/café",
            "/mnt/\U0001F4BE": "/mnt/\U0001F4BE",
            "/mnt/日本\\040data": "/mnt/日本 data",
        }
        for to_unesc in unesc_strings:
            self.assertEqual(snapm.unescape_mounts(to_unesc), unesc_strings[to_unesc])

    def test_unescape_mounts_literal_escape_not_double_decoded(self):
        # A path containing a literal "\040" is written by the kernel as
        # "\134040": the backslash substitution runs last, so the result must
        # be the literal text and not a space.
        self.assertEqual(snapm.unescape_mounts("/mnt/a\\134040b"), "/mnt/a\\040b")

    def test_unescape_mounts_leaves_unescaped_sequences(self):
        # \057 ('/') is not in the kernel's escape set and must be left alone.
        self.assertEqual(snapm.unescape_mounts("/mnt/a\\057b"), "/mnt/a\\057b")

    def test_unescape_mounts_None(self):
        with self.assertRaises(AttributeError):
            snapm.unescape_mounts(None)

    def test_unescape_mounts_not_a_string(self):
        with self.assertRaises(AttributeError):
            snapm.unescape_mounts(1)

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

    @staticmethod
    def _concrete_snapshot(devpath, origin, mount_point):
        """Return a minimal concrete Snapshot for the mounted checks."""
        class ConcreteSnapshot(snapm.Snapshot):
            """Minimal fulfillment of the ABC contract"""
            @property
            def origin(self): return origin
            @property
            def origin_options(self): return ""
            @property
            def devpath(self): return devpath
            @property
            def status(self): return snapm.SnapStatus.ACTIVE
            @property
            def size(self): return 0
            @property
            def free(self): return 0
            @property
            def autoactivate(self): return False
            def invalidate_cache(self): return

        return ConcreteSnapshot("s1", "set1", origin, 0, mount_point, MagicMock())

    def test_snapshot_mounted_checks(self):
        """Test Snapshot mounted checks against the mount table.

        The snapshot and its origin are distinct devices, and the snapshot
        is mounted under a name that differs from the one the Snapshot
        reports for it: matching is by device number, not by name (#1030).
        """
        devices = _block_device_aliases(2)
        if len(devices) < 2:
            self.skipTest("Fewer than two aliased block devices available")
        (snap_dev, snap_alias), (origin_dev, origin_alias) = devices

        s = self._concrete_snapshot(snap_alias, origin_alias, "/mnt/origin")

        def mountinfo(lines):
            # Index the entries the way MountInfoReader does: a mount with
            # an anonymous superblock is found by the device number of the
            # device it was mounted from as well as by the one the kernel
            # reports for it.
            def lookup_devno(devno):
                return [
                    entry
                    for entry in lines.values()
                    if entry.devno == devno
                    or (
                        os.major(entry.devno) == 0
                        and os.stat(entry.what).st_rdev == devno
                    )
                ]

            return MagicMock(
                wraps=None,
                **{
                    "lookup_mount_point.side_effect": lambda path: lines.get(path),
                    "lookup_devno.side_effect": lookup_devno,
                    "device_mounted.side_effect": lambda dev: bool(
                        lookup_devno(os.stat(dev).st_rdev)
                    ),
                },
            )

        def entry(devpath, anonymous=False):
            devno = os.makedev(0, 42) if anonymous else os.stat(devpath).st_rdev
            return SimpleNamespace(devno=devno, what=devpath)

        # The origin is mounted at the snapshot's mount point and the
        # snapshot itself is mounted elsewhere.
        table = mountinfo(
            {"/mnt/origin": entry(origin_dev), "/mnt/snap": entry(snap_dev)}
        )
        self.assertTrue(s.is_snapshot_mounted(table))
        self.assertTrue(s.is_origin_mounted(table))

        # Neither device is mounted.
        table = mountinfo({})
        self.assertFalse(s.is_snapshot_mounted(table))
        self.assertFalse(s.is_origin_mounted(table))

        # The snapshot is mounted at its own mount point: the origin is not.
        table = mountinfo({"/mnt/origin": entry(snap_dev)})
        self.assertTrue(s.is_snapshot_mounted(table))
        self.assertFalse(s.is_origin_mounted(table))

        # The origin carries a file system with an anonymous superblock:
        # the device number the kernel reports for the mount is the
        # st_rdev of nothing, so the origin is found through the index
        # rather than by comparing with it.
        table = mountinfo({"/mnt/origin": entry(origin_dev, anonymous=True)})
        self.assertTrue(s.is_origin_mounted(table))
        self.assertFalse(s.is_snapshot_mounted(table))

    def test_snapshot_mounted_checks_without_a_mount_point(self):
        """Test that a member with no mount point is never mounted."""
        s = self._concrete_snapshot("/dev/vg/lv-snap", "/dev/vg/lv", "")
        table = MagicMock()
        self.assertFalse(s.is_snapshot_mounted(table))
        self.assertFalse(s.is_origin_mounted(table))
        table.lookup_mount_point.assert_not_called()
        table.device_mounted.assert_not_called()

    def test_snapshot_mounted_properties_read_the_mount_table(self):
        """Test that the properties read the mount table for themselves."""
        s = self._concrete_snapshot("/dev/no/such/snap", "/dev/no/such/lv", "/mnt/x")
        self.assertFalse(s.snapshot_mounted)
        self.assertFalse(s.origin_mounted)


def _block_device_aliases(count):
    """Return up to ``count`` (canonical path, alias path) block device pairs.

    The ``/dev/block/MAJ:MIN`` links give every block device a second name
    that differs textually from its canonical path: the same situation as an
    LVM volume appearing as ``/dev/VG/LV`` in one place and
    ``/dev/mapper/VG-LV`` in another. Each pair names a distinct device.
    """
    try:
        aliases = sorted(os.listdir("/dev/block"))
    except OSError:
        return []
    devices = []
    for alias in aliases:
        alias_path = os.path.join("/dev/block", alias)
        canonical = os.path.realpath(alias_path)
        if canonical == alias_path:
            continue
        try:
            if not stat.S_ISBLK(os.stat(canonical).st_mode):
                continue
        except OSError:
            continue
        devices.append((canonical, alias_path))
        if len(devices) == count:
            break
    return devices


class MountInfoReaderTests(unittest.TestCase):
    """Test the MountInfoReader class."""

    @staticmethod
    @contextmanager
    def _mountinfo(lines):
        """Yield a MountInfoReader over a synthetic mountinfo file."""
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".mountinfo"
        ) as f:
            for line in lines:
                f.write(line + "\n")
            path = f.name
        try:
            yield snapm.MountInfoReader(path=path)
        finally:
            os.unlink(path)

    def test_parses_a_mountinfo_line(self):
        """Test that each mountinfo field is parsed into the entry."""
        with self._mountinfo(
            ["36 35 98:0 /sub /mnt rw,noatime - ext3 /dev/root rw,errors=continue"]
        ) as mountinfo:
            (entry,) = mountinfo.entries

        self.assertEqual(entry.mount_id, 36)
        self.assertEqual(entry.parent_id, 35)
        self.assertEqual(entry.devno, os.makedev(98, 0))
        self.assertEqual(entry.root, "/sub")
        self.assertEqual(entry.where, "/mnt")
        self.assertEqual(entry.options, "rw,noatime")
        self.assertEqual(entry.fstype, "ext3")
        self.assertEqual(entry.what, "/dev/root")
        self.assertEqual(entry.super_options, "rw,errors=continue")

    def test_parses_optional_fields(self):
        """Test that the variable optional fields are stepped over.

        The fields following the '-' separator are located relative to it,
        since any number of optional fields may precede it.
        """
        with self._mountinfo(
            [
                "36 35 98:0 / /one rw shared:1 - ext4 /dev/one rw",
                "37 35 98:1 / /two rw shared:2 master:1 propagate_from:1 - ext4 /dev/two rw",
                "38 35 98:2 / /three rw - ext4 /dev/three rw",
            ]
        ) as mountinfo:
            self.assertEqual(
                [entry.where for entry in mountinfo],
                ["/one", "/two", "/three"],
            )
            self.assertEqual(
                [entry.what for entry in mountinfo],
                ["/dev/one", "/dev/two", "/dev/three"],
            )

    def test_unescapes_path_fields(self):
        """Test that the octal escapes in path fields are decoded."""
        with self._mountinfo(
            [
                r"36 35 98:0 /a\040b /mnt/a\040b rw - ext4 /dev/mapper/a\134x2db rw",
            ]
        ) as mountinfo:
            (entry,) = mountinfo.entries

        self.assertEqual(entry.root, "/a b")
        self.assertEqual(entry.where, "/mnt/a b")
        self.assertEqual(entry.what, "/dev/mapper/a\\x2db")

    def test_skips_malformed_lines(self):
        """Test that malformed lines are skipped rather than raising."""
        with self._mountinfo(
            [
                "",
                "nonsense",
                "36 35 98:0 / /short rw",
                "37 35 98:1 / /notrunc rw - ext4",
                "38 35 bad:dev / /baddev rw - ext4 /dev/bad rw",
                "39 35 98:2 / /good rw - ext4 /dev/good rw",
            ]
        ) as mountinfo:
            self.assertEqual([entry.where for entry in mountinfo], ["/good"])

    def test_read_error_raises_system_error(self):
        """Test that an unreadable mountinfo file raises SnapmSystemError."""
        with self.assertRaises(snapm.SnapmSystemError):
            snapm.MountInfoReader(path="/proc/self/no-such-mountinfo")

    def test_lookup_mount_point_returns_the_topmost_mount(self):
        """Test that the last entry wins when mounts are stacked."""
        with self._mountinfo(
            [
                "36 35 98:0 / /mnt rw - ext4 /dev/under rw",
                "37 36 98:1 / /mnt rw - ext4 /dev/over rw",
            ]
        ) as mountinfo:
            self.assertEqual(mountinfo.lookup_mount_point("/mnt").what, "/dev/over")
            self.assertIsNone(mountinfo.lookup_mount_point("/nowhere"))

    def test_lookup_devno_returns_every_mount_of_a_device(self):
        """Test that a device mounted twice yields both of its mounts."""
        with self._mountinfo(
            [
                "36 35 98:0 / /one rw - ext4 /dev/dup rw",
                "37 35 98:1 / /other rw - ext4 /dev/other rw",
                "38 35 98:0 / /two rw - ext4 /dev/dup rw",
            ]
        ) as mountinfo:
            found = mountinfo.lookup_devno(os.makedev(98, 0))
            self.assertEqual([entry.where for entry in found], ["/one", "/two"])
            self.assertEqual(mountinfo.lookup_devno(os.makedev(99, 0)), [])

    def test_lookup_device_matches_an_alternate_device_name(self):
        """Test that lookup_device() matches on device number, not name.

        The device name a file system was mounted by and the name snapm
        knows it as frequently differ, and a logical volume renamed after it
        was mounted keeps its old name in the mount table (#1030).
        """
        devices = _block_device_aliases(2)
        if len(devices) < 2:
            self.skipTest("Fewer than two aliased block devices available")
        (canonical, alias), (other, _) = devices
        st_rdev = os.stat(canonical).st_rdev

        with self._mountinfo(
            [
                f"36 35 {os.major(st_rdev)}:{os.minor(st_rdev)} / /mnt rw - ext4"
                f" {canonical} rw",
            ]
        ) as mountinfo:
            # The alias names the same device by a different path.
            found = mountinfo.lookup_device(alias)
            self.assertEqual([entry.where for entry in found], ["/mnt"])
            self.assertTrue(mountinfo.device_mounted(canonical))
            # A different device is not mounted.
            self.assertEqual(mountinfo.lookup_device(other), [])
            self.assertFalse(mountinfo.device_mounted(other))

    def test_lookup_device_matches_an_anonymous_superblock(self):
        """Test that a mount with an anonymous device number is found.

        A file system with an anonymous superblock (btrfs, overlay) is
        given a device number with a major of zero, which is the st_rdev
        of no block device at all. The mount is found by the device it was
        mounted by instead.
        """
        devices = _block_device_aliases(2)
        if len(devices) < 2:
            self.skipTest("Fewer than two aliased block devices available")
        (canonical, alias), (other, _) = devices

        with self._mountinfo(
            [f"36 35 0:69 / /mnt rw - btrfs {canonical} rw"]
        ) as mountinfo:
            found = mountinfo.lookup_device(canonical)
            self.assertEqual([entry.where for entry in found], ["/mnt"])
            # The alias names the same device by a different path.
            self.assertTrue(mountinfo.device_mounted(alias))
            # The anonymous device number still resolves on its own.
            self.assertEqual(
                [entry.where for entry in mountinfo.lookup_devno(os.makedev(0, 69))],
                ["/mnt"],
            )
            # A different device is not mounted.
            self.assertEqual(mountinfo.lookup_device(other), [])
            self.assertFalse(mountinfo.device_mounted(other))

    def test_anonymous_superblock_without_a_device_is_not_indexed(self):
        """Test that a mount source that is not a device path is skipped.

        An overlay or tmpfs mount has an anonymous device number and a
        source that names no device, so there is nothing to index it by.
        """
        with self._mountinfo(
            [
                "36 35 0:70 / /mnt rw - overlay overlay rw",
                "37 35 0:71 / /run rw - tmpfs tmpfs rw",
            ]
        ) as mountinfo:
            self.assertEqual(
                [entry.where for entry in mountinfo.lookup_devno(os.makedev(0, 70))],
                ["/mnt"],
            )
            self.assertEqual(mountinfo.lookup_device("overlay"), [])

    def test_lookup_device_ignores_non_block_devices(self):
        """Test that lookup_device() yields nothing for a non-block path."""
        with self._mountinfo(["36 35 98:0 / /mnt rw - ext4 /dev/one rw"]) as mountinfo:
            self.assertEqual(mountinfo.lookup_device("/etc/fstab"), [])
            self.assertEqual(mountinfo.lookup_device("/no/such/device"), [])

    def test_repr(self):
        """Test the MountInfoReader string representation."""
        with self._mountinfo([]) as mountinfo:
            self.assertEqual(repr(mountinfo), f"MountInfoReader(path='{mountinfo.path}')")


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
