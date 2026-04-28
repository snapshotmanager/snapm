# Copyright Red Hat
#
# tests/test_manager.py - Manager core unit tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import Mock, patch
from datetime import datetime
import logging
import os
import os.path
from uuid import UUID
from json import loads
import tempfile
import errno

import snapm
import snapm.manager as manager
import snapm.manager._manager as _manager
import boom

from tests import have_root, BOOT_ROOT_TEST, MockPlugin
from ._util import LvmLoopBacked, StratisLoopBacked, _VG_NAME


log = logging.getLogger()

boom.set_boot_path(BOOT_ROOT_TEST)


@unittest.skipIf(not have_root(), "requires root privileges")
class ManagerTestsSimple(unittest.TestCase):
    """
    Test manager interfaces with mock callouts
    """

    _old_path = None

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)
        bin_path = os.path.abspath("tests/bin")
        cur_path = os.environ["PATH"]
        self._old_path = cur_path
        os.environ["PATH"] = bin_path + os.pathsep + cur_path

    def tearDown(self):
        log.debug("Tearing down %s", self._testMethodName)
        os.environ["PATH"] = self._old_path

    def test_manager(self):
        m = manager.Manager()
        self.assertEqual(len(m.snapshot_sets), 3)

    def test_find_snapshot_sets_all(self):
        m = manager.Manager()
        s = snapm.Selection()
        sets = m.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 3)

    def test_find_snapshots_all(self):
        m = manager.Manager()
        s = snapm.Selection()
        snaps = m.find_snapshots(selection=s)
        self.assertEqual(len(snaps), 16)

    def test_snapset_to_str(self):
        m = manager.Manager()
        sets = m.find_snapshot_sets()
        set_str = str(sets[0])
        self.assertTrue(isinstance(set_str, str))

    def test_snapshot_to_str(self):
        m = manager.Manager()
        snaps = m.find_snapshots()
        snap_str = str(snaps[0])
        self.assertTrue(isinstance(snap_str, str))

    def test_snapset_time(self):
        m = manager.Manager()
        s = snapm.Selection(name="backup")
        sets = m.find_snapshot_sets(selection=s)
        self.assertEqual(sets[0].time, "2023-09-05 13:40:53")

    def test_snapshot_time(self):
        m = manager.Manager()
        s = snapm.Selection(name="backup")
        sets = m.find_snapshot_sets(selection=s)
        snap = sets[0].snapshots[0]
        self.assertEqual(snap.time, "2023-09-05 13:40:53")

    def test_snapset_mount_points(self):
        m = manager.Manager()
        s = snapm.Selection(name="backup")
        sets = m.find_snapshot_sets(selection=s)
        self.assertEqual(sets[0].mount_points, ["/home", "/opt", "/", "/data", "/srv"])

    def test_snapset_find_by_mount_points(self):
        m = manager.Manager()
        s = snapm.Selection(mount_points=["/home", "/opt", "/", "/data", "/srv"])
        sets = m.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 2)

    def test_snapset_find_by_timestamp(self):
        m = manager.Manager()
        s = snapm.Selection(timestamp=1693921253)
        sets = m.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)

    def test_snapset_find_by_nr_snapshots(self):
        m = manager.Manager()
        s = snapm.Selection(nr_snapshots=5)
        sets = m.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 2)

    def test__check_lock_dir(self):
        _manager = manager._manager
        _orig_lock_dir = _manager._SNAPM_LOCK_DIR
        self.addCleanup(setattr, _manager, "_SNAPM_LOCK_DIR", _orig_lock_dir)
        with tempfile.TemporaryDirectory(suffix="_test_run_lock", dir="/tmp") as tempdir:
            _manager._SNAPM_LOCK_DIR = os.path.join(str(tempdir), _orig_lock_dir.lstrip(os.sep))
            self.assertEqual(_manager._check_lock_dir(), _manager._SNAPM_LOCK_DIR)
            st = os.stat(_manager._SNAPM_LOCK_DIR)
            self.assertEqual(st.st_mode & 0o777, _manager._SNAPM_LOCK_DIR_MODE)

    def test__lock_unlock_manager(self):
        _manager = manager._manager
        _orig_lock_dir = _manager._SNAPM_LOCK_DIR
        self.addCleanup(setattr, _manager, "_SNAPM_LOCK_DIR", _orig_lock_dir)
        with tempfile.TemporaryDirectory(suffix="_test_run_lock", dir="/tmp") as tempdir:
            _manager._SNAPM_LOCK_DIR = str(tempdir) + _orig_lock_dir
            lockdir = _manager._check_lock_dir()
            fd = _manager._lock_manager(lockdir)
            self.assertGreater(fd, 0)
            self.assertTrue(os.path.exists(os.path.join(lockdir, "manager.lock")))
            _manager._unlock_manager(lockdir, fd)
            with self.assertRaises(OSError) as cm:
                os.dup(fd)
            self.assertEqual(cm.exception.errno, errno.EBADF)

    def test__check_mounts_dir(self):
        _manager = manager._manager
        _orig_mounts_dir = _manager._SNAPM_MOUNTS_DIR
        self.addCleanup(setattr, _manager, "_SNAPM_MOUNTS_DIR", _orig_mounts_dir)
        with tempfile.TemporaryDirectory(suffix="_test_run_mounts", dir="/tmp") as tempdir:
            _manager._SNAPM_MOUNTS_DIR = os.path.join(str(tempdir), _orig_mounts_dir.lstrip(os.sep))
            self.assertEqual(_manager._check_mounts_dir(), _manager._SNAPM_MOUNTS_DIR)
            st = os.stat(_manager._SNAPM_MOUNTS_DIR)
            self.assertEqual(st.st_mode & 0o777, _manager._SNAPM_MOUNTS_DIR_MODE)

    @patch("builtins.open", new_callable=unittest.mock.mock_open, read_data="/dev/sda1 /boot xfs rw 0 0\n/dev/mapper/vg-lv /home ext4 rw 0 0\n")
    @patch("snapm.manager._manager.exists", return_value=True)
    @patch("snapm.manager._manager.samefile", return_value=True)
    def test_find_mount_point_for_devpath(self, mock_samefile, _mock_exists, _mock_open):
        """Test resolving device path to mount point via /proc/mounts."""
        mock_samefile.side_effect = lambda a, b: a == b

        # Match found
        mp = _manager._find_mount_point_for_devpath("/dev/mapper/vg-lv")
        self.assertEqual(mp, "/home")

        # No match found
        mp = _manager._find_mount_point_for_devpath("/dev/sdb1")
        self.assertEqual(mp, "")

    @patch("snapm.manager._manager.SNAPM_RUNTIME_DIR", "/run/snapm_test")
    @patch("snapm.manager._manager.exists")
    @patch("os.lstat")
    def test_check_snapm_runtime_dir(self, mock_lstat, mock_exists):
        """Test validation of the runtime directory security."""
        # Case 1: Directory exists and is secure (0700, root:root)
        mock_exists.return_value = True

        # lstat/stat returns dir, not symlink, correct perms
        stat_res = Mock()
        stat_res.st_mode = 0o40700 # S_IFDIR | 0700
        stat_res.st_uid = 0
        stat_res.st_gid = 0
        mock_lstat.return_value = stat_res

        _manager._check_snapm_runtime_dir() # Should not raise

        # Case 2: Wrong permissions (e.g., world readable)
        stat_res.st_mode = 0o40777
        with self.assertRaises(snapm.SnapmSystemError):
            _manager._check_snapm_runtime_dir()

        # Case 3: Not owned by root
        stat_res.st_mode = 0o40700
        stat_res.st_uid = 1000
        with self.assertRaises(snapm.SnapmSystemError):
            _manager._check_snapm_runtime_dir()

