# Copyright Red Hat
#
# tests/test_btrfs.py - Btrfs plugin unit tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
import logging
import os.path
import os
import shutil

from configparser import ConfigParser
from unittest.mock import Mock, mock_open, patch

log = logging.getLogger()

import snapm.manager.plugins.btrfs as btrfs
from snapm import (
    SnapmBusyError,
    SnapmNoSpaceError,
    SnapmInvalidIdentifierError,
    SnapmLimitError,
    SnapmPluginError,
    SnapmRecursionError,
    SnapStatus,
)

from tests import have_root
from ._util import BtrfsLoopBacked


def have_btrfs():
    """Return ``True`` if the btrfs commands needed to run the loop backed
    tests are installed, and ``False`` otherwise.
    """
    return all(shutil.which(cmd) for cmd in ("btrfs", "mkfs.btrfs"))


#: Output of 'btrfs subvolume list -u -q' for a Fedora style file system
#: containing 'root' and 'home' subvolumes, one snapshot of 'root' and one
#: subvolume nested beneath 'home'.
_SUBVOL_LIST = """\
ID 256 gen 9 top level 5 parent_uuid -                                    uuid c33923f5-31c4-bd4f-be85-59b758b7b2fe path root
ID 257 gen 9 top level 5 parent_uuid -                                    uuid 08509ddb-f7a7-cc4c-b805-0586e738b8fc path home
ID 258 gen 9 top level 5 parent_uuid c33923f5-31c4-bd4f-be85-59b758b7b2fe uuid 7722fff2-a17c-8840-95c9-9195abfa3411 path root-snapset_test_1721136677_-
ID 259 gen 10 top level 257 parent_uuid -                                    uuid 2d1850e3-7ec4-1c47-9761-158c12649d8b path home/nest vol
"""

#: Output of 'btrfs subvolume show' for a snapshot subvolume.
_SUBVOL_SHOW = """\
root-snapset_test_1721136677_-
\tName: \t\t\troot-snapset_test_1721136677_-
\tUUID: \t\t\t7722fff2-a17c-8840-95c9-9195abfa3411
\tParent UUID: \t\tc33923f5-31c4-bd4f-be85-59b758b7b2fe
\tReceived UUID: \t\t-
\tCreation time: \t\t2026-09-21 18:38:24 +0100
\tSubvolume ID: \t\t258
\tGeneration: \t\t9
\tGen at creation: \t9
\tParent ID: \t\t5
\tTop level ID: \t\t5
\tFlags: \t\t\t-
\tSnapshot(s):
\tQuota group:\t\tn/a
"""

#: Kernel mount table content for a Fedora style btrfs installation.
_PROC_MOUNTS = """\
sysfs /sys sysfs rw,seclabel,nosuid,nodev,noexec,relatime 0 0
/dev/vda3 / btrfs rw,seclabel,relatime,compress=zstd:1,ssd,space_cache=v2,subvolid=256,subvol=/root 0 0
/dev/vda2 /boot ext4 rw,seclabel,relatime 0 0
/dev/vda3 /home btrfs rw,seclabel,relatime,compress=zstd:1,ssd,space_cache=v2,subvolid=257,subvol=/home 0 0
/dev/vda3 /mnt/top btrfs rw,seclabel,relatime,space_cache=v2,subvolid=5,subvol=/ 0 0
/dev/loop7 /mnt/data\\040vol btrfs rw,relatime,subvolid=300,subvol=/data\\040vol 0 0
"""

#: Kernel mount table content with no btrfs file systems.
_PROC_MOUNTS_NO_BTRFS = """\
sysfs /sys sysfs rw,seclabel,nosuid,nodev,noexec,relatime 0 0
/dev/mapper/fedora-root / xfs rw,seclabel,relatime 0 0
"""


def _patch_proc_mounts(data):
    """
    Return a patcher replacing the kernel mount table with ``data``.
    """
    return patch("builtins.open", mock_open(read_data=data))


class BtrfsTestsSimple(unittest.TestCase):
    """Test Btrfs plugin functions"""

    def test__parse_subvol_list_line(self):
        lines = _SUBVOL_LIST.splitlines()
        subvol = btrfs._parse_subvol_list_line(lines[0])
        self.assertEqual(subvol[btrfs.SUBVOL_ID], 256)
        self.assertEqual(subvol[btrfs.SUBVOL_GEN], 9)
        self.assertEqual(subvol[btrfs.SUBVOL_TOP_LEVEL], 5)
        self.assertEqual(subvol[btrfs.SUBVOL_PARENT_UUID], btrfs.BTRFS_NULL_FIELD)
        self.assertEqual(
            subvol[btrfs.SUBVOL_UUID], "c33923f5-31c4-bd4f-be85-59b758b7b2fe"
        )
        self.assertEqual(subvol[btrfs.SUBVOL_PATH], "root")

    def test__parse_subvol_list_line_snapshot(self):
        subvol = btrfs._parse_subvol_list_line(_SUBVOL_LIST.splitlines()[2])
        self.assertEqual(subvol[btrfs.SUBVOL_ID], 258)
        self.assertEqual(
            subvol[btrfs.SUBVOL_PARENT_UUID], "c33923f5-31c4-bd4f-be85-59b758b7b2fe"
        )
        self.assertEqual(
            subvol[btrfs.SUBVOL_PATH], "root-snapset_test_1721136677_-"
        )

    def test__parse_subvol_list_line_nested_path_with_space(self):
        subvol = btrfs._parse_subvol_list_line(_SUBVOL_LIST.splitlines()[3])
        self.assertEqual(subvol[btrfs.SUBVOL_TOP_LEVEL], 257)
        self.assertEqual(subvol[btrfs.SUBVOL_PATH], "home/nest vol")

    def test__parse_subvol_list_line_otime(self):
        line = (
            "ID 258 gen 9 cgen 9 top level 5 otime 2026-09-21 18:38:24 "
            "parent_uuid - uuid 7722fff2-a17c-8840-95c9-9195abfa3411 path snap"
        )
        subvol = btrfs._parse_subvol_list_line(line)
        self.assertEqual(subvol[btrfs.SUBVOL_CGEN], 9)
        self.assertEqual(subvol[btrfs.SUBVOL_OTIME], "2026-09-21 18:38:24")
        self.assertEqual(subvol[btrfs.SUBVOL_PATH], "snap")

    def test__parse_subvol_list_line_malformed(self):
        lines = [
            "",
            "ID 256",
            "no fields here",
            "ID 256 gen 9 top level",
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(None, btrfs._parse_subvol_list_line(line))

    def test_list_subvolumes(self):
        with patch.object(btrfs, "_btrfs", return_value=_SUBVOL_LIST):
            subvolumes = btrfs.list_subvolumes("/mnt/top")
        self.assertEqual(
            sorted(subvolumes.keys()),
            sorted(
                [
                    "root",
                    "home",
                    "root-snapset_test_1721136677_-",
                    "home/nest vol",
                ]
            ),
        )
        self.assertEqual(subvolumes["home"][btrfs.SUBVOL_ID], 257)

    def test_list_subvolumes_empty(self):
        with patch.object(btrfs, "_btrfs", return_value=""):
            self.assertEqual({}, btrfs.list_subvolumes("/mnt/top"))

    def test_show_subvolume(self):
        with patch.object(btrfs, "_btrfs", return_value=_SUBVOL_SHOW):
            properties = btrfs.show_subvolume("/mnt/top/snap")
        self.assertEqual(properties[btrfs.SUBVOL_SHOW_ID], "258")
        self.assertEqual(
            properties[btrfs.SUBVOL_SHOW_UUID],
            "7722fff2-a17c-8840-95c9-9195abfa3411",
        )
        self.assertEqual(
            properties[btrfs.SUBVOL_SHOW_PARENT_UUID],
            "c33923f5-31c4-bd4f-be85-59b758b7b2fe",
        )
        self.assertEqual(properties[btrfs.SUBVOL_SHOW_FLAGS], "-")

    def test_subvolume_id(self):
        with patch.object(btrfs, "_btrfs", return_value=_SUBVOL_SHOW):
            self.assertEqual(258, btrfs.subvolume_id("/mnt/top/snap"))

    def test_subvolume_id_bad_report(self):
        with patch.object(btrfs, "_btrfs", return_value="No such subvolume\n"):
            with self.assertRaises(SnapmPluginError):
                btrfs.subvolume_id("/mnt/top/nosuch")

    def test_filter_btrfs_snapshot(self):
        with patch.object(btrfs, "_btrfs", return_value=_SUBVOL_LIST):
            subvolumes = btrfs.list_subvolumes("/mnt/top")
        self.assertEqual(False, btrfs.filter_btrfs_snapshot(subvolumes["root"]))
        self.assertEqual(
            True,
            btrfs.filter_btrfs_snapshot(subvolumes["root-snapset_test_1721136677_-"]),
        )
        self.assertEqual(False, btrfs.filter_btrfs_snapshot({}))

    def test__origin_from_parent_uuid(self):
        with patch.object(btrfs, "_btrfs", return_value=_SUBVOL_LIST):
            subvolumes = btrfs.list_subvolumes("/mnt/top")
        self.assertEqual(
            "root",
            btrfs._origin_from_parent_uuid(
                subvolumes, "c33923f5-31c4-bd4f-be85-59b758b7b2fe"
            ),
        )

    def test__origin_from_parent_uuid_no_origin(self):
        with patch.object(btrfs, "_btrfs", return_value=_SUBVOL_LIST):
            subvolumes = btrfs.list_subvolumes("/mnt/top")
        self.assertEqual(
            None,
            btrfs._origin_from_parent_uuid(
                subvolumes, "00000000-0000-0000-0000-000000000000"
            ),
        )

    def test__origin_from_parent_uuid_reverted_origin(self):
        # A snapshot of an origin that has since been reverted has the
        # preserved subvolume as its parent: the current origin path is
        # returned in this case.
        subvolumes = {
            "root.snapm-revert.1721136677": {btrfs.SUBVOL_UUID: "uuid-0"},
            "root": {btrfs.SUBVOL_UUID: "uuid-1"},
        }
        self.assertEqual(
            "root", btrfs._origin_from_parent_uuid(subvolumes, "uuid-0")
        )

    def test__strip_revert_suffix(self):
        paths = {
            "root": "root",
            "home": "home",
            "root.snapm-revert.1721136677": "root",
            "root-snapset_test_1721136677_-": "root-snapset_test_1721136677_-",
            "nested/root.snapm-revert.1721136677": "nested/root",
        }
        for path, expected in paths.items():
            with self.subTest(path=path):
                self.assertEqual(expected, btrfs._strip_revert_suffix(path))

    def test__find_in_progress_revert(self):
        subvolumes = {
            "root": {},
            "home": {},
            "root.snapm-revert.1721136677": {},
            "root-snapset_test_1721136677_-": {},
        }
        self.assertEqual(
            ["root.snapm-revert.1721136677"],
            btrfs._find_in_progress_revert(subvolumes, "root"),
        )
        self.assertEqual([], btrfs._find_in_progress_revert(subvolumes, "home"))

    def test_format_btrfs_name(self):
        self.assertEqual(
            "/dev/vda3:root", btrfs.format_btrfs_name("/dev/vda3", "root")
        )

    def test_device_subvol_from_origin(self):
        origins = {
            "/dev/vda3:root": ("/dev/vda3", "root"),
            "/dev/vda3:home": ("/dev/vda3", "home"),
            "/dev/loop0:root-snapset_test_1721136677_-": (
                "/dev/loop0",
                "root-snapset_test_1721136677_-",
            ),
            "/dev/mapper/fedora-btrfs:nested/vol": (
                "/dev/mapper/fedora-btrfs",
                "nested/vol",
            ),
        }
        for origin, expected in origins.items():
            with self.subTest(origin=origin):
                self.assertEqual(expected, btrfs.device_subvol_from_origin(origin))
                self.assertEqual(expected, btrfs.device_subvol_from_name(origin))

    def test_device_subvol_from_origin_malformed(self):
        origins = [
            "/dev/vda3",
            "/dev/vda3:",
            ":root",
            "",
        ]
        for origin in origins:
            with self.subTest(origin=origin):
                with self.assertRaises(SnapmInvalidIdentifierError):
                    btrfs.device_subvol_from_origin(origin)

    def test__check_subvol_name(self):
        btrfs._check_subvol_name("root")
        btrfs._check_subvol_name("a" * btrfs.BTRFS_MAX_NAME_LEN)
        btrfs._check_subvol_name("nested/" + "a" * btrfs.BTRFS_MAX_NAME_LEN)

    def test__check_subvol_name_too_long(self):
        with self.assertRaises(SnapmInvalidIdentifierError):
            btrfs._check_subvol_name("a" * (btrfs.BTRFS_MAX_NAME_LEN + 1))

    def test__check_subvol_name_too_long_multibyte(self):
        # Btrfs name limits apply to the encoded length of the name
        with self.assertRaises(SnapmInvalidIdentifierError):
            btrfs._check_subvol_name("é" * btrfs.BTRFS_MAX_NAME_LEN)

    def test__snapshot_min_size(self):
        sizes = [
            (0, 0),
            (256 * 2**20, 256 * 2**20),  # 256MiB/256MiB
            (1 * 2**30, 1 * 2**30),      # 1GiB/1GiB
            (1 * 2**40, 1 * 2**40),      # 1TiB/1TiB
        ]
        for (size, xsize) in sizes:
            self.assertEqual(xsize, btrfs._snapshot_min_size(size))

    def test__parse_mount_options(self):
        options = btrfs._parse_mount_options("rw,relatime,subvolid=256,subvol=/root")
        self.assertEqual(options["rw"], None)
        self.assertEqual(options["subvolid"], "256")
        self.assertEqual(options["subvol"], "/root")

    def test__unescape_mount(self):
        values = {
            "/mnt/data": "/mnt/data",
            "/mnt/data\\040vol": "/mnt/data vol",
            "/mnt/data\\011vol": "/mnt/data\tvol",
            "/mnt/data\\012vol": "/mnt/data\nvol",
            "/mnt/data\\134vol": "/mnt/data\\vol",
        }
        for value, expected in values.items():
            with self.subTest(value=value):
                self.assertEqual(expected, btrfs._unescape_mount(value))

    def test_btrfs_mounts(self):
        with _patch_proc_mounts(_PROC_MOUNTS):
            mounts = btrfs.btrfs_mounts()
        # The ext4 and sysfs entries are ignored
        self.assertEqual(4, len(mounts))
        self.assertEqual(
            btrfs.BtrfsMount("/dev/vda3", "/", "root", 256), mounts[0]
        )
        self.assertEqual(
            btrfs.BtrfsMount("/dev/vda3", "/home", "home", 257), mounts[1]
        )
        self.assertEqual(
            btrfs.BtrfsMount("/dev/vda3", "/mnt/top", "", 5), mounts[2]
        )
        # Escaped mount point and subvolume paths are decoded
        self.assertEqual(
            btrfs.BtrfsMount("/dev/loop7", "/mnt/data vol", "data vol", 300),
            mounts[3],
        )

    def test_btrfs_mounts_no_btrfs(self):
        with _patch_proc_mounts(_PROC_MOUNTS_NO_BTRFS):
            self.assertEqual([], btrfs.btrfs_mounts())

    def test_btrfs_devices_present(self):
        with _patch_proc_mounts(_PROC_MOUNTS):
            self.assertEqual(True, btrfs.btrfs_devices_present())

    def test_btrfs_devices_present_no_btrfs(self):
        with _patch_proc_mounts(_PROC_MOUNTS_NO_BTRFS):
            self.assertEqual(False, btrfs.btrfs_devices_present())

    def test_btrfs_devices(self):
        with _patch_proc_mounts(_PROC_MOUNTS):
            devices = btrfs.btrfs_devices()
        # One entry per file system
        self.assertEqual(["/dev/vda3", "/dev/loop7"], devices)

    def test_mount_for_path(self):
        with _patch_proc_mounts(_PROC_MOUNTS):
            mount = btrfs.mount_for_path("/home")
        self.assertEqual("/dev/vda3", mount.device)
        self.assertEqual("home", mount.subvol)
        self.assertEqual(257, mount.subvolid)

    def test_mount_for_path_not_btrfs(self):
        with _patch_proc_mounts(_PROC_MOUNTS):
            self.assertEqual(None, btrfs.mount_for_path("/boot"))

    @patch("snapm.manager.plugins.btrfs._same_device", lambda dev, other: dev == other)
    def test__find_top_level_mount(self):
        with _patch_proc_mounts(_PROC_MOUNTS):
            mount = btrfs._find_top_level_mount("/dev/vda3")
        self.assertEqual("/mnt/top", mount.mount_point)

    @patch("snapm.manager.plugins.btrfs._same_device", lambda dev, other: dev == other)
    def test__find_top_level_mount_not_mounted(self):
        with _patch_proc_mounts(_PROC_MOUNTS):
            self.assertEqual(None, btrfs._find_top_level_mount("/dev/loop7"))

    def test_is_btrfs_device_no_such_device(self):
        self.assertEqual(False, btrfs.is_btrfs_device("/dev/nosuchdevice"))

    def test_is_btrfs_device_not_block_device(self):
        self.assertEqual(False, btrfs.is_btrfs_device("/etc/fstab"))

    def test__check_btrfs_version(self):
        versions = {
            "btrfs-progs v7.1\n": True,
            "btrfs-progs v6.14\n": True,
            "btrfs-progs v5.4\n": True,
            "btrfs-progs v5.4.1\n": True,
            "btrfs-progs v5.3\n": False,
            "btrfs-progs v4.15.1\n": False,
        }
        plugin = _mock_plugin()
        for version, supported in versions.items():
            with self.subTest(version=version):
                with patch.object(btrfs, "_btrfs", return_value=version):
                    if supported:
                        plugin._check_btrfs_version()
                    else:
                        with self.assertRaises(SnapmPluginError):
                            plugin._check_btrfs_version()

    def test__check_btrfs_version_unparseable(self):
        plugin = _mock_plugin()
        with patch.object(btrfs, "_btrfs", return_value="btrfs-progs vfoo\n"):
            with self.assertRaises(SnapmPluginError):
                plugin._check_btrfs_version()


def _mock_plugin():
    """
    Return a ``Btrfs`` plugin instance with dependency and version checks
    disabled.
    """
    with patch.object(btrfs.Btrfs, "_check_btrfs_present"), patch.object(
        btrfs.Btrfs, "_check_btrfs_version"
    ):
        return btrfs.Btrfs(log, ConfigParser())


class BtrfsSnapshotTests(unittest.TestCase):
    """Test BtrfsSnapshot object properties"""

    def setUp(self):
        self.provider = Mock()
        self.provider.name = "btrfs"
        self.provider.fs_info.return_value = btrfs.BtrfsFsInfo(
            0,
            {
                "root": {},
                "root-snapset_test_1721136677_-home": {},
            },
            2 * 2**30,
            1 * 2**30,
        )
        self.snapshot = btrfs.BtrfsSnapshot(
            "/dev/vda3:root-snapset_test_1721136677_-home",
            "test",
            "root",
            1721136677,
            "/home",
            self.provider,
            "/dev/vda3",
            "root-snapset_test_1721136677_-home",
            258,
        )

    def test_btrfs_snapshot_fields(self):
        self.assertEqual("/dev/vda3", self.snapshot.what)
        self.assertEqual(258, self.snapshot.subvolid)
        self.assertEqual("/dev/vda3:root", self.snapshot.origin)
        self.assertEqual("/dev/vda3", self.snapshot.devpath)
        self.assertEqual("subvol=/root", self.snapshot.origin_options)
        self.assertEqual(
            "subvol=/root-snapset_test_1721136677_-home",
            self.snapshot.snapshot_options,
        )
        self.assertEqual(True, self.snapshot.autoactivate)

    def test_btrfs_snapshot_str(self):
        snapshot_str = str(self.snapshot)
        self.assertTrue("Device:         /dev/vda3" in snapshot_str)
        self.assertTrue("SubvolumeID:    258" in snapshot_str)

    def test_btrfs_snapshot_size_free(self):
        self.assertEqual(2 * 2**30, self.snapshot.size)
        self.assertEqual(1 * 2**30, self.snapshot.free)

    def test_btrfs_snapshot_status_active(self):
        self.assertEqual(SnapStatus.ACTIVE, self.snapshot.status)

    def test_btrfs_snapshot_status_invalid(self):
        self.provider.fs_info.return_value = btrfs.BtrfsFsInfo(
            0, {"root": {}}, 2 * 2**30, 1 * 2**30
        )
        self.assertEqual(SnapStatus.INVALID, self.snapshot.status)

    def test_btrfs_snapshot_status_reverting(self):
        subvolumes = dict(self.provider.fs_info.return_value.subvolumes)
        subvolumes["root.snapm-revert.1721136677"] = {}
        self.provider.fs_info.return_value = btrfs.BtrfsFsInfo(
            0, subvolumes, 2 * 2**30, 1 * 2**30
        )
        self.assertEqual(SnapStatus.REVERTING, self.snapshot.status)

    def test_btrfs_snapshot_invalidate_cache(self):
        self.snapshot.invalidate_cache()
        self.provider.invalidate_fs_cache.assert_called_with("/dev/vda3")

    @patch("snapm.manager.plugins.btrfs._same_device", lambda dev, other: dev == other)
    def test_btrfs_snapshot_mounted(self):
        # All subvolumes of a btrfs file system share one device: the
        # mounted subvolume must match as well as the device.
        mounts = _PROC_MOUNTS.replace(
            "subvolid=257,subvol=/home",
            "subvolid=258,subvol=/root-snapset_test_1721136677_-home",
        )
        with _patch_proc_mounts(mounts):
            self.assertEqual(True, self.snapshot.snapshot_mounted)
            self.assertEqual(True, self.snapshot.origin_mounted)

    @patch("snapm.manager.plugins.btrfs._same_device", lambda dev, other: dev == other)
    def test_btrfs_snapshot_not_mounted(self):
        with _patch_proc_mounts(_PROC_MOUNTS):
            self.assertEqual(False, self.snapshot.snapshot_mounted)
            self.assertEqual(True, self.snapshot.origin_mounted)


@unittest.skipIf(not have_root(), "requires root privileges")
@unittest.skipIf(not have_btrfs(), "requires btrfs-progs")
class BtrfsTests(unittest.TestCase):
    """Test Btrfs plugin operations using a loop backed file system"""

    btrfs_volumes = ["root", "home"]

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)

        def cleanup():
            log.debug("Cleaning up Btrfs (%s)", self._testMethodName)
            if hasattr(self, "_btrfs"):
                self._btrfs.destroy()

        self.addCleanup(cleanup)

        self._btrfs = BtrfsLoopBacked(self.btrfs_volumes)
        self._plugin = btrfs.Btrfs(log, ConfigParser())

    def _mount_point(self, name):
        return os.path.join(self._btrfs.mount_root, name)

    def _origin(self, name):
        return self._plugin.origin_from_mount_point(self._mount_point(name))

    def _create_snapshot(self, name, snapset="test", timestamp=1721136677):
        mount_point = self._mount_point(name)
        origin = self._origin(name)
        self._plugin.start_transaction()
        self._plugin.check_create_snapshot(
            origin, snapset, timestamp, mount_point, None
        )
        snapshot = self._plugin.create_snapshot(
            origin, snapset, timestamp, mount_point, None
        )
        self._plugin.end_transaction()
        return snapshot

    def test_btrfs_plugin_priority(self):
        self.assertEqual(btrfs.BTRFS_STATIC_PRIORITY, self._plugin.priority)

    def test_origin_from_mount_point(self):
        origin = self._origin("root")
        (device, subvol) = btrfs.device_subvol_from_origin(origin)
        self.assertEqual(os.path.realpath(self._btrfs.device), device)
        self.assertEqual("root", subvol)

    def test_origin_from_mount_point_not_btrfs(self):
        self.assertEqual(None, self._plugin.origin_from_mount_point("/proc"))

    def test_btrfs_can_snapshot(self):
        for mount_point in self._btrfs.mount_points():
            self.assertEqual(True, self._plugin.can_snapshot(mount_point))

    def test_btrfs_can_snapshot_block_device(self):
        # Btrfs sources must be given as a mount point
        self.assertEqual(False, self._plugin.can_snapshot(self._btrfs.device))

    def test_btrfs_can_snapshot_snapshot_raises(self):
        snapshot = self._create_snapshot("root")
        self._btrfs.umount("home")
        self._btrfs.mount("home", subvol=snapshot.subvol)
        self.addCleanup(self._btrfs.mount, "home")
        self.addCleanup(self._btrfs.umount, "home")
        with self.assertRaises(SnapmRecursionError) as cm:
            self._plugin.can_snapshot(self._mount_point("home"))

    def test_btrfs_discover_snapshots(self):
        self._btrfs.create_snapshot("root", "root-snapset_test_1721136677_-opt")
        self._btrfs.create_snapshot("home", "home-snapset_test_1721136677_-data")
        snapshots = self._plugin.discover_snapshots()
        self.assertEqual(2, len(snapshots))
        for snapshot in snapshots:
            self.assertEqual("test", snapshot.snapset_name)
            self.assertEqual(1721136677, snapshot.timestamp)
            self.assertEqual(SnapStatus.ACTIVE, snapshot.status)
        self.assertEqual(
            sorted(["/opt", "/data"]),
            sorted([snapshot.mount_point for snapshot in snapshots]),
        )

    def test_btrfs_discover_snapshots_ignores_other_subvolumes(self):
        # Neither plain subvolumes nor snapshots with a non-snapset name
        # are discovered.
        self._btrfs.create_subvolume("data")
        self._btrfs.create_snapshot("root", "rootbackup")
        self.assertEqual([], self._plugin.discover_snapshots())

    def test_btrfs_discover_snapshots_orphan_snapshot(self):
        # A snapshot whose origin has been deleted is not discovered
        self._btrfs.create_subvolume("data")
        self._btrfs.create_snapshot("data", "data-snapset_test_1721136677_-data")
        self._btrfs.delete_subvolume("data")
        self.assertEqual([], self._plugin.discover_snapshots())

    def test_btrfs_create_snapshot(self):
        snapshot = self._create_snapshot("home")
        self.assertEqual(
            "home-snapset_test_1721136677_"
            f"{btrfs.encode_mount_point(self._mount_point('home'))}",
            snapshot.subvol,
        )
        self.assertEqual(self._origin("home"), snapshot.origin)
        self.assertEqual(os.path.realpath(self._btrfs.device), snapshot.devpath)
        self.assertEqual("subvol=/home", snapshot.origin_options)
        self.assertEqual(SnapStatus.ACTIVE, snapshot.status)
        self.assertEqual(True, snapshot.origin_mounted)
        self.assertEqual(False, snapshot.snapshot_mounted)
        self.assertTrue(snapshot.size > 0)
        self.assertTrue(snapshot.free > 0)
        self.assertTrue(snapshot.subvol in self._btrfs.list_subvolumes())

    def test_btrfs_create_snapshot_duplicate(self):
        self._create_snapshot("home")
        with self.assertRaises(SnapmPluginError) as cm:
            self._create_snapshot("home")

    def test_btrfs_create_snapshot_content(self):
        self._btrfs.touch_path("home/testfile")
        snapshot = self._create_snapshot("home")
        os.unlink(os.path.join(self._mount_point("home"), "testfile"))
        self._btrfs.umount("root")
        self._btrfs.mount("root", subvol=snapshot.subvol)
        self.addCleanup(self._btrfs.mount, "root")
        self.addCleanup(self._btrfs.umount, "root")
        self.assertEqual(True, self._btrfs.test_path("root/testfile"))

    def test_btrfs_delete_snapshot(self):
        snapshot = self._create_snapshot("home")
        self._plugin.delete_snapshot(snapshot.name)
        self.assertFalse(snapshot.subvol in self._btrfs.list_subvolumes())
        self.assertEqual([], self._plugin.discover_snapshots())

    def test_btrfs_rename_snapshot(self):
        snapshot = self._create_snapshot("home")
        renamed = self._plugin.rename_snapshot(
            snapshot.name,
            snapshot.origin,
            "test1",
            1721136678,
            snapshot.mount_point,
        )
        self.assertEqual("test1", renamed.snapset_name)
        self.assertEqual(1721136678, renamed.timestamp)
        self.assertEqual(snapshot.mount_point, renamed.mount_point)
        self.assertEqual(snapshot.origin, renamed.origin)
        self.assertEqual(snapshot.subvolid, renamed.subvolid)
        self.assertTrue(renamed.subvol in self._btrfs.list_subvolumes())
        self.assertFalse(snapshot.subvol in self._btrfs.list_subvolumes())

    def test_btrfs_revert_snapshot(self):
        self._btrfs.touch_path("home/testfile")
        snapshot = self._create_snapshot("home")
        os.unlink(os.path.join(self._mount_point("home"), "testfile"))

        self._plugin.check_revert_snapshot(snapshot.name, snapshot.origin)
        self._plugin.revert_snapshot(snapshot.name)

        # The origin subvolume has been replaced by a snapshot of the
        # reverted snapshot and the previous content preserved.
        subvolumes = self._btrfs.list_subvolumes()
        self.assertTrue("home" in subvolumes)
        self.assertEqual(
            1, len(btrfs._find_in_progress_revert(dict.fromkeys(subvolumes, {}), "home"))
        )

        # The revert takes effect at the next mount of the origin
        self._btrfs.umount("home")
        self._btrfs.mount("home")
        self.assertEqual(True, self._btrfs.test_path("home/testfile"))

    def test_btrfs_revert_snapshot_status(self):
        snapshot = self._create_snapshot("home")
        self._plugin.revert_snapshot(snapshot.name)
        self.assertEqual(SnapStatus.REVERTING, snapshot.status)

        # The snapshot set is still discoverable while the revert is pending
        snapshots = self._plugin.discover_snapshots()
        self.assertEqual(1, len(snapshots))
        self.assertEqual(snapshot.origin, snapshots[0].origin)
        self.assertEqual(SnapStatus.REVERTING, snapshots[0].status)

    def test_btrfs_revert_snapshot_in_progress(self):
        snapshot = self._create_snapshot("home")
        self._plugin.revert_snapshot(snapshot.name)
        with self.assertRaises(SnapmBusyError) as cm:
            self._plugin.check_revert_snapshot(snapshot.name, snapshot.origin)
        with self.assertRaises(SnapmBusyError) as cm:
            self._plugin.revert_snapshot(snapshot.name)

    def test_btrfs_resize_snapshot_is_nop(self):
        snapshot = self._create_snapshot("home")
        self._plugin.start_transaction()
        self._plugin.check_resize_snapshot(
            snapshot.name, snapshot.origin, snapshot.mount_point, None
        )
        self._plugin.resize_snapshot(
            snapshot.name, snapshot.origin, snapshot.mount_point, None
        )
        self._plugin.end_transaction()
        self.assertEqual(SnapStatus.ACTIVE, snapshot.status)

    def test_btrfs_activation_is_nop(self):
        snapshot = self._create_snapshot("home")
        self._plugin.activate_snapshot(snapshot.name)
        self._plugin.deactivate_snapshot(snapshot.name)
        self._plugin.set_autoactivate(snapshot.name, auto=False)
        self.assertEqual(True, snapshot.autoactivate)

    def test_btrfs_check_create_snapshot_limits(self):
        config = ConfigParser()
        config.read_dict({"Limits": {"MaxSnapshotsPerOrigin": "1"}})
        plugin = btrfs.Btrfs(log, config)

        self._btrfs.create_snapshot("home", "home-snapset_test_1721136677_-data")
        plugin.discover_snapshots()

        plugin.start_transaction()
        with self.assertRaises(SnapmLimitError) as cm:
            plugin.check_create_snapshot(
                self._origin("home"),
                "test1",
                1721136678,
                self._mount_point("home"),
                None,
            )
        plugin.end_transaction()

    def test_btrfs_check_create_snapshot_no_space(self):
        self._plugin.start_transaction()
        with self.assertRaises(SnapmNoSpaceError) as cm:
            self._plugin.check_create_snapshot(
                self._origin("home"),
                "test",
                1721136677,
                self._mount_point("home"),
                "100G",
            )
        self._plugin.end_transaction()

    def test_btrfs_snapshot_name_too_long(self):
        self._plugin.start_transaction()
        with self.assertRaises(SnapmInvalidIdentifierError) as cm:
            self._plugin.check_create_snapshot(
                self._origin("home"),
                "a" * btrfs.BTRFS_MAX_NAME_LEN,
                1721136677,
                self._mount_point("home"),
                None,
            )
        self._plugin.end_transaction()

    def test_btrfs_fs_info_cache(self):
        device = os.path.realpath(self._btrfs.device)
        info = self._plugin.fs_info(device)
        self.assertTrue("root" in info.subvolumes)
        # A second call within BTRFS_CACHE_VALID returns the cached data
        self.assertTrue(info is self._plugin.fs_info(device))
        self._plugin.invalidate_fs_cache(device)
        self.assertFalse(info is self._plugin.fs_info(device))

    def test_btrfs_top_level_mount_reused(self):
        # An existing top level mount is used in place of a temporary mount
        self._btrfs.mount_top()
        self.addCleanup(self._btrfs.umount_top)
        with self._plugin._top_level(self._btrfs.device) as top_level:
            self.assertEqual(self._btrfs.top_root, top_level)