@unittest.skipIf(not have_root(), "requires root privileges")
class ManagerTests(unittest.TestCase):
    """
    Tests for snapm.manager.Manager that apply to all supported snapshot
    providers.
    """
    volumes = ["root", "var"]
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

        self.manager = snapm.manager.Manager()

    def mount_points(self):
        return self._lvm.mount_points() + self._stratis.mount_points()

    def stop_start_storage(self):
        self._lvm.umount_all()
        self._lvm.deactivate()
        self._lvm.activate()
        self._lvm.mount_all()

        self._stratis.umount_all()
        self._stratis.stop_pool()
        self._stratis.start_pool()
        self._stratis.mount_all()

    def test_find_snapshot_sets_none(self):
        sets = self.manager.find_snapshot_sets()
        self.assertEqual([], sets)

    def test_find_snapshot_sets_one(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        sets = self.manager.find_snapshot_sets()
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_find_snapshot_sets_with_selection_name(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        self.manager.create_snapshot_set("testset1", self.mount_points())
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_find_snapshot_sets_with_selection_uuid(self):
        set1 = self.manager.create_snapshot_set("testset0", self.mount_points())
        self.manager.create_snapshot_set("testset1", self.mount_points())
        s = snapm.Selection(uuid=set1.uuid)
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_create_snapshot_set(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)

        # Verify snapshot set JSON
        set_dict = loads(sets[0].json())
        self.assertTrue("SnapsetName" in set_dict)
        self.assertEqual(set_dict["SnapsetName"], "testset0")

        # Verify snapshot JSON
        snap_dict = loads(sets[0].snapshots[0].json())
        self.assertTrue("SnapsetName" in snap_dict)
        self.assertEqual(snap_dict["SnapsetName"], "testset0")

        # Verify that a source can be looked up by origin
        self.assertTrue(sets[0].snapshot_by_source(sets[0].snapshots[0].origin))

        # Verify that a non-existent source raises an exception
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            sets[0].snapshot_by_source("/quux")

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_blockdevs(self):
        snapset = self.manager.create_snapshot_set(
            "testset0", self._lvm.block_devs() + self._stratis.block_devs()
        )
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_blockdevs_unmounted(self):
        self._lvm.umount_all()
        self._stratis.umount_all()
        snapset = self.manager.create_snapshot_set(
            "testset0", self._lvm.block_devs() + self._stratis.block_devs()
        )
        self._lvm.mount_all()
        self._stratis.mount_all()
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_blockdev_dupe_raises(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            snapset = self.manager.create_snapshot_set(
                "testset0", self._lvm.block_devs()[0] + self._lvm.mount_points()[0]
            )

    def test_create_snapshot_set_duplicate_sources_raises(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            snapset = self.manager.create_snapshot_set(
                "testset0", [self.mount_points()[0], self.mount_points()[0]],
            )

    def test_create_snapshot_set_mixed_1(self):
        self._stratis.umount_all()
        snapset = self.manager.create_snapshot_set(
            "testset0", self._lvm.mount_points() + self._stratis.block_devs()
        )
        self._stratis.mount_all()
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_mixed_2(self):
        self._lvm.umount_all()
        snapset = self.manager.create_snapshot_set(
            "testset0", self._lvm.block_devs() + self._stratis.mount_points()
        )
        self._lvm.mount_all()
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_default_size_policy(self):
        self.manager.create_snapshot_set(
            "testset0", self.mount_points(), default_size_policy="10%FREE"
        )
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_size_policies_10_free(self):
        mount_specs = [f"{mp}:10%FREE" for mp in self.mount_points()]
        self.manager.create_snapshot_set("testset0", mount_specs)
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_size_policies_10_size(self):
        mount_specs = [f"{mp}:10%SIZE" for mp in self.mount_points()]
        self.manager.create_snapshot_set("testset0", mount_specs)
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_size_policies_200_used(self):
        mount_specs = [f"{mp}:200%USED" for mp in self.mount_points()]
        self.manager.create_snapshot_set("testset0", mount_specs)
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_size_policies_100_size(self):
        mount_specs = [f"{mp}:100%SIZE" for mp in self.mount_points()]
        self.manager.create_snapshot_set("testset0", mount_specs)
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_size_policies_fixed(self):
        mount_specs = [f"{mp}:512MiB" for mp in self.mount_points()]
        self.manager.create_snapshot_set("testset0", mount_specs)
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_size_policy_size_over_100_raises(self):
        with self.assertRaises(snapm.SnapmSizePolicyError) as cm:
            self.manager.create_snapshot_set(
                "testset0", self.mount_points(), default_size_policy="150%SIZE"
            )

    def test_create_snapshot_set_size_policy_free_over_100_raises(self):
        with self.assertRaises(snapm.SnapmSizePolicyError) as cm:
            self.manager.create_snapshot_set(
                "testset0", self.mount_points(), default_size_policy="150%FREE"
            )

    def test_create_snapshot_set_size_policy_bad_units_raises(self):
        with self.assertRaises(snapm.SnapmSizePolicyError) as cm:
            self.manager.create_snapshot_set(
                "testset0", self.mount_points(), default_size_policy="2FiB"
            )

    def test_create_snapshot_set_size_policy_non_num_raises(self):
        with self.assertRaises(snapm.SnapmSizePolicyError) as cm:
            self.manager.create_snapshot_set(
                "testset0", self.mount_points(), default_size_policy="quux"
            )

    def test_create_snapshot_set_size_policies_blockdev_used_raises(self):
        dev_specs = [f"{dev}:100%USED" for dev in self._lvm.block_devs()]
        self._lvm.umount_all()
        with self.assertRaises(snapm.SnapmSizePolicyError) as cm:
            self.manager.create_snapshot_set("testset0", dev_specs)
        self._lvm.mount_all()

    def test_create_snapshot_set_bad_name_backslash(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set("bad\\name", self.mount_points())

    def test_create_snapshot_set_bad_name_underscore(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set("bad_name", self.mount_points())

    def test_create_snapshot_set_bad_name_slash(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set("bad/name", self.mount_points())

    def test_create_snapshot_set_bad_name_space(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set("bad name", self.mount_points())

    def test_create_snapshot_set_bad_name_at(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set("bad@name", self.mount_points())

    def test_create_snapshot_set_bad_name_pipe(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set("bad|name", self.mount_points())

    def test_create_snapshot_set_name_too_long(self):
        name = "a" * 127
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set(name, self.mount_points())

    def test_create_snapshot_set_no_space_raises(self):
        # Hack alert: override the default MaxSnapshotsPerOrigin for this
        # test.
        for p in self.manager.plugins:
            if p.limits:
                p.limits.snapshots_per_origin = 100

        with self.assertRaises(snapm.SnapmNoSpaceError):
            for i in range(0, 50):
                self.manager.create_snapshot_set(
                    f"testset{i}", self.mount_points()
                )

    def test_create_delete_snapshot_set(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="testset0")
        self.manager.delete_snapshot_sets(s)
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 0)

    def test_create_delete_snapshot_set_with_index(self):
        self.manager.create_snapshot_set("testset0", self.mount_points(), autoindex=True)
        select = snapm.Selection(name="testset0.0")
        sset = self.manager.find_snapshot_sets(selection=select)[0]
        self.assertEqual(sset.basename, "testset0")
        self.assertEqual(sset.index, 0)
        self.assertEqual(sset.index, sset.snapshots[0].index)
        self.assertEqual(sset.basename, sset.snapshots[0].snapset_basename)
        self.manager.delete_snapshot_sets(selection=select)

    def test_create_delete_snapshot_set_no_index(self):
        self.manager.create_snapshot_set("testset0", self.mount_points(), autoindex=False)
        select = snapm.Selection(name="testset0")
        sset = self.manager.find_snapshot_sets(selection=select)[0]
        self.assertEqual(sset.basename, "testset0")
        self.assertEqual(sset.index, snapm.SNAPSET_INDEX_NONE)
        self.manager.delete_snapshot_sets(selection=select)

    def test_create_delete_snapshot_set_dot_no_index(self):
        self.manager.create_snapshot_set("foo.bar", self.mount_points(), autoindex=False)
        select = snapm.Selection(name="foo.bar")
        sset = self.manager.find_snapshot_sets(selection=select)[0]
        self.assertEqual(sset.basename, "foo.bar")
        self.assertEqual(sset.index, snapm.SNAPSET_INDEX_NONE)
        self.manager.delete_snapshot_sets(selection=select)

    def test_snapshot_set_is_mounted(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points(), autoindex=False)

        # Origin is already mounted
        self.assertTrue(sset.origin_mounted)
        self.assertTrue(sset.mounted)

        # Snapshot is not yet mounted
        self.assertFalse(sset.snapshot_mounted)

        mnt_snap = sset.snapshots[0]
        mnt_name = mnt_snap.name.split("/")[1]

        self._lvm.make_mount_point(mnt_name)
        self._lvm.mount(mnt_name)

        # Snapshot is now mounted
        self.assertTrue(sset.snapshot_mounted)
        self.assertTrue(sset.mounted)

        self._lvm.umount(mnt_name)
        # Snapshot is no longer mounted
        self.assertFalse(sset.snapshot_mounted)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_delete_snapshot_set_nosuch(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="nosuch")
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.delete_snapshot_sets(s)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_delete_snapshot_set_mounted(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="testset0")

        mnt_snap = sset.snapshots[0]
        mnt_name = mnt_snap.name.split("/")[1]

        self._lvm.make_mount_point(mnt_name)
        self._lvm.mount(mnt_name)

        with self.assertRaises(snapm.SnapmBusyError) as cm:
            self.manager.delete_snapshot_sets(s)

        self._lvm.umount(mnt_name)
        self.manager.delete_snapshot_sets(s)

    def test_delete_err_raises(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())
        selection = snapm.Selection(name="testset0")

        def fail_delete():
            raise snapm.SnapmError("Error deleting snapshot")

        orig_delete = sset.snapshots[1].delete
        sset.snapshots[1].delete = fail_delete

        with self.assertRaises(snapm.SnapmPluginError) as cm:
            self.manager.delete_snapshot_sets(selection)

        sset.snapshots[0].delete = orig_delete
        self.manager.delete_snapshot_sets(selection)

    def test_create_snapshot_set_duplicate(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        with self.assertRaises(snapm.SnapmExistsError) as cm:
            self.manager.create_snapshot_set("testset0", self.mount_points())
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_not_a_mount_point(self):
        mount_point = self.mount_points()[0]
        non_mount = os.path.join(mount_point, "etc")
        os.makedirs(non_mount)
        with self.assertRaises(snapm.SnapmPathError) as cm:
            self.manager.create_snapshot_set("testset0", [non_mount])

    @unittest.skipIf(not os.path.ismount("/boot"), "no suitable mount path")
    def test_create_snapshot_set_no_provider(self):
        with self.assertRaises(snapm.SnapmNoProviderError):
            self.manager.create_snapshot_set("testset0", ["/boot"])

    def test_rename_snapshot_set(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.rename_snapshot_set("testset0", "testset1")
        s1 = snapm.Selection(name="testset1")
        sets = self.manager.find_snapshot_sets(selection=s1)
        self.assertEqual(len(sets), 1)
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 0)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_rename_snapshot_set_nosuch(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.rename_snapshot_set("nosuch", "newname")
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_rename_snapshot_set_exists(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        self.manager.create_snapshot_set("testset1", self.mount_points())
        with self.assertRaises(snapm.SnapmExistsError) as cm:
            self.manager.rename_snapshot_set("testset0", "testset1")
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_rename_err_and_rollback(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        def fail_rename(_new_name):
            raise snapm.SnapmError("Error renaming snapshot")

        sset.snapshots[1].rename = fail_rename

        with self.assertRaises(snapm.SnapmPluginError) as cm:
            self.manager.rename_snapshot_set("testset0", "testset1")

        self.assertEqual(sset.name, "testset0")
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_find_snapshots(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        snaps = self.manager.find_snapshots()
        self.assertEqual(len(snaps), len(self.mount_points()))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_find_snapshots_with_selection(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        self.manager.create_snapshot_set("testset1", self.mount_points())
        s = snapm.Selection(name="testset0")
        snaps = self.manager.find_snapshots(selection=s)
        self.assertEqual(len(snaps), len(self.mount_points()))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_activate_deactivate_snapsets(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="testset0")
        self.manager.activate_snapshot_sets(selection=s)
        sets = self.manager.find_snapshot_sets(selection=s)
        for snap in sets[0].snapshots:
            self.assertEqual(snap.status, snapm.SnapStatus.ACTIVE)
        self.manager.deactivate_snapshot_sets(selection=s)
        for snap in sets[0].snapshots:
            if snap.origin.removeprefix(f"{_VG_NAME}/") in self.thin_volumes:
                self.assertEqual(snap.status, snapm.SnapStatus.INACTIVE)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_activate_snapsets_nosuch(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="nosuch")
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.activate_snapshot_sets(selection=s)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_deactivate_snapsets_nosuch(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="nosuch")
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.deactivate_snapshot_sets(selection=s)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_set_autoactivate_snapsets(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="testset0")
        self.manager.set_autoactivate(s, auto=False)
        sets = self.manager.find_snapshot_sets(selection=s)
        # autoactivate cannot be set to False for Stratis snapshots
        #for snap in sets[0].snapshots:
        #    self.assertEqual(snap.autoactivate, False)
        self.manager.set_autoactivate(s, auto=True)
        for snap in sets[0].snapshots:
            self.assertEqual(snap.autoactivate, True)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_set_autoactivate_snapsets_nosuch(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="nosuch")
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.set_autoactivate(s, True)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_revert_snapshot_sets(self):
        # Revert is only supported on LVM2: don't include Stratis in the test.
        origin_file = "root/origin"
        snapshot_file = "root/snapshot"
        testset = "testset0"

        # Create files in the origin volume and post-snapshot
        self._lvm.touch_path(origin_file)
        self.manager.create_snapshot_set(testset, self.mount_points())
        self._lvm.touch_path(snapshot_file)

        # Test that the origin and snapshot files both exist
        self.assertEqual(self._lvm.test_path(origin_file), True)
        self.assertEqual(self._lvm.test_path(snapshot_file), True)

        # Start revert the snapshot set
        selection = snapm.Selection(name=testset)
        self.manager.revert_snapshot_sets(selection)

        # Unmount the set, deactivate/reactivate and re-mount
        self.stop_start_storage()

        # Test that only the origin file exists
        self.assertEqual(self._lvm.test_path(origin_file), True)
        self.assertEqual(self._lvm.test_path(snapshot_file), False)

    def test_revert_reverting_snapshot_set_raises(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        selection = snapm.Selection(name=testset)
        self.manager.revert_snapshot_sets(selection)

        with self.assertRaises(snapm.SnapmBusyError) as cm:
            self.manager.revert_snapshot_sets(selection)

        # Unmount the set, deactivate/reactivate and re-mount
        self.stop_start_storage()

    def test_rename_reverting_snapshot_set_raises(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        selection = snapm.Selection(name=testset)
        self.manager.revert_snapshot_sets(selection)

        with self.assertRaises(snapm.SnapmStateError) as cm:
            self.manager.rename_snapshot_set(testset, "testset1")

        # Unmount the set, deactivate/reactivate and re-mount
        self.stop_start_storage()

    def test_delete_reverting_snapshot_set_raises(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        selection = snapm.Selection(name=testset)
        self.manager.revert_snapshot_sets(selection)

        with self.assertRaises(snapm.SnapmBusyError) as cm:
            self.manager.delete_snapshot_sets(selection)

        # Unmount the set, deactivate/reactivate and re-mount
        self.stop_start_storage()

    def test_revert_snapshot_sets_bad_name_raises(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.revert_snapshot_sets(snapm.Selection(name="nosuchset"))

    def test_revert_snapshot_set_bad_name_raises(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.revert_snapshot_set(name="nosuchset")

    def test_revert_snapshot_set_bad_uuid_raises(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.revert_snapshot_set(uuid=UUID("00000000-0000-0000-0000-000000000000"))

    def test_revert_snapshot_set_name_uuid_conflict_raises(self):
        sset1 = self.manager.create_snapshot_set("testset0", self.mount_points())
        sset2 = self.manager.create_snapshot_set("testset1", self.mount_points())

        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.revert_snapshot_set(name=sset1.name, uuid=sset2.uuid)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_resize_snapshot_set_bad_name_raises(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.resize_snapshot_set([], name="nosuchset")

    def test_resize_snapshot_set_bad_uuid_raises(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.resize_snapshot_set([], uuid=UUID("00000000-0000-0000-0000-000000000000"))

    def test_resize_snapshot_set_name_uuid_conflict_raises(self):
        sset1 = self.manager.create_snapshot_set("testset0", self.mount_points())
        sset2 = self.manager.create_snapshot_set("testset1", self.mount_points())

        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.resize_snapshot_set([], name=sset1.name, uuid=sset2.uuid)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_resize_snapshot_set_non_member_raises(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.resize_snapshot_set(["/home:1G"], name="testset0")

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_resize_snapshot_set_no_space_raises(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        with self.assertRaises(snapm.SnapmNoSpaceError) as cm:
            self.manager.resize_snapshot_set([], name="testset0", default_size_policy="20G")

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_resize_snapshot_set_mount_specs(self):
        testset = "testset0"
        mount_specs = [f"{mp}:512MiB" for mp in self.mount_points()]
        self.manager.create_snapshot_set(testset, mount_specs)

        snapset = self.manager.find_snapshot_sets(snapm.Selection(name=testset))[0]
        for snapshot in snapset.snapshots:
            if snapshot.provider.name == "lvm2cow":
                self.assertEqual(snapshot.size, 512 * 1024 ** 2)

        resize_specs = [f"{self._lvm.mount_root}/{name}:1GiB" for name in self._lvm.volumes]
        self.manager.resize_snapshot_set(resize_specs, name=testset)

        snapset = self.manager.find_snapshot_sets(snapm.Selection(name=testset))[0]
        for snapshot in snapset.snapshots:
            if snapshot.provider.name == "lvm2cow":
                self.assertEqual(snapshot.size, 1024 ** 3)

        self.manager.delete_snapshot_sets(snapm.Selection(name=testset))

    def test_resize_snapshot_set_default_size_policy(self):
        testset = "testset0"
        mount_specs = [f"{mp}:512MiB" for mp in self.mount_points()]
        self.manager.create_snapshot_set(testset, mount_specs)

        snapset = self.manager.find_snapshot_sets(snapm.Selection(name=testset))[0]
        for snapshot in snapset.snapshots:
            if snapshot.provider.name == "lvm2cow":
                self.assertEqual(snapshot.size, 512 * 1024 ** 2)

        self.manager.resize_snapshot_set(None, name=testset, default_size_policy="1G")

        snapset = self.manager.find_snapshot_sets(snapm.Selection(name=testset))[0]
        for snapshot in snapset.snapshots:
            if snapshot.provider.name == "lvm2cow":
                self.assertEqual(snapshot.size, 1024 ** 3)

    def test_split_snapshot_set_split(self):
        testset = "testset0"
        splitset = "testset1"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_source = self.mount_points()[0]
        nosplit_sources = self.mount_points()[1:]

        # Split split_source from snapshot set
        split = self.manager.split_snapshot_set(testset, splitset, [split_source])

        # Get updated snapshot set
        test = self.manager.find_snapshot_sets(selection=snapm.Selection(name=testset))[0]

        self.assertEqual(split.name, splitset)
        self.assertTrue(split_source in split.sources)
        self.assertTrue(split_source not in test.sources)
        for source in nosplit_sources:
            self.assertTrue(source in test.sources)

    def test_split_snapshot_set_split_bad_name(self):
        testset = "testset0"
        splitset = "testset1"
        badset = "badset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_source = self.mount_points()[0]

        with self.assertRaises(snapm.SnapmNotFoundError):
            # Attempt to split with bad snapset name
            split = self.manager.split_snapshot_set(badset, splitset, [split_source])

    def test_split_snapshot_set_split_empty_split(self):
        testset = "testset0"
        splitset = "testset1"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_sources = self.mount_points()

        with self.assertRaises(snapm.SnapmArgumentError):
            # Split split_source from snapshot set
            split = self.manager.split_snapshot_set(testset, splitset, split_sources)

    def test_split_snapshot_set_split_size_policy_raises(self):
        testset = "testset0"
        splitset = "testset1"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_sources = [f"{mp}:10%SIZE" for mp in self.mount_points()]

        with self.assertRaises(snapm.SnapmArgumentError):
            # Attempt to split with size policies in source_specs
            split = self.manager.split_snapshot_set(testset, splitset, split_sources)

    def test_split_snapshot_set_split_bad_source_raises(self):
        testset = "testset0"
        splitset = "testset1"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_source = "/quux"

        with self.assertRaises(snapm.SnapmNotFoundError):
            # Split non-existent source from snapshot set
            split = self.manager.split_snapshot_set(testset, splitset, [split_source])

    def test_split_snapshot_set_split_empty_sources(self):
        testset = "testset0"
        splitset = "testset1"
        self.manager.create_snapshot_set(testset, self.mount_points())

        with self.assertRaises(snapm.SnapmArgumentError):
            # Split [] from snapshot set
            split = self.manager.split_snapshot_set(testset, splitset, [])

    def test_split_snapshot_set_prune(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        prune_source = self.mount_points()[0]
        noprune_sources = self.mount_points()[1:]

        # Prune prune_source from snapshot set
        prune = self.manager.split_snapshot_set(testset, None, [prune_source])

        self.assertTrue(prune_source not in prune.sources)
        for source in noprune_sources:
            self.assertTrue(source in prune.sources)

    def test_split_snapshot_set_prune_bad_name(self):
        testset = "testset0"
        badset = "badset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_source = self.mount_points()[0]

        with self.assertRaises(snapm.SnapmNotFoundError):
            # Attempt to split with bad snapset name
            split = self.manager.split_snapshot_set(badset, None, [split_source])

    def test_split_snapshot_set_prune_empty_prune(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_sources = self.mount_points()

        with self.assertRaises(snapm.SnapmArgumentError):
            # Split split_source from snapshot set
            split = self.manager.split_snapshot_set(testset, None, split_sources)

    def test_split_snapshot_set_prune_empty_sources(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        with self.assertRaises(snapm.SnapmArgumentError):
            # Split [] from snapshot set
            split = self.manager.split_snapshot_set(testset, None, [])

    def test_split_snapshot_set_prune_size_policy_raises(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_sources = [f"{mp}:10%SIZE" for mp in self.mount_points()]

        with self.assertRaises(snapm.SnapmArgumentError):
            # Attempt to split with size policies in source_specs
            split = self.manager.split_snapshot_set(testset, None, split_sources)

    def test_split_snapshot_set_prune_bad_source_raises(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_source = "/quux"

        with self.assertRaises(snapm.SnapmNotFoundError):
            # Split non-existent source from snapshot set
            split = self.manager.split_snapshot_set(testset, None, [split_source])

    @patch("snapm.manager._manager.exists", return_value=True)
    @patch("snapm.manager._manager.ismount", return_value=True)
    def test_find_plugins_respects_priority(self, _ismount_mock, _exists_mock):
        """
        Verify that _find_and_verify_plugins selects the plugin with the
        highest numerical priority when multiple plugins support a source.
        """
        # 1. Create two mock plugins that support the same source
        source_path = "/dev/test/vol"

        class LowPrioPlugin(MockPlugin):
            name = "low-prio"

            def __init__(self, logger, plugin_cfg):
                super().__init__(logger, plugin_cfg)
                self.priority = 10

            def can_snapshot(self, source):
                return source == source_path

        class HighPrioPlugin(MockPlugin):
            name = "high-prio"

            def __init__(self, logger, plugin_cfg):
                super().__init__(logger, plugin_cfg)
                self.priority = 20

            def can_snapshot(self, source):
                return source == source_path

        # 2. Inject plugins
        self.manager.plugins = [LowPrioPlugin(Mock(), Mock()), HighPrioPlugin(Mock(), Mock())]

        # 3. Run selection
        sources = [source_path]
        size_policies = {source_path: None}

        # _find_and_verify_plugins(self, sources, size_policies, _requested_provider=None)
        provider_map = self.manager._find_and_verify_plugins(sources, size_policies)

        # 4. Assert the High Priority plugin was selected
        selected_plugin = provider_map[source_path]
        self.assertEqual(selected_plugin.name, "high-prio")
        self.assertEqual(selected_plugin.priority, 20)

    @patch("snapm.manager._manager.exists", return_value=True)
    @patch("snapm.manager._manager.ismount", return_value=True)
    def test_lvm2_cow_priority_override(self, _ismount_mock, _exists_mock):
        """
        Verify overriding lvm2-cow priority via config allows it to supersede
        lvm2-thin (simulating the 'thick snapshot of thin vol' scenario).
        """
        source_path = "/home"

        # Simulate lvm2-thin (usually higher priority for thin volumes)
        class Lvm2Thin(MockPlugin):
            name = "lvm2-thin"

            def __init__(self, logger, plugin_cfg):
                super().__init__(logger, plugin_cfg)
                self.priority = 15

            def can_snapshot(self, source):
                return True

        # Simulate lvm2-cow (usually lower priority)
        class Lvm2Cow(MockPlugin):
            name = "lvm2-cow"

            def __init__(self, logger, plugin_cfg):
                super().__init__(logger, plugin_cfg)
                self.priority = 10

            def can_snapshot(self, source):
                return True

        # Initialize plugins
        mock_logger = Mock()
        thin_plugin = Lvm2Thin(mock_logger, Mock())
        cow_plugin = Lvm2Cow(mock_logger, Mock())

        # Manually construct manager
        self.manager.plugins = [thin_plugin, cow_plugin]
        self.manager._log_debug = Mock()
        self.manager._log_error = Mock() # Suppress error logs during test

        sources = [source_path]
        size_policies = {source_path: "2%SIZE"}

        # CASE A: Default priorities
        # Expect lvm2-thin to be selected because 15 > 10
        provider_map = self.manager._find_and_verify_plugins(sources, size_policies)
        self.assertEqual(provider_map[source_path].name, "lvm2-thin")

        # CASE B: Dynamic Override
        # Simulate the user editing /etc/snapm/plugins.d/lvm2-cow.conf
        # by manually modifying the plugin instance priority.
        cow_plugin.priority = 100

        # Re-run selection
        provider_map = self.manager._find_and_verify_plugins(sources, size_policies)

        # Expect lvm2-cow to be selected because 100 > 15
        self.assertEqual(provider_map[source_path].name, "lvm2-cow")

    @patch("snapm.manager._manager.exists", return_value=True)
    @patch("snapm.manager._manager.ismount", return_value=True)
    def test_find_plugins_priority_tie_raises(self, _ismount_mock, _exists_mock):
        source_path = "/dev/test/vol"

        class Prio1(MockPlugin):
            name = "prio1"
            def __init__(self, logger, plugin_cfg):
                super().__init__(logger, plugin_cfg)
                self.priority = 10
            def can_snapshot(self, _source): return True

        class Prio2(MockPlugin):
            name = "prio2"
            def __init__(self, logger, plugin_cfg):
                super().__init__(logger, plugin_cfg)
                self.priority = 10
            def can_snapshot(self, _source): return True

        mock_logger = Mock()
        self.manager.plugins = [Prio1(Mock(), Mock()), Prio2(Mock(), Mock())]
        sources = [source_path]
        size_policies = {source_path: None}

        with self.assertRaises(snapm.SnapmNoProviderError):
            self.manager._find_and_verify_plugins(sources, size_policies)


@unittest.skipIf(not have_root(), "requires root privileges")
class ManagerTestsThin(unittest.TestCase):
    """
    Tests for snapm.manager.Manager that apply only to thin provisioned
    snapshot providers (lvm2thin and stratis).
    """
    volumes = [] # Do not use lvm2cow
    thin_volumes = ["root", "var"]
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

        self.manager = snapm.manager.Manager()

    def mount_points(self):
        return self._lvm.mount_points() + self._stratis.mount_points()

    def test_create_snapshot_set_recursion_raises(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())
        self.manager.activate_snapshot_sets(snapm.Selection(name="testset0"))
        snap_devs = [snapshot.devpath for snapshot in sset.snapshots]
        with self.assertRaises(snapm.SnapmRecursionError) as cm:
            sset = self.manager.create_snapshot_set("testset1", snap_devs)


class TestTimelineCategorization(unittest.TestCase):
    """Tests for snapshot set timeline categorization logic."""

    def test__categorize_snapshot_sets(self):
        """Test classification of snapshots into timeline categories."""
        # Helper to create a mock snapshot set
        def make_set(ts_str):
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            m = Mock()
            m.datetime = dt
            m.timestamp = dt.timestamp()
            # id() relies on object identity, so distinct mocks work
            return m

        # 1. Yearly/Quarterly/Monthly/Daily/Hourly (Jan 1 2023, Sunday)
        # Note: Weekly requires Monday (weekday 0). Jan 1 2023 is Sunday (6).
        s1 = make_set("2023-01-01 00:00:00")

        # 2. Hourly later same day
        s2 = make_set("2023-01-01 01:00:00")

        # 3. Weekly (Monday Jan 2)
        # Also Daily/Hourly for Jan 2
        s3 = make_set("2023-01-02 00:00:00")

        # 4. Monthly (Feb 1)
        s4 = make_set("2023-02-01 00:00:00")

        # 5. Quarterly (Apr 1)
        s5 = make_set("2023-04-01 00:00:00")

        sets = [s1, s2, s3, s4, s5]

        categorized = _manager._categorize_snapshot_sets(sets)

        # Check s1 (Jan 1)
        self.assertIn("yearly", categorized[id(s1)])
        self.assertIn("quarterly", categorized[id(s1)])
        self.assertIn("monthly", categorized[id(s1)])
        self.assertIn("daily", categorized[id(s1)])
        self.assertIn("hourly", categorized[id(s1)])
        self.assertNotIn("weekly", categorized[id(s1)]) # Sunday

        # Check s2 (same day/month/year/quarter as s1, just later hour)
        self.assertIn("hourly", categorized[id(s2)])
        self.assertNotIn("daily", categorized[id(s2)])

        # Check s3 (Monday)
        self.assertIn("weekly", categorized[id(s3)])
        self.assertIn("daily", categorized[id(s3)])
        self.assertIn("hourly", categorized[id(s3)])

        # Check s4 (Feb 1)
        self.assertIn("monthly", categorized[id(s4)])
        self.assertNotIn("quarterly", categorized[id(s4)]) # Feb is not start of quarter

        # Check s5 (Apr 1)
        self.assertIn("quarterly", categorized[id(s5)])
        self.assertIn("monthly", categorized[id(s5)])

        self.assertIn("yearly", categorized[id(s1)])
        self.assertIn("hourly", categorized[id(s2)])
        self.assertIn("weekly", categorized[id(s3)])


class SchedulerTests(unittest.TestCase):
    """Tests for the Scheduler class."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.sched_path = self.tmp_dir.name
        self.manager = Mock()
        # We assume empty config dir initially
        self.scheduler = _manager.Scheduler(self.manager, self.sched_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch("snapm.manager._manager.Schedule")
    def test_create_schedule(self, MockSchedule):
        # Setup mock instance
        sched_inst = MockSchedule.return_value
        sched_inst.name = "sched1"

        res = self.scheduler.create(
            "sched1", ["/mnt"], "10%FREE", False, "daily", None
        )

        self.assertEqual(res, sched_inst)
        sched_inst.write_config.assert_called_with(self.sched_path)
        sched_inst.enable.assert_called()
        sched_inst.start.assert_called()

        # Check internal list update
        self.assertIn(sched_inst, self.scheduler._schedules)
        self.assertEqual(self.scheduler._get_schedule_by_name("sched1"), sched_inst)

    @patch("snapm.manager._manager.Schedule")
    def test_create_duplicate_raises(self, MockSchedule):
        sched_inst = MockSchedule.return_value
        sched_inst.name = "sched1"
        self.scheduler.create("sched1", ["/mnt"], None, False, "daily", None)

        with self.assertRaises(snapm.SnapmExistsError):
            self.scheduler.create("sched1", ["/mnt"], None, False, "daily", None)

    @patch("snapm.manager._manager.Schedule")
    def test_edit_schedule(self, MockSchedule):
        # Setup initial schedule
        old_sched = Mock()
        old_sched.name = "sched1"
        self.scheduler._schedules.append(old_sched)
        self.scheduler._schedules_by_name["sched1"] = old_sched

        # Setup new schedule mock
        new_sched = MockSchedule.return_value
        new_sched.name = "sched1"

        res = self.scheduler.edit(
            "sched1", ["/mnt/new"], "10%FREE", False, "weekly", None
        )

        # Verify old schedule cleanup
        old_sched.stop.assert_called()
        old_sched.disable.assert_called()
        self.assertNotIn(old_sched, self.scheduler._schedules)

        # Verify new schedule setup
        self.assertEqual(res, new_sched)
        new_sched.write_config.assert_called_with(self.sched_path)
        new_sched.enable.assert_called()
        new_sched.start.assert_called()
        self.assertIn(new_sched, self.scheduler._schedules)

    def test_delete_schedule(self):
        mock_sched = Mock()
        mock_sched.name = "sched1"
        self.scheduler._schedules.append(mock_sched)
        self.scheduler._schedules_by_name["sched1"] = mock_sched

        self.scheduler.delete("sched1")

        mock_sched.stop.assert_called()
        mock_sched.disable.assert_called()
        mock_sched.delete_config.assert_called()
        self.assertNotIn(mock_sched, self.scheduler._schedules)

    def test_lifecycle_proxies(self):
        """Test enable/disable/start/stop/gc pass-throughs."""
        mock_sched = Mock()
        mock_sched.name = "sched1"
        self.scheduler._schedules.append(mock_sched)
        self.scheduler._schedules_by_name["sched1"] = mock_sched

        self.scheduler.enable("sched1", start=True)
        mock_sched.enable.assert_called()
        mock_sched.start.assert_called()

        self.scheduler.stop("sched1")
        mock_sched.stop.assert_called()

        self.scheduler.disable("sched1")
        mock_sched.disable.assert_called()

        self.scheduler.gc("sched1")
        mock_sched.gc.assert_called()
