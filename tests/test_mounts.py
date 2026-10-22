# Copyright Red Hat
#
# tests/test_mounts.py - Mount support tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
from subprocess import run, CalledProcessError
from contextlib import contextmanager
from stat import S_ISBLK
from types import SimpleNamespace
from uuid import UUID, uuid5
import unittest
import unittest.mock
import logging
import tempfile
import shutil
import os.path
import pytest
import os


log = logging.getLogger()


import snapm
import snapm.manager._mounts as mounts
import snapm.manager._mountstate as mountstate
import snapm.manager
from snapm.manager.plugins import format_snapshot_name, encode_mount_point

from tests import have_root, is_redhat, in_rh_ci
from ._util import (
    LoopBackDevices,
    LvmLoopBacked,
    rmtree_one_file_system,
    _VG_NAME,
)

ETC_FSTAB = "/etc/fstab"

_root_dirs = ("boot", "dev", "etc", "mnt", "opt", "proc", "run", "sys", "usr")

@contextmanager
def mounted(what, where, fstype=None, options="defaults"):
    """Context manager for mounting/unmounting."""
    mounts._mount(what, where, fstype=fstype, options=options)
    try:
        yield where
    finally:
        if os.path.ismount(where):
            mounts._umount(where)


@unittest.skipIf(not have_root(), "requires root privileges")
class MountsTestsSimple(unittest.TestCase):
    """
    Test mount helpers
    """

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)
        self.loop_devs = LoopBackDevices()
        self.loop_devs.create_devices(1)
        self.addCleanup(self.loop_devs.destroy_all)

    def tearDown(self):
        log.debug("Tearing down %s", self._testMethodName)


    def test__mount__umount(self):
        somestr = "Blue canary in the outlet by the light switch\nWho watches over you\n"
        with tempfile.TemporaryDirectory(suffix="_test_mount") as tempdir:
            mount_point = os.path.join(tempdir, "mytmpfs")
            os.makedirs(mount_point, mode=0o700)

            with mounted("none", mount_point, fstype="tmpfs", options="size=1m,nosuid,nodev,noexec"):
                self.assertTrue(os.path.ismount(mount_point))
                with open(os.path.join(mount_point, "testfile"), "w", encoding="utf8") as fpw:
                   fpw.write(somestr)
                with open(os.path.join(mount_point, "testfile"), "r", encoding="utf8") as fpr:
                   self.assertEqual(fpr.read(), somestr)

        self.assertFalse(os.path.ismount(mount_point))

    def test__mount_bad_dev(self):
        with tempfile.TemporaryDirectory(suffix="_test_mount") as tempdir:
            mount_point = os.path.join(tempdir, "devmp")
            os.makedirs(mount_point, mode=0o700)
            with self.assertRaises(snapm.SnapmMountError):
                mounts._mount("/not/a/dev", mount_point, fstype="ext4")

    def test__mount_bad_mp(self):
        with self.assertRaises(snapm.SnapmMountError):
            mounts._mount(self.loop_devs.device_nodes()[0], "/last/train/to/trancentral")

    def test__umount_bad_mp(self):
        with self.assertRaises(snapm.SnapmUmountError):
            mounts._umount("/3/am/eternal")

    #@unittest.mock.patch("subprocess.run")
    @unittest.mock.patch("snapm.manager._mounts.run")
    def test_get_xfs_quota_options(self, mock_run):
        """
        Tests the XFS quota flag parser.
        """
        dev = "/dev/test"

        # Quota flags:
        # _XFS_UQUOTA_ACCT = 0x0001
        # _XFS_UQUOTA_ENFD = 0x0002
        # _XFS_GQUOTA_ACCT = 0x0040
        # _XFS_GQUOTA_ENFD = 0x0080
        # _XFS_PQUOTA_ACCT = 0x0008
        # _XFS_PQUOTA_ENFD = 0x0200

        # 1. Test all flags enabled
        # uquota (0x001+0x002=0x3), gquota (0x040+0x080=0xC0), pquota (0x008+0x200=0x208)
        # All on: 0x3 | 0xC0 | 0x208 = 0x2CB
        mock_run.return_value = unittest.mock.Mock(
            stdout="qflags = 0x2cb\n", check=True
        )
        self.assertEqual(mounts._get_xfs_quota_options(dev), "uquota,gquota,pquota")

        # 2. Test "noenforce" flags
        # uqnoenforce (0x1), gqnoenforce (0x40), pqnoenforce (0x8)
        mock_run.return_value.stdout = "qflags = 0x49\n"
        self.assertEqual(mounts._get_xfs_quota_options(dev), "uqnoenforce,gqnoenforce,pqnoenforce")

        # 3. Test no flags
        mock_run.return_value.stdout = "qflags = 0x0\n"
        self.assertEqual(mounts._get_xfs_quota_options(dev), "")

        # 4. Test malformed output
        mock_run.return_value.stdout = "bad output\n"
        with self.assertRaises(snapm.SnapmCalloutError):
            mounts._get_xfs_quota_options(dev)

        # 5. Test xfs_db failure
        mock_run.side_effect = CalledProcessError(1, "cmd", stderr="xfs_db failed")
        with self.assertRaises(snapm.SnapmCalloutError):
            mounts._get_xfs_quota_options(dev)

        # 6. Test FileNotFoundError
        mock_run.side_effect = FileNotFoundError(2, "No such file", "xfs_db")
        with self.assertRaises(snapm.SnapmCalloutError):
            mounts._get_xfs_quota_options(dev)

        # 7. Test malformed integer value
        mock_run.side_effect = None
        mock_run.return_value.stdout = "qflags = 0xNOT_HEX\n"
        with self.assertRaises(snapm.SnapmCalloutError):
            mounts._get_xfs_quota_options(dev)


@unittest.skipIf(not have_root(), "requires root privileges")
@unittest.skipIf(not is_redhat(), "requires Fedora-like OS")
class MountsTestsBase(unittest.TestCase):
    """
    Base class for mount tests.
    """
    volumes = ("root",)
    thin_volumes = ("opt",)
    mount_volumes = (
        ("root", "/"),
        ("opt", "/opt"),
    )
    snapset_name = "mntset0"
    snapset_time = 1707923088

    # Need executables in /?
    need_exec = False

    def _set_fstab(self):
        """
        Set up a fake /etc/fstab to be used for the duration of the test run.
        """
        self._tmp_fstab = tempfile.NamedTemporaryFile(
            prefix="fstab_", delete=False, mode="w", encoding="utf8"
        ).name
        with open(self._tmp_fstab, "w", encoding="utf8") as file:
            file.write("# Test fstab\n")
            for origin, mp in self.mount_volumes:
                file.write(f"/dev/{_VG_NAME}/{origin}\t{mp}\text4\tdefaults 0 0\n")
        run(["mount", "--bind", self._tmp_fstab, ETC_FSTAB], check=True)

    def _clear_fstab(self):
        """
        Remove the fake /etc/fstab.
        """
        try:
            run(["umount", ETC_FSTAB], check=True)
        finally:
            if hasattr(self, "_tmp_fstab") and self._tmp_fstab:
                os.unlink(self._tmp_fstab)

    def cleanup(self):
        log.debug("Clearing /etc/fstab bind mount")
        self._clear_fstab()
        log.debug("Destroying LVM context")
        self._lvm.destroy()

    def cleanup_mounts(self):
        mount_path = os.path.join(self.mounts_root_dir, self.snapset_name)

        # Try to get the mount manager to clean up first
        try:
            self.mounts.umount(self.snapset)
        except snapm.SnapmError as err:
            log.warning(
                "Mount manager cleanup failed for %s: %s",
                self.snapset_name,
                err,
            )
            if os.path.ismount(mount_path):
                try:
                    run(
                        ["umount", "-R", mount_path],
                        check=True,
                        capture_output=True,
                        encoding="utf8",
                    )
                except CalledProcessError as err:
                    pytest.exit(
                        f"Emergency shutdown: mount cleanup failed for mount path {mount_path}: "
                        f"(rc={err.returncode}: {err.stderr})"
                    )

    def _umount_mount_point(self, mount_path):
        """
        Unmount the snapshot set from ``mount_path``, if it is mounted.

        Leaving it mounted is not recoverable: the cleanups that follow
        remove the mount point and then detach the volume group out from
        under whatever is still mounted on it, so give up on the run
        instead of letting them proceed.
        """
        if not os.path.ismount(mount_path):
            return
        try:
            self.mounts.umount(self.snapset, mount_root=mount_path)
        except snapm.SnapmError as err:
            log.warning("Cleanup unmount failed for %s: %s", mount_path, err)
            try:
                run(
                    ["umount", "-R", mount_path],
                    check=True,
                    capture_output=True,
                    encoding="utf8",
                )
            except CalledProcessError as err:
                pytest.exit(
                    f"Emergency shutdown: mount cleanup failed for mount path "
                    f"{mount_path}: (rc={err.returncode}: {err.stderr})"
                )

    def make_mount_point(self, prefix="snapm_custom_root_"):
        """
        Return a temporary directory to mount a snapshot set at.

        Cleanups run in reverse order of registration, so the unmount is
        registered after the removal in order to run before it.

        :param prefix: Prefix for the temporary directory name.
        :returns: The path to the new directory.
        """
        mount_point = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(rmtree_one_file_system, mount_point)
        self.addCleanup(self._umount_mount_point, mount_point)
        return mount_point

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)

        # 1. Setup LVM
        self._lvm = LvmLoopBacked(self.volumes, thin_volumes=self.thin_volumes)

        # 2. Setup fake fstab
        self._set_fstab()

        # Push LVM and /etc/fstab cleanup
        self.addCleanup(self.cleanup) # self.cleanup already calls _clear_fstab and _lvm.destroy

        # 3. Build fake root and optionally install static busybox binaries

        # ./~ Make it cheap & keep it shallow. Fill it up and make it hollow. ./~
        with tempfile.TemporaryDirectory(prefix="snapm_root_prep_") as tempdir:
            with mounted(f"/dev/{_VG_NAME}/root", tempdir):
                for dirpath in _root_dirs:
                    os.makedirs(os.path.join(tempdir, dirpath))

                if self.need_exec:
                    # ./~ Keep it working 'til tomorrow; it might be wrong but no one will know. ./~
                    run(["dnf", "-y", "install", "--use-host-config", "--installroot", tempdir, "busybox"], check=True)
                    run([os.path.join(tempdir, "bin", "busybox"), "--install", os.path.join(tempdir, "bin")], check=True)

        # 4. Setup fake snapshot set
        snapset_name = self.snapset_name
        snapset_time = self.snapset_time
        for origin, mp in self.mount_volumes:
            self._lvm.create_snapshot(
                origin,
                format_snapshot_name(
                    origin, snapset_name, snapset_time, encode_mount_point(mp)
                ),
            )

        # 5. Setup temp directory for mounts
        #
        # Removed with rmtree_one_file_system() rather than through a
        # TemporaryDirectory: that would remove the tree from its finaliser
        # at garbage collection time even if the cleanup below declined to.
        self.mounts_root_dir = tempfile.mkdtemp(prefix="snapm_mnt_")
        self.addCleanup(rmtree_one_file_system, self.mounts_root_dir)

        # 5b. Setup temp directory for the mount provenance records
        state_dir_obj = tempfile.TemporaryDirectory(prefix="snapm_mnt_state_")
        self.addCleanup(state_dir_obj.cleanup)
        self.mount_state_dir = state_dir_obj.name

        # 5c. Setup temp directory for the mount state lock
        lock_dir_obj = tempfile.TemporaryDirectory(prefix="snapm_mnt_lock_")
        self.addCleanup(lock_dir_obj.cleanup)
        self.mount_lock_dir = lock_dir_obj.name

        # 6. Setup Manager and find the SnapshotSet
        self.manager = snapm.manager.Manager()
        self.manager.discover_snapshot_sets()
        self.snapset = self.manager.by_name[self.snapset_name]

        # 7. Initialize the Mounts object
        self.mounts = mounts.Mounts(
            self.manager,
            self.mounts_root_dir,
            self.mount_state_dir,
            self.mount_lock_dir,
        )

        # 8. Cleanup mounts - registered last so that it runs first, before
        # the mount root is removed. rmtree_one_file_system() refuses to
        # remove a tree that is still mounted, so getting this wrong now
        # fails the test rather than running rm -rf over /dev and /run.
        self.addCleanup(self.cleanup_mounts)

    def tearDown(self):
        log.debug("Tearing down %s", self._testMethodName)


@unittest.skipIf(not have_root(), "requires root privileges")
@unittest.skipIf(not is_redhat(), "requires Fedora-like OS")
class MountsTests(MountsTestsBase):
    """
    Mount tests with skeleton root file system.
    """
    def test_mount_and_umount(self):
        """
        Tests the full, successful lifecycle: mount, check, umount, check.
        """
        self.assertEqual(len(self.mounts._mounts), 0)

        # --- Mount ---
        try:
            mount_obj = self.mounts.mount(self.snapset)
        except snapm.SnapmError as e:
            self.fail(f"Mounting failed when it should have succeeded: {e}")

        self.assertIs(self.mounts._canonical_mount(self.snapset), mount_obj)
        self.assertIn(mount_obj, self.mounts._mounts)
        self.assertTrue(mount_obj.mounted)
        self.assertEqual(self.snapset.mount_root, mount_obj.root)

        # Check that root, subvolume, and API mounts exist
        self.assertTrue(os.path.ismount(mount_obj.root))
        self.assertTrue(os.path.ismount(os.path.join(mount_obj.root, "opt")))
        self.assertTrue(os.path.ismount(os.path.join(mount_obj.root, "proc")))
        self.assertTrue(os.path.ismount(os.path.join(mount_obj.root, "dev")))

        # --- Umount ---
        self.mounts.umount(self.snapset)

        self.assertIsNone(self.mounts._canonical_mount(self.snapset))
        self.assertNotIn(mount_obj, self.mounts._mounts)
        self.assertFalse(mount_obj.mounted) # Check object state
        self.assertFalse(os.path.ismount(mount_obj.root)) # Check system state
        self.assertEqual(self.snapset.mount_root, "") # Check snapset state

        # Check that the mount directory itself is removed
        self.assertFalse(os.path.exists(mount_obj.root))

    def test_mount_marks_mounts_as_snapm_managed(self):
        """
        Tests that every mount in the tree carries x-snapm.managed.
        """
        mount_obj = self.mounts.mount(self.snapset)
        self.addCleanup(self.mounts.umount, self.snapset)

        # findmnt reports the "x-" options from /run/mount/utab alongside
        # the kernel's own, so the marker makes the tree findable wherever
        # it is mounted (#1027).
        # "--mtab" is required: the "x-" options live in /run/mount/utab,
        # and findmnt only merges them into its view in that mode.
        result = run(
            ["findmnt", "--mtab", "--noheadings", "--output", "TARGET,OPTIONS",
             "--options", "x-snapm.managed"],
            capture_output=True, encoding="utf8", check=True,
        )
        marked = {}
        for line in result.stdout.splitlines():
            target, options = line.split()
            marked[target] = options

        expected = f"x-snapm.managed={self.snapset.uuid}"
        for path in (mount_obj.root,
                     os.path.join(mount_obj.root, "opt"),
                     os.path.join(mount_obj.root, "proc")):
            with self.subTest(path=path):
                self.assertIn(path, marked)
                self.assertIn(expected, marked[path].split(","))

    def test_discover_mounts(self):
        """
        Tests that a new Mounts instance can discover pre-existing mounts.
        """
        # 1. Mount a snapset
        mount_obj = self.mounts.mount(self.snapset)
        self.assertTrue(mount_obj.mounted)
        self.assertEqual(len(self.mounts._mounts), 1)

        # 2. Create a *new* Mounts instance pointing to the same directory
        new_mounts = mounts.Mounts(
            self.manager,
            self.mounts_root_dir,
            self.mount_state_dir,
            self.mount_lock_dir,
        )

        # 3. Check if it discovered the mount
        self.assertEqual(len(new_mounts._mounts), 1)
        discovered_mount = new_mounts._mounts[0]

        self.assertEqual(discovered_mount.snapset.name, self.snapset.name)
        self.assertEqual(discovered_mount.root, mount_obj.root)
        self.assertTrue(discovered_mount.mounted)

        # 4. Cleanup (using the original 'mounts' object)
        self.mounts.umount(self.snapset)

    def test_mount_rollback_on_failure(self):
        """
        Tests that a mount failure during an auxiliary mount (e.g., /opt)
        correctly rolls back the entire operation, unmounting root.
        """
        mount_path = os.path.join(self.mounts_root_dir, self.snapset.name)

        # Mock `_mount` to fail on the *second* call (the /opt mount)
        # The first call is the root mount.
        original_mount = mounts._mount

        def mock_mount_fail(what, where, *args, **kwargs):
            if where.endswith("/opt"):
                raise snapm.SnapmMountError(what, where, 1, "Mocked failure")
            return original_mount(what, where, *args, **kwargs)

        with unittest.mock.patch("snapm.manager._mounts._mount", side_effect=mock_mount_fail):
            # We expect the Mount.mount() call to fail and raise the error
            with self.assertRaises(snapm.SnapmMountError):
                self.mounts.mount(self.snapset)

        # Check that the rollback logic worked
        self.assertFalse(os.path.ismount(mount_path))
        self.assertFalse(os.path.exists(mount_path)) # Mounts.mount should rmdir
        self.assertEqual(len(self.mounts._mounts), 0)

    def test_mount_skips_nonexistent_mountpoint_dir(self):
        """
        Tests that a missing mount point directory inside the snapshot
        (e.g., /opt is in fstab but missing in the snapshot)
        is skipped with a warning, rather than failing the whole mount.
        """
        # Mock `isdir` to report that the '/opt' directory does not exist
        # inside the snapshot root.
        original_isdir = os.path.isdir
        def mock_isdir(path):
            if path == os.path.join(self.mounts_root_dir, self.snapset.name, "opt"):
                return False
            return original_isdir(path)

        with unittest.mock.patch("os.path.isdir", side_effect=mock_isdir):
            # Expect a warning to be logged
            with self.assertLogs(mounts._log, level='WARNING') as cm:
            #with self.assertLogs(logger=None, level='DEBUG') as cm:
                mount_obj = self.mounts.mount(self.snapset)

                log_output = "\n".join(cm.output)
                self.assertIn("Mount point", log_output)
                self.assertIn("does not exist in snapshot set", log_output)
                self.assertIn(self.snapset.name, log_output)

        # The root mount and API mounts should still be present
        self.assertTrue(mount_obj.mounted)
        self.assertTrue(os.path.ismount(mount_obj.root))
        self.assertTrue(os.path.ismount(os.path.join(mount_obj.root, "proc")))

        # The /opt mount should *not* exist
        self.assertFalse(os.path.ismount(os.path.join(mount_obj.root, "opt")))

        self.mounts.umount(self.snapset)

    def test_mount_already_mounted(self):
        """
        Tests that calling mount() on an already-mounted snapset
        logs a warning and returns the existing object.
        """
        mount_obj_1 = self.mounts.mount(self.snapset)

        with self.assertLogs(mounts._log, level='INFO') as cm:
            mount_obj_2 = self.mounts.mount(self.snapset)

            log_output = "\n".join(cm.output)
            self.assertIn("already mounted", log_output)

        self.assertIs(mount_obj_1, mount_obj_2) # Should be the same object
        self.assertEqual(len(self.mounts._mounts), 1)

        self.mounts.umount(self.snapset)

    def test_umount_not_mounted(self):
        """
        Tests that calling umount() on a snapset that is not managed
        by this Mounts instance raises SnapmNotFoundError.
        """
        # Note: self.snapset exists, but it has no mount in self.mounts
        self.assertEqual(len(self.mounts._mounts), 0)
        with self.assertRaises(snapm.SnapmNotFoundError):
            self.mounts.umount(self.snapset)

    def test_discover_invalid_mount_path(self):
        """
        Tests that discovery ignores a directory that matches a snapset
        name but is not actually a mount point.
        """
        # 1. Create a directory named after the snapset, but don't mount it
        dummy_path = os.path.join(self.mounts_root_dir, self.snapset_name)
        os.makedirs(dummy_path)
        self.addCleanup(lambda: os.path.isdir(dummy_path) and os.rmdir(dummy_path))

        # 2. Run discovery. Mounts are found from the provenance records
        #    snapm writes as it makes them, so a directory that merely carries
        #    the snapshot set name is never mistaken for a mount.
        new_mounts = mounts.Mounts(
            self.manager,
            self.mounts_root_dir,
            self.mount_state_dir,
            self.mount_lock_dir,
        )

        # 3. Check that no mount was discovered
        self.assertEqual(len(new_mounts._mounts), 0)
        self.assertEqual(self.snapset.mount_root, "")

        os.rmdir(dummy_path)

    def test_find_mounts(self):
        """
        Tests finding mounts by selection.
        """
        # 1. No mounts
        self.assertEqual(self.mounts.find_mounts(selection=snapm.Selection(name=self.snapset_name)), [])

        # 2. Mount and find
        mount_obj = self.mounts.mount(self.snapset)

        # Find by name
        found = self.mounts.find_mounts(selection=snapm.Selection(name=self.snapset_name))
        self.assertEqual(found, [mount_obj])

        # Find all
        found = self.mounts.find_mounts(selection=snapm.Selection())
        self.assertEqual(found, [mount_obj])

        # Find and miss
        found = self.mounts.find_mounts(selection=snapm.Selection(name="no-such-snapset"))
        self.assertEqual(found, [])

    def _sets_named(self, name):
        """
        Return the list of snapshot sets known to the manager with the name
        ``name``. A refused split must not leave a second set behind in the
        manager's snapshot set list.
        """
        return [s for s in self.manager.snapshot_sets if s.name == name]

    def test_delete_mounted_snapset_raises(self):
        """
        Tests that Manager.delete_snapshot_set() refuses a mounted snapshot
        set and leaves the manager's lookup tables and the mount tree alone.
        """
        mount_obj = self.mounts.mount(self.snapset)
        snapset_uuid = self.snapset.uuid

        # The delete is refused by the manager, before any boot entry or
        # lookup table teardown takes place.
        with self.assertLogs("snapm.manager._manager", level="ERROR") as cm:
            with self.assertRaises(snapm.SnapmBusyError):
                self.manager.delete_snapshot_set(name=self.snapset_name)

        self.assertIn("Cannot delete mounted snapshot set", "\n".join(cm.output))
        self.assertNotIn("Failed to delete snapshot set", "\n".join(cm.output))

        # The snapshot set is still known by its original name and UUID
        self.assertEqual(self.snapset.name, self.snapset_name)
        self.assertIs(self.manager.by_name[self.snapset_name], self.snapset)
        self.assertIs(self.manager.by_uuid[snapset_uuid], self.snapset)
        self.assertIn("mntset0", self.manager.by_name)
        self.assertEqual(self.manager.snapshot_sets.count(self.snapset), 1)

        # The mount tree is untouched
        self.assertTrue(mount_obj.mounted)
        self.assertTrue(os.path.ismount(mount_obj.root))
        self.assertEqual(self.snapset.mount_root, mount_obj.root)

        self.mounts.umount(self.snapset)

    def test_snapset_delete_mounted_raises(self):
        """
        Tests that the SnapshotSet.delete() guard refuses a mounted snapshot
        set independently of the Manager level check.
        """
        self.mounts.mount(self.snapset)

        with self.assertRaises(snapm.SnapmBusyError):
            self.snapset.delete()

        # No member was deleted
        for snapshot in self.snapset.snapshots:
            self.assertIn(self.snapset_name, snapshot.name)

        self.mounts.umount(self.snapset)

    def test_rename_mounted_snapset_raises(self):
        """
        Tests that Manager.rename_snapshot_set() refuses a mounted snapshot
        set and leaves the manager's lookup tables and the mount tree alone.
        """
        mount_obj = self.mounts.mount(self.snapset)
        snapset_uuid = self.snapset.uuid

        # The rename is refused by the manager, before any boot entry or
        # lookup table teardown takes place.
        with self.assertLogs("snapm.manager._manager", level="ERROR") as cm:
            with self.assertRaises(snapm.SnapmBusyError):
                self.manager.rename_snapshot_set(self.snapset_name, "mntset1")

        self.assertIn("Cannot rename mounted snapshot set", "\n".join(cm.output))
        self.assertNotIn("Failed to rename snapshot set", "\n".join(cm.output))

        # The snapshot set is still known by its original name and UUID
        self.assertEqual(self.snapset.name, self.snapset_name)
        self.assertIs(self.manager.by_name[self.snapset_name], self.snapset)
        self.assertIs(self.manager.by_uuid[snapset_uuid], self.snapset)
        self.assertNotIn("mntset1", self.manager.by_name)
        self.assertEqual(self.manager.snapshot_sets.count(self.snapset), 1)

        # The mount tree is untouched
        self.assertTrue(mount_obj.mounted)
        self.assertTrue(os.path.ismount(mount_obj.root))
        self.assertEqual(self.snapset.mount_root, mount_obj.root)

        self.mounts.umount(self.snapset)

    def test_snapset_rename_mounted_raises(self):
        """
        Tests that the SnapshotSet.rename() guard refuses a mounted snapshot
        set independently of the Manager level check.
        """
        self.mounts.mount(self.snapset)

        with self.assertRaises(snapm.SnapmBusyError):
            self.snapset.rename("mntset1")

        # No member was renamed
        self.assertEqual(self.snapset.name, self.snapset_name)
        for snapshot in self.snapset.snapshots:
            self.assertIn(self.snapset_name, snapshot.name)

        self.mounts.umount(self.snapset)

    def test_split_mounted_snapset_raises(self):
        """
        Tests that Manager.split_snapshot_set() refuses a mounted snapshot
        set without disturbing the set's membership.
        """
        mount_obj = self.mounts.mount(self.snapset)
        sources = self.snapset.sources

        # Splitting "/opt" leaves "/" in the original set, so the split is
        # otherwise legal: assert on the refusal logged by the mount check
        # rather than on the exception type alone, which the empty set rule
        # would raise too if the set were ever reduced to a single source.
        self.assertGreater(len(sources), 1)

        with self.assertLogs("snapm.manager._manager", level="ERROR") as cm:
            with self.assertRaises(snapm.SnapmBusyError):
                self.manager.split_snapshot_set(self.snapset_name, "mntset1", ["/opt"])

        self.assertIn("Cannot split mounted snapshot set", "\n".join(cm.output))

        self.assertIs(self.manager.by_name[self.snapset_name], self.snapset)
        self.assertNotIn("mntset1", self.manager.by_name)
        self.assertEqual(self._sets_named(self.snapset_name), [self.snapset])
        self.assertEqual(self.snapset.sources, sources)
        self.assertTrue(mount_obj.mounted)

        self.mounts.umount(self.snapset)

    def test_prune_mounted_snapset_raises(self):
        """
        Tests that pruning a mounted snapshot set is refused: the pruned
        members must not be deleted out from under the mount tree.
        """
        mount_obj = self.mounts.mount(self.snapset)
        sources = self.snapset.sources

        # As for the split above: pruning "/opt" leaves "/" behind, so the
        # prune is refused for being mounted and nothing else.
        self.assertGreater(len(sources), 1)

        with self.assertLogs("snapm.manager._manager", level="ERROR") as cm:
            with self.assertRaises(snapm.SnapmBusyError):
                self.manager.split_snapshot_set(self.snapset_name, None, ["/opt"])

        self.assertIn("Cannot prune mounted snapshot set", "\n".join(cm.output))

        self.assertIs(self.manager.by_name[self.snapset_name], self.snapset)
        self.assertEqual(self._sets_named(self.snapset_name), [self.snapset])
        self.assertEqual(self.snapset.sources, sources)
        self.assertTrue(mount_obj.mounted)

        self.mounts.umount(self.snapset)

    def test_exec_on_unmounted(self):
        """
        Tests that exec fails on an unmounted snapset.
        """
        # 1. Create mount path dir, but don't mount
        mount_path = os.path.join(self.mounts_root_dir, self.snapset.name)
        os.makedirs(mount_path, exist_ok=True)

        # 2. Create Mount object directly (bypassing Mounts.mount)
        mount_obj = mounts.Mount(self.snapset, mount_path)
        self.assertFalse(mount_obj.mounted)

        # 3. Assert it raises the correct error
        with self.assertRaises(snapm.SnapmPathError):
            mount_obj.exec("ls")

    def test_exec_malformed_command(self):
        """
        Tests that exec fails on a malformed command string.
        """
        mount_obj = self.mounts.mount(self.snapset)

        # 2. Pass an unclosed quote
        with self.assertRaises(snapm.SnapmArgumentError):
            mount_obj.exec("ls 'unterminated-quote")

    def test_discover_skips_non_snapset_dir(self):
        """
        Tests that discovery skips directories that don't match a snapset.
        """
        # 1. Mount the snapshot set so that discovery has something to find
        mount_obj = self.mounts.mount(self.snapset)

        # 2. Create a directory that isn't a known snapset alongside it
        dummy_path = os.path.join(self.mounts_root_dir, "not-a-real-snapset")
        os.makedirs(dummy_path)
        self.addCleanup(lambda: os.path.isdir(dummy_path) and os.rmdir(dummy_path))

        # 3. Run discovery
        new_mounts = mounts.Mounts(
            self.manager,
            self.mounts_root_dir,
            self.mount_state_dir,
            self.mount_lock_dir,
        )

        # 4. Only the real snapshot set mount is discovered
        self.assertEqual(len(new_mounts._mounts), 1)
        self.assertEqual(new_mounts._mounts[0].root, mount_obj.root)

        os.rmdir(dummy_path)

    def test_discover_incomplete_submounts(self):
        """
        Tests that discovery logs warnings for incomplete mounts.
        """
        # 1. Mount a snapset fully
        _ = self.mounts.mount(self.snapset)

        # 2. Unmount /opt within the mount
        mounts._umount(os.path.join(self.mounts_root_dir, self.snapset.name, "opt"))

        # 3. Re-run discovery with the missing submount
        with self.assertLogs(mounts._log, level='WARNING') as cm:
            new_mounts = mounts.Mounts(
                self.manager,
                self.mounts_root_dir,
                self.mount_state_dir,
                self.mount_lock_dir,
            )

            log_output = "\n".join(cm.output)
            self.assertIn("Missing snapshot set submounts", log_output)
            self.assertIn("/opt", log_output)
            self.assertNotIn("Missing API file system submounts", log_output)

        # 4. Check that the mount was still discovered (it's just a warning)
        self.assertEqual(len(new_mounts._mounts), 1)

    @unittest.skipIf(in_rh_ci(), "Tests running in RH CI pipeline")
    def test_discover_incomplete_api_mounts(self):
        """
        Tests that discovery logs warnings for missing API mounts.
        """
        # 1. Mount a snapset fully
        _ = self.mounts.mount(self.snapset)

        # 2. Recursively unmount /proc within the mount
        proc_mount_path = os.path.join(self.mounts_root_dir, self.snapset.name, "proc")
        run(["umount", "-R", proc_mount_path], check=True)

        # 3. Re-run discovery with the mock
        with self.assertLogs(mounts._log, level='WARNING') as cm:
            _ = mounts.Mounts(
                self.manager,
                self.mounts_root_dir,
                self.mount_state_dir,
                self.mount_lock_dir,
            )

            log_output = "\n".join(cm.output)
            self.assertNotIn("Missing snapshot set submounts", log_output)
            self.assertIn("Missing API file system submounts", log_output)
            self.assertIn("/proc", log_output)

    def test_mount_with_custom_mount_root(self):
        """
        Tests that a custom mount_root places the mount tree at that path
        instead of beneath the default mounts directory.
        """
        custom_root = self.make_mount_point()

        mount_obj = self.mounts.mount(self.snapset, mount_root=custom_root)

        self.assertEqual(mount_obj.root, custom_root)
        self.assertTrue(mount_obj.mounted)
        self.assertTrue(os.path.ismount(custom_root))

        self.mounts.umount(self.snapset)

        # The mount point was not snapm's to create, so it is still here.
        self.assertFalse(os.path.ismount(custom_root))
        self.assertTrue(os.path.isdir(custom_root))

    def test_mount_with_custom_mount_root_nonexistent(self):
        """
        Tests that a non-existent mount_root directory raises SnapmPathError.
        """
        custom_root = self.make_mount_point()

        new_root = os.path.join(custom_root, "nested", "mount_root")
        self.assertFalse(os.path.exists(new_root))

        with self.assertRaisesRegex(snapm.SnapmPathError, "does not exist"):
            self.mounts.mount(self.snapset, mount_root=new_root)

        # snapm does not create a mount point it was handed.
        self.assertFalse(os.path.exists(new_root))

    def test_mount_with_mount_root_not_a_dir(self):
        """
        Tests that a mount_root that exists as a regular file raises SnapmPathError.
        """
        with tempfile.NamedTemporaryFile(prefix="snapm_not_a_dir_") as tmp:
            with self.assertRaisesRegex(
                snapm.SnapmPathError, "not a directory"
            ):
                self.mounts.mount(self.snapset, mount_root=tmp.name)

    def test_discover_mounts_with_custom_root(self):
        """
        Tests that discover_mounts() finds mounts created with custom mount_root
        by checking each snapset's mount_root attribute.
        """
        custom_path = self.make_mount_point()

        # Mount at the custom mount point
        mount_obj = self.mounts.mount(self.snapset, mount_root=custom_path)
        self.assertEqual(mount_obj.root, custom_path)
        self.assertTrue(mount_obj.mounted)

        # Verify snapset.mount_root is set
        self.assertEqual(self.snapset.mount_root, custom_path)

        # Clear the in-process hint so that rediscovery has to find the mount
        # from its provenance record, as it would in a freshly started
        # process.
        self.snapset.mount_root = ""

        # Re-discover mounts (simulating a fresh Manager initialization)
        self.mounts.discover_mounts()

        # Verify the custom mount was rediscovered
        rediscovered = self.mounts._canonical_mount(self.snapset)
        self.assertIsNotNone(rediscovered)
        self.assertEqual(rediscovered.root, custom_path)
        self.assertTrue(rediscovered.mounted)

        # Discovery repopulates mount_root for report output
        self.assertEqual(self.snapset.mount_root, custom_path)

        self.mounts.umount(self.snapset)

    def test_discover_mounts_clears_stale_mount_root(self):
        """
        Tests that discover_mounts() clears stale mount_root values when
        the mount no longer exists.
        """
        # Set a fake mount_root that doesn't exist
        fake_path = "/nonexistent/path/to/mount"
        self.snapset.mount_root = fake_path

        # Discover mounts should clear the stale mount_root
        self.mounts.discover_mounts()

        # Verify mount_root was cleared
        self.assertEqual(self.snapset.mount_root, "")

    def test_get_sys_mount(self):
        """
        Tests that ``Mounts.get_sys_mount()`` returns a ``SysMount`` object
        with the correct property values.
        """
        sm = self.mounts.get_sys_mount()
        self.assertIsInstance(sm, mounts.SysMount)
        self.assertEqual(sm.root, "/")
        self.assertEqual(sm.snapset, None)


@unittest.skipIf(not have_root(), "requires root privileges")
@unittest.skipIf(not is_redhat(), "requires Fedora-like OS")
class MountsTestsExec(MountsTestsBase):
    """
    Mount tests with executables in root file system.
    """
    need_exec = True

    @unittest.skipIf(in_rh_ci(), "Tests running in RH CI pipeline")
    def test_mount_exec(self):
        """
        Tests running a command chrooted inside a mounted snapshot set.
        """
        mount_obj = self.mounts.mount(self.snapset)

        # Create a test file inside the mounted snapshot's root
        test_file_path = os.path.join(mount_obj.root, "snapm_exec_test.txt")
        with open(test_file_path, "w", encoding="utf8") as f:
            f.write("Hello? Hello? Hello? Is there anybody in there?")

        # 1. Test successful command that sees the file
        #    (Note: `ls /...` because of the chroot)
        retcode = mount_obj.exec(["ls", "/snapm_exec_test.txt"])
        self.assertEqual(retcode, 0)

        # 2. Test a failing command
        retcode = mount_obj.exec("/bin/false")
        self.assertNotEqual(retcode, 0)

        # 3. Test a non-existent command
        retcode = mount_obj.exec("/bin/just-nod-if-you-can-hear-me")
        self.assertEqual(retcode, 127) # 127 is standard for "command not found"

        # 4. Test with a string command
        retcode = mount_obj.exec("cat /snapm_exec_test.txt")
        self.assertEqual(retcode, 0)

        self.mounts.umount(self.snapset)


class RmtreeOneFileSystemTests(unittest.TestCase):
    """
    Tests for the mount-aware directory removal used by the mount tests.
    """

    def _tree(self):
        """Build a small directory tree and return its path."""
        top = tempfile.mkdtemp(prefix="snapm_rmtree_")
        self.addCleanup(shutil.rmtree, top, True)
        os.makedirs(os.path.join(top, "sub", "deeper"))
        with open(os.path.join(top, "sub", "file"), "w", encoding="utf8"):
            pass
        return top

    def test_rmtree_removes_an_unmounted_tree(self):
        """Test that a tree with nothing mounted in it is removed."""
        top = self._tree()
        rmtree_one_file_system(top)
        self.assertFalse(os.path.exists(top))

    def test_rmtree_ignores_a_missing_path(self):
        """Test that removing a path that does not exist is not an error."""
        rmtree_one_file_system(os.path.join(self._tree(), "no", "such", "path"))

    def test_rmtree_refuses_to_cross_a_mount_point(self):
        """Test that a tree with something mounted in it is left alone."""
        top = self._tree()
        mounted_dir = os.path.join(top, "sub", "deeper")

        # Stand in for a mount point: the test suite cannot mount anything
        # without root, and st_dev is identical throughout a temporary tree.
        real_ismount = os.path.ismount

        def fake_ismount(path):
            return path == mounted_dir or real_ismount(path)

        with unittest.mock.patch("os.path.ismount", side_effect=fake_ismount):
            with self.assertRaisesRegex(AssertionError, "still mounted"):
                rmtree_one_file_system(top)

        # Nothing was removed, including the parts above the mount point.
        self.assertTrue(os.path.exists(mounted_dir))
        self.assertTrue(os.path.exists(os.path.join(top, "sub", "file")))


class SysMountTests(unittest.TestCase):
    """
    Tests for the SysMount class.
    """
    def test_init_and_properties(self):
        """Test initialization and property values."""
        sm = mounts.SysMount()
        self.assertEqual(sm.root, "/")
        self.assertEqual(sm.name, "System Root")
        self.assertTrue(sm.mounted)
        # _chroot should return empty list for SysMount
        self.assertEqual(sm._chroot(), [])

    def test_mount_lifecycle_restrictions(self):
        """Test that SysMount restricts mount/umount operations."""
        sm = mounts.SysMount()

        # 1. mount() should be a no-op (log only) because it's already mounted
        with self.assertLogs(mounts._log, level='INFO') as cm:
            sm.mount()
            log_output = "\n".join(cm.output)
            self.assertIn("already mounted", log_output)

        # 2. _do_mount() should strictly raise if called directly
        with self.assertRaises(snapm.SnapmPathError):
            sm._do_mount()

        # 3. umount() should raise because SysMount refuses to unmount root
        with self.assertRaises(snapm.SnapmPathError):
            sm.umount()

    @unittest.mock.patch("snapm.manager._mounts.run")
    def test_exec(self, mock_run):
        """
        Test command execution within SysMount.

        Verifies that commands are executed directly on the host WITHOUT chroot,
        as SysMount._chroot() returns an empty list.
        """
        sm = mounts.SysMount()
        mock_run.return_value.returncode = 0

        # Test string command
        sm.exec("ls -l /boot")
        # Expect NO chroot prefix: ["ls", "-l", "/boot"]
        mock_run.assert_called_with(["ls", "-l", "/boot"], check=False)

        # Test list command
        sm.exec(["echo", "hello"])
        # Expect NO chroot prefix: ["echo", "hello"]
        mock_run.assert_called_with(["echo", "hello"], check=False)

    @unittest.mock.patch("snapm.manager._mounts.ProcMountsReader")
    def test_missing_api_mounts_warning(self, mock_pmr_cls):
        """Test that SysMount warns if critical API filesystems are missing."""
        mock_pmr = mock_pmr_cls.return_value
        # Return empty submounts -> simulates missing /proc, /dev, etc.
        mock_pmr.submounts.return_value = []

        with self.assertLogs(mounts._log, level='WARNING') as cm:
            mounts.SysMount()
            log_out = "\n".join(cm.output)
            self.assertIn("Missing API file system submounts", log_out)
            self.assertIn("Missing: /proc", log_out)


class MountBaseTests(unittest.TestCase):
    """
    Tests for the abstract MountBase class logic.
    """
    def test_init_validation(self):
        """Test that MountBase validates the mount root directory."""
        with tempfile.TemporaryDirectory() as tempdir:
            not_a_dir = os.path.join(tempdir, "file")
            with open(not_a_dir, "w") as f:
                f.write("data")

            # Define a concrete stub
            class StubMount(mounts.MountBase):
                def _do_mount(self): pass
                def _do_umount(self): pass
                def _chroot(self): return []

            # 1. Test invalid directory
            with self.assertRaisesRegex(snapm.SnapmPathError, "is not a directory"):
                StubMount(not_a_dir, "Stub")

            # 2. Test valid directory
            m = StubMount(tempdir, "Stub")
            self.assertEqual(m.root, tempdir)
            self.assertEqual(m.name, "Stub")

    @unittest.mock.patch("snapm.manager._mounts.run")
    def test_exec_uses_chroot_hook(self, mock_run):
        """
        Test that exec correctly utilizes the _chroot hook.
        This verifies the fix in MountBase.exec.
        """
        mock_run.return_value.returncode = 0

        with tempfile.TemporaryDirectory() as tempdir:
            # Concrete stub that returns a specific chroot command
            class ChrootMount(mounts.MountBase):
                def _do_mount(self): pass
                def _do_umount(self): pass
                def _chroot(self): return ["fake-chroot", self.root]
                @property
                def mounted(self): return True

            m = ChrootMount(tempdir, "ChrootStub")

            # Execute a command
            m.exec(["ls", "-la"])

            # Verify the final command is [chroot_cmd + user_cmd]
            mock_run.assert_called_with(["fake-chroot", tempdir, "ls", "-la"], check=False)

    @unittest.mock.patch("snapm.manager._mounts.run")
    def test_exec_argument_parsing(self, mock_run):
        """Test command parsing logic in MountBase.exec."""
        mock_run.return_value.returncode = 0

        with tempfile.TemporaryDirectory() as tempdir:
            # Concrete stub that claims to be mounted
            class StubMount(mounts.MountBase):
                is_mounted = True
                def _do_mount(self): pass
                def _do_umount(self): pass
                def _chroot(self): return []
                @property
                def mounted(self): return self.is_mounted

            m = StubMount(tempdir, "Stub")

            # Test shlex parsing failure
            with self.assertRaises(snapm.SnapmArgumentError):
                m.exec("ls 'unclosed string")

            # Test execution when not mounted
            m.is_mounted = False
            with self.assertRaisesRegex(snapm.SnapmPathError, "is not mounted"):
                m.exec("ls")


def _alias_block_devices(count):
    """Return up to ``count`` (canonical path, alias path) block device pairs.

    The ``/dev/block/MAJ:MIN`` links give every block device a second name
    that differs textually from its canonical path, which is the same
    situation as an LVM volume appearing as ``/dev/VG/LV`` in one place and
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
            if not S_ISBLK(os.stat(canonical).st_mode):
                continue
        except OSError:
            continue
        devices.append((canonical, alias_path))
        if len(devices) == count:
            break
    return devices


def _fake_snapset(members, name="testset0"):
    """Build a minimal snapshot set stand-in from (mount_point, devpath)."""
    snapshots = [
        SimpleNamespace(
            mount_point=mount_point, devpath=devpath, name=f"{name}-{mount_point}"
        )
        for mount_point, devpath in members
    ]
    return SimpleNamespace(
        name=name,
        uuid=uuid5(snapm.NAMESPACE_SNAPSHOT_SET, name),
        mount_root="",
        snapshots=snapshots,
        mount_points=[snapshot.mount_point for snapshot in snapshots],
        snapshot_by_source=lambda mount_point: next(
            snapshot for snapshot in snapshots if snapshot.mount_point == mount_point
        ),
    )


def _mountinfo_lines(mounts_lines, anon=()):
    """Translate '/proc/mounts' style lines into mountinfo lines.

    Mount table lookups match on device number, so each line is given the
    device number of the device it names: a device that does not exist gets
    the zero device number, which no block device has.

    A mount point named in ``anon`` is given an anonymous device number
    instead, with a major of zero, as the kernel does for a file system
    whose superblock is not that of a single block device (btrfs, overlay).
    """
    lines = []
    for mount_id, line in enumerate(mounts_lines, start=36):
        what, where, fstype = line.split()[:3]
        if where in anon:
            major, minor = (0, mount_id)
        else:
            try:
                st_rdev = os.stat(what).st_rdev
            except OSError:
                st_rdev = 0
            major, minor = (os.major(st_rdev), os.minor(st_rdev))
        lines.append(
            f"{mount_id} 1 {major}:{minor} / {where} rw - {fstype} {what} rw"
        )
    return lines


@contextmanager
def _mounts_file(lines):
    """Yield the path to a synthetic mounts file containing ``lines``."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".mounts") as f:
        for line in lines:
            f.write(line + "\n")
        path = f.name
    try:
        yield path
    finally:
        os.unlink(path)


class ProcMountsReaderTests(unittest.TestCase):
    """
    Tests for ProcMountsReader class.
    """

    def test_submounts_with_escaped_paths(self):
        """Test that ProcMountsReader correctly handles octal escape sequences.
        
        /proc/mounts uses octal escape sequences for special characters:
        - \\134 represents a backslash (octal 92)
        - \\134x2d represents the escape sequence \\x2d (hyphen, hex 2d = decimal 45)
        
        This is common with systemd-cryptsetup credential mounts on LUKS-encrypted
        systems, where the service name contains hyphens in the LUKS UUID:
        systemd-cryptsetup@luks-XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX.service
        """
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.mounts') as f:
            # Write a mock /proc/mounts with escaped paths
            # This simulates what you see on LUKS-encrypted systems
            f.write("tmpfs /run/credentials/systemd-journald.service tmpfs ro,nosuid 0 0\n")
            f.write("tmpfs /run/credentials/systemd-cryptsetup@luks\\134x2dc0d4bcd3\\134x2d54d6\\134x2d4b7e\\134x2db656\\134x2d77ddcbbc8416.service tmpfs ro,nosuid 0 0\n")
            f.write("proc /run/snapm/mounts/test/proc proc rw,nosuid 0 0\n")
            f.write("tmpfs /run/snapm/mounts/test/run/credentials/systemd-cryptsetup@luks\\134x2dc0d4bcd3\\134x2d54d6\\134x2d4b7e\\134x2db656\\134x2d77ddcbbc8416.service tmpfs ro,nosuid 0 0\n")
            mock_mounts_file = f.name

        try:
            reader = mounts.ProcMountsReader(path=mock_mounts_file)
            submounts_list = list(reader.submounts("/run/snapm/mounts/test"))
            
            # Should find 2 submounts under /run/snapm/mounts/test
            self.assertEqual(len(submounts_list), 2)
            
            # Check the proc mount (no escapes)
            proc_mount = submounts_list[0]
            self.assertEqual(proc_mount.where, "/run/snapm/mounts/test/proc")
            self.assertEqual(proc_mount.fstype, "proc")
            
            # Check the systemd-cryptsetup credential mount (has escapes)
            credential_mount = submounts_list[1]

            # The path should have literal \x2d (not hyphens, not \134x2d)
            # systemd creates directories with literal escape sequence characters
            expected_path = r"/run/snapm/mounts/test/run/credentials/systemd-cryptsetup@luks\x2dc0d4bcd3\x2d54d6\x2d4b7e\x2db656\x2d77ddcbbc8416.service"
            self.assertEqual(credential_mount.where, expected_path)

            # Verify the octal escapes are gone (no \134)
            self.assertNotIn(r'\134', credential_mount.where)

            # Verify it contains the literal \x2d characters (backslash + x + 2 + d)
            self.assertIn(r'\x2d', credential_mount.where)
            
        finally:
            os.unlink(mock_mounts_file)

    def test_submounts_with_other_escape_sequences(self):
        """Test handling of the full set of escape sequences in /proc/mounts.

        The kernel escapes exactly the characters in " \\t\\n\\\\#" (see
        mangle() in fs/proc_namespace.c). Any other octal sequence is a
        literal part of the path name and must be left alone.
        """
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.mounts') as f:
            # Test various escape sequences
            f.write("tmpfs /run/test/path\\040with\\040spaces tmpfs rw 0 0\n")  # \040 = space
            f.write("tmpfs /run/test/path\\011with\\011tabs tmpfs rw 0 0\n")  # \011 = tab
            f.write("tmpfs /run/test/path\\043with\\043hashes tmpfs rw 0 0\n")  # \043 = #
            f.write("tmpfs /run/test/path\\057not\\057escaped tmpfs rw 0 0\n")  # \057 not escaped
            f.write("tmpfs /run/test/normal-path tmpfs rw 0 0\n")
            mock_mounts_file = f.name

        try:
            reader = mounts.ProcMountsReader(path=mock_mounts_file)
            submounts_list = list(reader.submounts("/run/test"))

            self.assertEqual(len(submounts_list), 5)

            # Check space escape (\040 = octal for space)
            self.assertEqual(submounts_list[0].where, "/run/test/path with spaces")

            # Check tab escape (\011 = octal for tab)
            self.assertEqual(submounts_list[1].where, "/run/test/path\twith\ttabs")

            # Check hash escape (\043 = octal for '#')
            self.assertEqual(submounts_list[2].where, "/run/test/path#with#hashes")

            # \057 ('/') is not in the kernel's escape set: a path name may
            # legitimately contain that text, so it must survive verbatim.
            self.assertEqual(submounts_list[3].where, r"/run/test/path\057not\057escaped")

            # Check normal path (no escapes)
            self.assertEqual(submounts_list[4].where, "/run/test/normal-path")

        finally:
            os.unlink(mock_mounts_file)

    def test_submounts_with_non_latin1_paths(self):
        """Test that mount paths outside the Latin-1 range are preserved.

        The kernel writes UTF-8 path names through unmodified, so decoding
        must not assume the field is representable in Latin-1.
        """
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf8', delete=False, suffix='.mounts'
        ) as f:
            f.write("tmpfs /run/test/日本 tmpfs rw 0 0\n")  # CJK
            f.write("tmpfs /run/test/данные tmpfs rw 0 0\n")  # Cyrillic
            f.write("tmpfs /run/test/café tmpfs rw 0 0\n")  # Latin-1 range
            f.write("tmpfs /run/test/\U0001F4BE tmpfs rw 0 0\n")  # non-BMP
            # A non-Latin-1 path that also carries a real escape sequence.
            f.write("tmpfs /run/test/日本\\040data tmpfs rw 0 0\n")
            mock_mounts_file = f.name

        try:
            reader = mounts.ProcMountsReader(path=mock_mounts_file)
            submounts_list = list(reader.submounts("/run/test"))

            self.assertEqual(len(submounts_list), 5)
            self.assertEqual(submounts_list[0].where, "/run/test/日本")
            self.assertEqual(submounts_list[1].where, "/run/test/данные")
            self.assertEqual(submounts_list[2].where, "/run/test/café")
            self.assertEqual(submounts_list[3].where, "/run/test/\U0001F4BE")
            self.assertEqual(submounts_list[4].where, "/run/test/日本 data")

        finally:
            os.unlink(mock_mounts_file)

    def test_submounts_unescapes_device_field(self):
        """Test that ProcMountsReader unescapes the device (what) field.

        The kernel escapes special characters in the device field too, not just
        the mount point. A bind mount of a file whose path contains a space is a
        common way to hit this.
        """
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.mounts') as f:
            # Device path contains an escaped space (\040)
            f.write("/mnt/backing\\040file /run/test/loop ext4 rw 0 0\n")
            mock_mounts_file = f.name

        try:
            reader = mounts.ProcMountsReader(path=mock_mounts_file)
            submounts_list = list(reader.submounts("/run/test"))

            self.assertEqual(len(submounts_list), 1)

            # The device field should be unescaped just like the mount point
            self.assertEqual(submounts_list[0].what, "/mnt/backing file")
            self.assertEqual(submounts_list[0].where, "/run/test/loop")

        finally:
            os.unlink(mock_mounts_file)

    def test_entries_skips_malformed_lines(self):
        """Test that entries ignores lines without exactly six fields."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.mounts') as f:
            f.write("tmpfs /run/test/one tmpfs rw 0 0\n")
            f.write("\n")
            f.write("tmpfs /run/test/truncated tmpfs rw\n")
            f.write("tmpfs /run/test/two tmpfs rw 0 0\n")
            mock_mounts_file = f.name

        try:
            reader = mounts.ProcMountsReader(path=mock_mounts_file)
            entries = list(reader.entries)

            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0].where, "/run/test/one")
            self.assertEqual(entries[1].where, "/run/test/two")

        finally:
            os.unlink(mock_mounts_file)

    def test_lookup_by_field(self):
        """Test that lookup returns all entries matching a field value."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.mounts') as f:
            f.write("/dev/vda1 /boot ext4 rw 0 0\n")
            f.write("tmpfs /run tmpfs rw 0 0\n")
            f.write("tmpfs /dev/shm tmpfs rw 0 0\n")
            mock_mounts_file = f.name

        try:
            reader = mounts.ProcMountsReader(path=mock_mounts_file)

            by_where = list(reader.lookup("where", "/boot"))
            self.assertEqual(len(by_where), 1)
            self.assertEqual(by_where[0].what, "/dev/vda1")

            by_what = list(reader.lookup("what", "tmpfs"))
            self.assertEqual(len(by_what), 2)
            self.assertEqual(
                [entry.where for entry in by_what], ["/run", "/dev/shm"]
            )

            self.assertEqual(list(reader.lookup("where", "/nonexistent")), [])

        finally:
            os.unlink(mock_mounts_file)

    def test_lookup_invalid_key(self):
        """Test that lookup rejects a key that is not a MountsEntry field."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.mounts') as f:
            f.write("tmpfs /run tmpfs rw 0 0\n")
            mock_mounts_file = f.name

        try:
            reader = mounts.ProcMountsReader(path=mock_mounts_file)
            with self.assertRaisesRegex(KeyError, "Invalid lookup key"):
                # The generator body only runs once it is iterated.
                list(reader.lookup("device", "/dev/vda1"))

        finally:
            os.unlink(mock_mounts_file)


class MountsDiscoveryTests(unittest.TestCase):
    """
    Tests for Mounts.discover_mounts().

    Snapm recognises its own mounts from the provenance records it writes
    as it makes them, so each test sets up a mount table and the records
    that do or do not account for it (#1025).
    """

    @staticmethod
    @contextmanager
    def _mount_table(lines, anon=()):
        """Make both mount table readers read synthetic mount tables.

        Mount points named in ``anon`` are given an anonymous device
        number in the mountinfo table.
        """
        pmr = mounts.ProcMountsReader
        mir = snapm.MountInfoReader
        with _mounts_file(lines) as mounts_path:
            with _mounts_file(_mountinfo_lines(lines, anon=anon)) as mountinfo_path:
                with unittest.mock.patch.object(
                    mounts,
                    "ProcMountsReader",
                    lambda *args, **kwargs: pmr(path=mounts_path),
                ):
                    with unittest.mock.patch.object(
                        mounts,
                        "MountInfoReader",
                        lambda *args, **kwargs: mir(path=mountinfo_path),
                    ):
                        yield

    @staticmethod
    def _real_mount_point():
        """Return a (device, mount point) pair for a real non-root mount."""
        for entry in mounts.ProcMountsReader().entries:
            if entry.where == "/" or not entry.what.startswith("/dev/"):
                continue
            try:
                if not S_ISBLK(os.stat(entry.what).st_mode):
                    continue
            except OSError:
                continue
            return entry.what, entry.where
        return None, None

    def _devices_other_than(self, devpath, count):
        """Return ``count`` aliased devices that are not ``devpath``."""
        canonical = os.path.realpath(devpath)
        devices = [dev for dev in self.devices if dev[0] != canonical]
        if len(devices) < count:
            self.skipTest(f"Fewer than {count} usable block devices available")
        return devices[:count]

    def _tmpdir(self, prefix):
        """Return the path to a new, empty directory."""
        dir_obj = tempfile.TemporaryDirectory(prefix=prefix)
        self.addCleanup(dir_obj.cleanup)
        return dir_obj.name

    def setUp(self):
        self.devices = _alias_block_devices(4)
        if len(self.devices) < 4:
            self.skipTest("Fewer than four aliased block devices available")
        self.root = self._tmpdir("snapm_discover_root_")
        self.state = self._tmpdir("snapm_discover_state_")
        self.lock = self._tmpdir("snapm_discover_lock_")

    def _record(
        self, mount_path, snapset, root_device, created=False, mount_devno=None
    ):
        """Write the provenance record for a mount snapm made.

        A record with no ``mount_devno`` is one written by a command that
        did not reach the end of its mount.
        """
        record = mountstate.MountStateRecord(
            mount_path=mount_path,
            snapset_name=snapset.name,
            snapset_uuid=str(snapset.uuid),
            root_device=root_device,
            root_devno=os.stat(root_device).st_rdev,
            created_mount_point=created,
            mount_devno=mount_devno,
        )
        mountstate.MountStateDir(self.state).add(record)
        return record

    def _discover(self, snapsets):
        """Build a Mounts object over the test mounts and state directories."""
        manager = SimpleNamespace(snapshot_sets=snapsets)
        return mounts.Mounts(manager, self.root, self.state, self.lock)

    def _records(self):
        """Return the mount paths the state directory still has records for."""
        return [r.mount_path for r in mountstate.MountStateDir(self.state).records]

    def test_discover_mount_from_record(self):
        """Test that a recorded snapshot set mount is discovered."""
        root_dev, root_path = self._real_mount_point()
        if root_dev is None:
            self.skipTest("No non-root block device mount available")
        ((_, opt_alias),) = self._devices_other_than(root_dev, 1)
        snapset = _fake_snapset([("/opt", opt_alias)])
        self._record(root_path, snapset, root_dev)

        opt_dev = os.path.realpath(opt_alias)
        with self._mount_table(
            [
                f"{root_dev} / ext4 rw 0 0",
                f"{root_dev} {root_path} ext4 rw 0 0",
                f"{opt_dev} {root_path}/opt ext4 rw 0 0",
            ]
        ):
            discovered = self._discover([snapset])

        self.assertEqual([mount.root for mount in discovered._mounts], [root_path])
        self.assertEqual(snapset.mount_root, root_path)
        self.assertEqual(self._records(), [root_path])

    def test_discover_mount_with_an_anonymous_superblock(self):
        """Test that a mount with an anonymous device number is kept.

        A file system such as btrfs reports a device number that is the
        st_rdev of no device at all, so a record that names only the
        device it set out to mount has to be matched through the mount
        table index. Discarding it here unmounts nothing and destroys the
        only record of a mount that is still up.
        """
        root_dev, root_path = self._real_mount_point()
        if root_dev is None:
            self.skipTest("No non-root block device mount available")
        snapset = _fake_snapset([("/", root_dev)])
        self._record(root_path, snapset, root_dev)

        with self._mount_table(
            [
                f"{root_dev} / ext4 rw 0 0",
                f"{root_dev} {root_path} btrfs rw 0 0",
            ],
            anon=[root_path],
        ):
            discovered = self._discover([snapset])

        self.assertEqual([mount.root for mount in discovered._mounts], [root_path])
        self.assertEqual(self._records(), [root_path])

    def test_discover_verifies_a_mount_by_its_recorded_devno(self):
        """Test that the device number noted at mount time is authoritative.

        Once a mount has been made the record carries the device number
        the kernel gave it, which is comparable with the mount table
        whatever the file system is, and is used in preference to the
        device the mount was made from.
        """
        root_dev, root_path = self._real_mount_point()
        if root_dev is None:
            self.skipTest("No non-root block device mount available")
        ((other_dev, _),) = self._devices_other_than(root_dev, 1)
        snapset = _fake_snapset([("/", root_dev)])

        with self._mount_table(
            [
                f"{root_dev} / ext4 rw 0 0",
                f"{root_dev} {root_path} btrfs rw 0 0",
            ],
            anon=[root_path],
        ):
            devno = mounts.MountInfoReader().lookup_mount_point(root_path).devno
            # The recorded device is not the one the mount table names.
            self._record(root_path, snapset, other_dev, mount_devno=devno)
            discovered = self._discover([snapset])

        self.assertEqual([mount.root for mount in discovered._mounts], [root_path])
        self.assertEqual(self._records(), [root_path])

    def test_discover_passes_over_a_record_with_a_stale_devno(self):
        """Test that a mount point something else was mounted on is dropped.

        A record carrying the device number of the mount it describes must
        still fail to verify when that mount has gone and another has
        taken its place (#1025).
        """
        root_dev, root_path = self._real_mount_point()
        if root_dev is None:
            self.skipTest("No non-root block device mount available")
        snapset = _fake_snapset([("/", root_dev)])
        self._record(root_path, snapset, root_dev, mount_devno=0)

        with self._mount_table(
            [
                f"{root_dev} / ext4 rw 0 0",
                f"{root_dev} {root_path} ext4 rw 0 0",
            ]
        ):
            discovered = self._discover([snapset])
            self.assertEqual(discovered._mounts, [])
            # Discovery leaves it; the next unmount reaps it.
            self.assertEqual(self._records(), [root_path])

            with self.assertRaisesRegex(snapm.SnapmNotFoundError, "not found"):
                discovered.umount(snapset)

        self.assertEqual(self._records(), [])

    def test_umount_waits_for_the_mount_state_lock(self):
        """Test that a command that writes mount state takes the lock.

        Mounting and unmounting are serialised against each other, so a
        command that cannot take the lock gives up rather than writing
        over a mount another command is in the middle of making.
        """
        ((root_dev, _),) = self._devices_other_than("/dev/null", 1)
        snapset = _fake_snapset([("/", root_dev)])
        mount_path = os.path.join(self.root, snapset.name)
        os.mkdir(mount_path)
        self._record(mount_path, snapset, root_dev, created=True)

        with self._mount_table([f"{root_dev} / ext4 rw 0 0"]):
            discovered = self._discover([snapset])
            discovered._lock_timeout = 0.2

            with mountstate.mount_state_lock(self.lock):
                with self.assertRaisesRegex(snapm.SnapmBusyError, "mount state lock"):
                    discovered.umount(snapset)

            # Nothing was reaped while the lock was held elsewhere.
            self.assertEqual(self._records(), [mount_path])
            self.assertTrue(os.path.isdir(mount_path))

            # With the lock free the unmount proceeds and reaps as usual.
            with self.assertRaisesRegex(snapm.SnapmNotFoundError, "not found"):
                discovered.umount(snapset)

        self.assertEqual(self._records(), [])
        self.assertFalse(os.path.exists(mount_path))

    def test_discover_does_not_match_a_record_by_name(self):
        """Test that a set is matched by UUID and not by name.

        A record names its set as well as identifying it, so that the
        directory can be read. If the set a record was written for is
        gone and a new set has since been created with the same name,
        matching on the name would hand the new set a mount tree that is
        not its own, and 'snapset umount' would then take it down under
        the new set's name.
        """
        root_dev, root_path = self._real_mount_point()
        if root_dev is None:
            self.skipTest("No non-root block device mount available")
        mounted = _fake_snapset([("/", root_dev)])
        self._record(root_path, mounted, root_dev)

        # A different set that has since taken the same name.
        replacement = _fake_snapset([("/", root_dev)])
        replacement.uuid = uuid5(snapm.NAMESPACE_SNAPSHOT_SET, "a-different-set")
        self.assertEqual(replacement.name, mounted.name)
        self.assertNotEqual(replacement.uuid, mounted.uuid)

        with self._mount_table(
            [
                f"{root_dev} / ext4 rw 0 0",
                f"{root_dev} {root_path} ext4 rw 0 0",
            ]
        ):
            discovered = self._discover([replacement])

        # The mount belongs to neither: it is not attached to the set that
        # merely shares its name.
        self.assertEqual(discovered._mounts, [])
        self.assertEqual(replacement.mount_root, "")
        # The record is kept: it still describes a mount that is up.
        self.assertEqual(self._records(), [root_path])

    def test_discover_ignores_a_mount_with_no_record(self):
        """Test that a snapshot mounted by hand is not a snapshot set mount.

        An administrator who mounts a set's root snapshot at a path of
        their own produces a mount that matches on every device snapm can
        check. Without a record it is not snapm's, and 'snapset umount'
        must not take it down (#1025).
        """
        root_dev, root_path = self._real_mount_point()
        if root_dev is None:
            self.skipTest("No non-root block device mount available")
        snapset = _fake_snapset([("/", root_dev)])

        with self._mount_table(
            [
                f"{root_dev} / ext4 rw 0 0",
                f"{root_dev} {root_path} ext4 rw 0 0",
            ]
        ):
            discovered = self._discover([snapset])

            self.assertEqual(discovered._mounts, [])
            self.assertEqual(snapset.mount_root, "")

            # An unqualified unmount finds nothing to unmount.
            with self.assertRaisesRegex(snapm.SnapmNotFoundError, "not found"):
                discovered.umount(snapset)

        # The administrator's mount point is left alone.
        self.assertTrue(os.path.isdir(root_path))

    def test_discover_passes_over_a_record_for_an_unmounted_path(self):
        """Test that a record for a path nothing is mounted at is not used.

        Discovery runs for every command, including those that only
        report, so it reads the record directory without writing to it:
        the record stays until a command that mounts or unmounts reaps it.
        """
        ((root_dev, _),) = self._devices_other_than("/dev/null", 1)
        snapset = _fake_snapset([("/", root_dev)])
        mount_path = os.path.join(self.root, snapset.name)
        os.mkdir(mount_path)
        self._record(mount_path, snapset, root_dev, created=True)

        with self._mount_table([f"{root_dev} / ext4 rw 0 0"]):
            discovered = self._discover([snapset])

        self.assertEqual(discovered._mounts, [])
        self.assertEqual(snapset.mount_root, "")
        # Nothing was written: the record and the mount point are both
        # still here.
        self.assertEqual(self._records(), [mount_path])
        self.assertTrue(os.path.isdir(mount_path))

    def test_umount_reaps_a_record_for_an_unmounted_path(self):
        """Test that a command that writes clears up the stale records.

        A command that mounts or unmounts is going to write to the record
        directory in any case, so it is the one that discards the records
        describing mounts that are over, and the mount points snapm made
        for them.
        """
        ((root_dev, _),) = self._devices_other_than("/dev/null", 1)
        snapset = _fake_snapset([("/", root_dev)])
        mount_path = os.path.join(self.root, snapset.name)
        os.mkdir(mount_path)
        self._record(mount_path, snapset, root_dev, created=True)

        with self._mount_table([f"{root_dev} / ext4 rw 0 0"]):
            discovered = self._discover([snapset])
            with self.assertRaisesRegex(snapm.SnapmNotFoundError, "not found"):
                discovered.umount(snapset)

        # The record and the mount point snapm created go together.
        self.assertEqual(self._records(), [])
        self.assertFalse(os.path.exists(mount_path))

    def test_reap_keeps_a_nominated_mount_point(self):
        """Test that discarding a record leaves a nominated point in place."""
        ((root_dev, _),) = self._devices_other_than("/dev/null", 1)
        snapset = _fake_snapset([("/", root_dev)])
        mount_path = self._tmpdir("snapm_discover_point_")
        self._record(mount_path, snapset, root_dev, created=False)

        with self._mount_table([f"{root_dev} / ext4 rw 0 0"]):
            discovered = self._discover([snapset])
            with self.assertRaisesRegex(snapm.SnapmNotFoundError, "not found"):
                discovered.umount(snapset)

        self.assertEqual(discovered._mounts, [])
        self.assertEqual(self._records(), [])
        self.assertTrue(os.path.isdir(mount_path))

    def test_discover_passes_over_a_record_for_a_replaced_mount(self):
        """Test that another file system at the recorded path is not ours.

        Snapm's mount can be taken down and something else mounted on the
        same path. Only the device that was recorded establishes that the
        mount still there is snapm's.
        """
        root_dev, root_path = self._real_mount_point()
        if root_dev is None:
            self.skipTest("No non-root block device mount available")
        ((other_dev, _),) = self._devices_other_than(root_dev, 1)
        snapset = _fake_snapset([("/", other_dev)])
        self._record(root_path, snapset, other_dev)

        with self._mount_table(
            [
                f"{root_dev} / ext4 rw 0 0",
                f"{root_dev} {root_path} ext4 rw 0 0",
            ]
        ):
            discovered = self._discover([snapset])

        self.assertEqual(discovered._mounts, [])
        # Discovery writes nothing: the record goes when a command that
        # mounts or unmounts reaps it.
        self.assertEqual(self._records(), [root_path])
        # The mount that replaced snapm's is not snapm's to remove.
        self.assertTrue(os.path.isdir(root_path))

    def test_discover_matches_a_renamed_root_device(self):
        """Test that a record matches on device number, not device name.

        The recorded device can be renamed while the set is mounted, and
        the mount table keeps the name it was mounted by. Neither name has
        to resolve for the mount to be recognised (#1030).
        """
        root_dev, root_path = self._real_mount_point()
        if root_dev is None:
            self.skipTest("No non-root block device mount available")
        snapset = _fake_snapset([("/", root_dev)])
        record = self._record(root_path, snapset, root_dev)

        # Rewrite the record with a device path that no longer exists,
        # keeping the device number it was recorded with.
        record.root_device = "/dev/testvg/renamed-away"
        mountstate.MountStateDir(self.state).add(record)

        with self._mount_table(
            [
                f"{root_dev} / ext4 rw 0 0",
                f"{root_dev} {root_path} ext4 rw 0 0",
            ]
        ):
            discovered = self._discover([snapset])

        self.assertEqual([mount.root for mount in discovered._mounts], [root_path])

    def test_discover_keeps_a_record_for_an_unknown_set(self):
        """Test that a live mount of a set snapm no longer knows is kept.

        The mount is the one thing that still needs tearing down, and the
        record is the only description of it: dropping it would strand the
        mount with nothing left to describe it.
        """
        root_dev, root_path = self._real_mount_point()
        if root_dev is None:
            self.skipTest("No non-root block device mount available")
        snapset = _fake_snapset([("/", root_dev)])
        self._record(root_path, snapset, root_dev)

        with self._mount_table(
            [
                f"{root_dev} / ext4 rw 0 0",
                f"{root_dev} {root_path} ext4 rw 0 0",
            ]
        ):
            with unittest.mock.patch.object(mounts, "_log_warn") as log_warn:
                discovered = self._discover([])

        self.assertEqual(discovered._mounts, [])
        self.assertEqual(self._records(), [root_path])
        self.assertTrue(
            any("no longer known" in call[0][0] for call in log_warn.call_args_list)
        )

    def test_discover_matches_a_record_by_uuid(self):
        """Test that a record is matched to its set by UUID, not by name.

        The name in a record is there to make the record readable; the
        UUID is what identifies the set.
        """
        root_dev, root_path = self._real_mount_point()
        if root_dev is None:
            self.skipTest("No non-root block device mount available")
        snapset = _fake_snapset([("/", root_dev)])
        self._record(root_path, snapset, root_dev)
        snapset.name = "renamed"

        with self._mount_table(
            [
                f"{root_dev} / ext4 rw 0 0",
                f"{root_dev} {root_path} ext4 rw 0 0",
            ]
        ):
            discovered = self._discover([snapset])

        self.assertEqual([mount.root for mount in discovered._mounts], [root_path])
        self.assertEqual(snapset.mount_root, root_path)

    def test_discover_warns_about_a_set_mounted_twice(self):
        """Test that a set with records for two live mounts is reported."""
        root_dev, root_path = self._real_mount_point()
        if root_dev is None:
            self.skipTest("No non-root block device mount available")
        second_path = self._tmpdir("snapm_discover_second_")
        snapset = _fake_snapset([("/", root_dev)])
        self._record(root_path, snapset, root_dev)
        self._record(second_path, snapset, root_dev)

        with self._mount_table(
            [
                f"{root_dev} / ext4 rw 0 0",
                f"{root_dev} {root_path} ext4 rw 0 0",
                f"{root_dev} {second_path} ext4 rw 0 0",
            ]
        ):
            # Both paths have to read as mount points for the Mount objects
            # to be built: only one of them really is.
            with unittest.mock.patch.object(
                os.path, "ismount", lambda path: path in ("/", root_path, second_path)
            ):
                with unittest.mock.patch.object(mounts, "_log_warn") as log_warn:
                    discovered = self._discover([snapset])

        self.assertEqual(
            sorted(mount.root for mount in discovered._mounts),
            sorted([root_path, second_path]),
        )
        self.assertTrue(
            any(
                "mounted at multiple paths" in call[0][0]
                for call in log_warn.call_args_list
            )
        )


class MountsMountSelectionTests(unittest.TestCase):
    """
    Tests for the mount selection logic in Mounts.mount().
    """

    #: The device the stub mounts pretend to mount.
    ROOT_DEVICE = "/dev/testvg/testset0-root"

    #: The device number the stub root device pretends to have.
    ROOT_DEVNO = 0x1234

    def setUp(self):
        manager = SimpleNamespace(snapshot_sets=[])
        self.root = self._tmpdir("snapm_mount_root_")
        self.state = self._tmpdir("snapm_mount_state_")
        self.lock = self._tmpdir("snapm_mount_lock_")
        self.mounts = mounts.Mounts(manager, self.root, self.state, self.lock)
        self.snapset = SimpleNamespace(
            name="testset0",
            uuid=UUID("00000000-0000-5000-a000-000000000000"),
            mount_root="",
            snapshots=[],
            activate=lambda: None,
        )

    def _tmpdir(self, prefix):
        """Return the path to a new, empty directory."""
        dir_obj = tempfile.TemporaryDirectory(prefix=prefix)
        self.addCleanup(dir_obj.cleanup)
        return dir_obj.name

    def _mount_point(self):
        """Return a new, empty directory to nominate as a mount point."""
        return self._tmpdir("snapm_mount_point_")

    def _default_path(self):
        """Return the default mount point path for the test snapshot set."""
        return os.path.join(self.root, self.snapset.name)

    @staticmethod
    def _stub_mount(snapset, root, discover=False, root_device=None):
        """Build a stub mount, in place of mounting the set for real."""
        return SimpleNamespace(
            snapset=snapset,
            root=root,
            root_device=root_device,
            mounted=True,
            mount=lambda: None,
        )

    @contextmanager
    def _stubbed(self):
        """Stub out everything that touches the system for real."""
        with unittest.mock.patch.object(mounts, "Mount", self._stub_mount):
            with unittest.mock.patch.object(
                mounts, "FsTabReader", lambda *args, **kwargs: None
            ):
                with unittest.mock.patch.object(
                    mounts, "find_snapset_root", lambda *args: self.ROOT_DEVICE
                ):
                    with unittest.mock.patch.object(
                        mounts, "device_number", lambda devpath: self.ROOT_DEVNO
                    ):
                        yield

    def _mount(self, mount_root=None):
        """Call Mounts.mount() without mounting anything."""
        with self._stubbed():
            return self.mounts.mount(self.snapset, mount_root=mount_root)

    def _record(self, mount_path):
        """Return the mount state record written for ``mount_path``."""
        return mountstate.MountStateDir(self.state).get(mount_path)

    def test_mount_creates_the_default_mount_point(self):
        """Test that snapm creates the default mount point itself."""
        mount_obj = self._mount()

        self.assertEqual(mount_obj.root, self._default_path())
        self.assertTrue(os.path.isdir(mount_obj.root))

    def test_mount_second_root_creates_second_mount(self):
        """Test that a nominated mount point mounts a set already mounted."""
        one = self._mount_point()
        two = self._mount_point()

        first = self._mount(mount_root=one)
        second = self._mount(mount_root=two)

        self.assertIsNot(first, second)
        self.assertEqual(first.root, one)
        self.assertEqual(second.root, two)
        self.assertEqual(self.mounts._mounts, [first, second])

    def test_mount_same_root_returns_existing(self):
        """Test that re-mounting at one mount point returns the same mount."""
        point = self._mount_point()

        first = self._mount(mount_root=point)
        again = self._mount(mount_root=point)

        self.assertIs(again, first)
        self.assertEqual(self.mounts._mounts, [first])

    def test_mount_without_root_returns_existing(self):
        """Test that mount() with no mount root accepts any existing mount."""
        first = self._mount(mount_root=self._mount_point())
        again = self._mount()

        self.assertIs(again, first)
        self.assertEqual(self.mounts._mounts, [first])

    def test_mount_does_not_create_a_nominated_mount_point(self):
        """Test that a mount point that does not exist is refused."""
        missing = os.path.join(self._mount_point(), "nosuchdir")

        with self.assertRaisesRegex(
            snapm.SnapmPathError, "Mount point does not exist"
        ):
            self._mount(mount_root=missing)

        self.assertFalse(os.path.exists(missing))
        self.assertEqual(self.mounts._mounts, [])

    def test_mount_nominated_mount_point_not_a_directory(self):
        """Test that a mount point that is not a directory is refused."""
        path = os.path.join(self._mount_point(), "file")
        with open(path, "w", encoding="utf8"):
            pass

        with self.assertRaisesRegex(
            snapm.SnapmPathError, "Mount point is not a directory"
        ):
            self._mount(mount_root=path)

    def test_mount_nominated_mount_point_symlink(self):
        """Test that a mount point that is a symbolic link is refused."""
        link = os.path.join(self._mount_point(), "link")
        os.symlink(self._mount_point(), link)

        with self.assertRaisesRegex(
            snapm.SnapmPathError, "Mount point is a symbolic link"
        ):
            self._mount(mount_root=link)

    def test_mount_resolves_a_link_in_the_nominated_mount_point_path(self):
        """Test that a link leading to a mount point is resolved."""
        # mount(8) follows the link and the kernel reports the resolved
        # path: a mount recorded at the unresolved one is never found
        # again (#1029).
        parent = self._mount_point()
        point = os.path.join(parent, "point")
        os.mkdir(point)
        link = os.path.join(self._mount_point(), "link")
        os.symlink(parent, link)

        mount_obj = self._mount(mount_root=os.path.join(link, "point"))

        self.assertEqual(mount_obj.root, point)
        self.assertEqual(self.snapset.mount_root, point)

    def test_mount_refuses_a_link_into_the_mounts_directory(self):
        """Test that a link may not redirect --mount-root into the mounts dir."""
        nested = os.path.join(self.root, "a")
        os.mkdir(nested)
        link = os.path.join(self._mount_point(), "link")
        os.symlink(nested, link)

        with self.assertRaisesRegex(
            snapm.SnapmPathError, "within the snapm mounts directory"
        ):
            self._mount(mount_root=link)

        self.assertEqual(self.mounts._mounts, [])

    def test_mount_nominated_mount_point_need_not_be_empty(self):
        """Test that a nominated mount point may have contents to shadow."""
        point = self._mount_point()
        with open(os.path.join(point, "shadowed"), "w", encoding="utf8"):
            pass

        mount_obj = self._mount(mount_root=point)

        self.assertEqual(mount_obj.root, point)

    def test_mount_replaces_stale_default_mount_point(self):
        """Test that a stale default mount point is discarded and remade."""
        first = self._mount()
        self.assertTrue(os.path.isdir(first.root))

        # The set is unmounted behind snapm's back: the mount table entry
        # goes stale, but the mount point is left behind.
        first.mounted = False

        second = self._mount()

        self.assertIsNot(second, first)
        self.assertEqual(second.root, first.root)
        self.assertEqual(self.mounts._mounts, [second])
        self.assertTrue(os.path.isdir(second.root))

    def test_mount_keeps_a_stale_nominated_mount_point(self):
        """Test that pruning leaves a nominated mount point in place."""
        point = self._mount_point()
        first = self._mount(mount_root=point)

        first.mounted = False
        second = self._mount(mount_root=point)

        # The entry is replaced, but the directory is not snapm's to remove.
        self.assertIsNot(second, first)
        self.assertEqual(self.mounts._mounts, [second])
        self.assertTrue(os.path.isdir(point))

    def test_mount_prefers_live_mount_to_stale_entry(self):
        """Test that a live mount is used when an earlier entry is stale."""
        first = self._mount(mount_root=self._mount_point())
        second = self._mount(mount_root=self._mount_point())

        # The first mount goes away behind snapm's back, leaving the second
        # mount live but no longer the first entry recorded for the set.
        first.mounted = False

        again = self._mount()

        self.assertIs(again, second)
        self.assertEqual(self.mounts._mounts, [second])

        # The pruned mount point was nominated, so it is left in place.
        self.assertTrue(os.path.isdir(first.root))

        # The pruned mount is no longer the one reported for the set
        self.assertEqual(self.snapset.mount_root, second.root)

    def test_mount_clears_mount_root_when_remount_fails(self):
        """Test that a failed re-mount does not leave a stale mount root."""
        first = self._mount()
        self.assertEqual(self.snapset.mount_root, first.root)

        # The mount goes away behind snapm's back, and something is left in
        # the mount point so that it can be neither removed nor re-created.
        first.mounted = False
        with open(os.path.join(first.root, "leftover"), "w", encoding="utf8"):
            pass

        with self.assertRaises(snapm.SnapmPathError):
            self._mount()

        self.assertEqual(self.mounts._mounts, [])
        self.assertEqual(self.snapset.mount_root, "")

    def test_mount_reuses_a_leftover_mount_point(self):
        """Test that a mount point left by an outside unmount is re-used."""
        mount_path = self._default_path()

        # 'umount -R <path>' outside snapm leaves the mount point behind
        # with nothing mounted on it and nothing in the mount table.
        os.mkdir(mount_path)
        self.assertEqual(self.mounts._mounts, [])

        mount_obj = self._mount()

        self.assertEqual(mount_obj.root, mount_path)
        self.assertTrue(os.path.isdir(mount_path))

    def test_mount_point_in_use_raises_path_error(self):
        """Test that an occupied mount point fails with a SnapmError."""
        mount_path = self._default_path()
        os.mkdir(mount_path)
        occupant = os.path.join(mount_path, "occupant")
        with open(occupant, "w", encoding="utf8"):
            pass

        with self.assertRaisesRegex(
            snapm.SnapmPathError, "is not an empty directory"
        ):
            self._mount()

        # A directory that is not empty is not snapm's to re-use.
        self.assertTrue(os.path.exists(occupant))

    def test_mount_point_symlink_raises_path_error(self):
        """Test that a symbolic link cannot redirect the mount."""
        target = self._mount_point()
        mount_path = self._default_path()

        # A link to an empty directory: refused for being a link, not for
        # what it happens to point at.
        os.symlink(target, mount_path)

        with self.assertRaisesRegex(
            snapm.SnapmPathError, "is not an empty directory"
        ):
            self._mount()

        # Neither the link nor what it points at is touched.
        self.assertTrue(os.path.islink(mount_path))
        self.assertTrue(os.path.isdir(target))
        self.assertEqual(os.listdir(target), [])

    def test_mount_point_already_mounted_raises_path_error(self):
        """Test that a directory with a file system on it is not re-used."""
        mount_path = self._default_path()
        os.mkdir(mount_path)

        # An empty file system mounted here reads as an empty directory,
        # and re-using it would stack a second mount on top.
        with unittest.mock.patch.object(
            os.path, "ismount", lambda path: path == mount_path
        ):
            with self.assertRaisesRegex(
                snapm.SnapmPathError, "is not an empty directory"
            ):
                self._mount()

        self.assertEqual(self.mounts._mounts, [])

    def test_mount_refuses_a_mount_point_in_the_mounts_directory(self):
        """Test that --mount-root may not name a path snapm manages."""
        nested = os.path.join(self.root, "a", "b")
        os.makedirs(nested)

        for path in (self.root, os.path.join(self.root, "a"), nested):
            with self.subTest(path=path):
                with self.assertRaisesRegex(
                    snapm.SnapmPathError, "within the snapm mounts directory"
                ):
                    self._mount(mount_root=path)

        self.assertEqual(self.mounts._mounts, [])
        self.assertTrue(os.path.isdir(nested))

    def test_mount_refuses_a_writable_mounts_directory(self):
        """Test that a mounts directory other users can write to is refused."""
        os.chmod(self.root, 0o777)

        with self.assertRaisesRegex(
            snapm.SnapmPathError, "writable by other users"
        ):
            self._mount()

        self.assertEqual(os.listdir(self.root), [])

    def test_mount_point_outside_default_root_raises_path_error(self):
        """Test that a set name that is not a path component is refused."""
        # An empty name joins to the mounts directory, which is an empty
        # directory whenever no set is mounted: without the containment
        # check it is re-used and later removed.
        for name in ("", ".", ".."):
            with self.subTest(name=name):
                self.snapset.name = name

                with self.assertRaisesRegex(
                    snapm.SnapmPathError, "does not name a mount point"
                ):
                    self._mount()

                self.assertEqual(os.listdir(self.root), [])

    def test_mount_reports_canonical_mount_root(self):
        """Test that a second mount leaves the reported mount root alone."""
        first = self._mount(mount_root=self._mount_point())
        self.assertEqual(self.snapset.mount_root, first.root)

        self._mount(mount_root=self._mount_point())
        self.assertEqual(self.snapset.mount_root, first.root)


class MountsUmountSelectionTests(unittest.TestCase):
    """
    Tests for the mount selection logic in Mounts.umount().
    """

    def setUp(self):
        manager = SimpleNamespace(snapshot_sets=[])
        root_obj = tempfile.TemporaryDirectory(prefix="snapm_umount_root_")
        self.addCleanup(root_obj.cleanup)
        self.root = root_obj.name
        state_obj = tempfile.TemporaryDirectory(prefix="snapm_umount_state_")
        self.addCleanup(state_obj.cleanup)
        self.state = state_obj.name
        lock_obj = tempfile.TemporaryDirectory(prefix="snapm_umount_lock_")
        self.addCleanup(lock_obj.cleanup)
        self.lock = lock_obj.name
        self.mounts = mounts.Mounts(manager, self.root, self.state, self.lock)
        self.snapset = SimpleNamespace(
            name="testset0",
            uuid=UUID("00000000-0000-5000-a000-000000000000"),
            mount_root="",
        )

    def _add_mount(self, name=None, default_root=False, record=True):
        """Register a stub mount for self.snapset at a real directory.

        A mount snapm made has a provenance record, and a mount point in
        the mounts directory is one snapm created and may remove again
        (#1025). Pass ``record=False`` for a mount snapm did not make.
        """
        if default_root:
            root = os.path.join(self.root, self.snapset.name)
        else:
            dir_obj = tempfile.TemporaryDirectory(prefix="snapm_umount_sel_")
            self.addCleanup(dir_obj.cleanup)
            root = os.path.join(dir_obj.name, name)
        os.makedirs(root)
        if record:
            self.mounts._state.add(
                mountstate.MountStateRecord(
                    mount_path=root,
                    snapset_name=self.snapset.name,
                    snapset_uuid=str(self.snapset.uuid),
                    root_device="/dev/testvg/testset0-root",
                    root_devno=0x1234,
                    created_mount_point=default_root,
                )
            )
        mount = SimpleNamespace(
            snapset=self.snapset, root=root, mounted=True, umount=lambda: None
        )
        self.mounts._mounts.append(mount)
        return mount

    def test_umount_not_mounted(self):
        """Test that unmounting a snapshot set that is not mounted fails."""
        with self.assertRaisesRegex(snapm.SnapmNotFoundError, "not found"):
            self.mounts.umount(self.snapset)

    def test_umount_completes_when_the_mount_point_will_not_go(self):
        """Test that a mount point that cannot be removed is not fatal.

        The unmount has already succeeded by this point. A directory
        that will not go is untidy rather than a failure, and the record
        and the table entry have to go either way: leaving them would
        leave both describing a mount that is no longer there.
        """
        mount = self._add_mount(default_root=True)
        # Something else is in the way, so rmdir() fails with ENOTEMPTY.
        in_the_way = os.path.join(mount.root, "occupied")
        os.mkdir(in_the_way)

        # Nothing is really mounted here, so the reap would otherwise
        # discard the record before the unmount reaches the mount point.
        with unittest.mock.patch.object(
            mounts.Mounts, "_record_is_live", staticmethod(lambda record, mi: True)
        ):
            with self.assertLogs(mounts._log, level="WARNING") as caught:
                self.mounts.umount(self.snapset)

        self.assertIn("Could not remove mount point", "\n".join(caught.output))
        # The unmount reported success and finished its cleanup.
        self.assertEqual(self.mounts._mounts, [])
        self.assertEqual(self.mounts._state.get(mount.root), None)
        self.assertEqual(self.snapset.mount_root, "")
        self.assertTrue(os.path.isdir(in_the_way))

    def test_umount_mount_root_selects_nothing(self):
        """Test that a mount root matching no mount fails."""
        self._add_mount("one")
        with self.assertRaisesRegex(snapm.SnapmNotFoundError, "not found"):
            self.mounts.umount(self.snapset, mount_root="/no/such/path")

    def test_umount_mount_root_resolves_symlinks(self):
        """Test that a link leading to a mount point selects the mount."""
        # The mount is recorded at the path mount(8) resolved it to, so a
        # link naming the same mount point has to select it (#1029).
        mount = self._add_mount("one")
        link = os.path.join(self.root, "link")
        os.symlink(os.path.dirname(mount.root), link)

        self.mounts.umount(self.snapset, mount_root=os.path.join(link, "one"))

        self.assertEqual(self.mounts._mounts, [])

    def test_umount_ambiguous_without_mount_root(self):
        """Test that unmounting a set mounted twice needs a mount root."""
        first = self._add_mount("one")
        second = self._add_mount("two")

        with self.assertRaises(snapm.SnapmArgumentError) as ctx:
            self.mounts.umount(self.snapset)

        # The error names the mount point of each, so that the values it
        # prints can be passed straight back in as --mount-root.
        message = str(ctx.exception)
        self.assertIn(first.root, message)
        self.assertIn(second.root, message)
        self.assertIn("--mount-root", message)

        # Nothing was unmounted
        self.assertEqual(len(self.mounts._mounts), 2)

        # Each value it printed selects one of the mounts
        for mount in (first, second):
            self.mounts.umount(self.snapset, mount_root=mount.root)
            self.assertNotIn(mount, self.mounts._mounts)

    def test_umount_mount_root_disambiguates(self):
        """Test that a mount root selects one of several mounts."""
        first = self._add_mount("one")
        second = self._add_mount("two")

        self.mounts.umount(self.snapset, mount_root=first.root)

        self.assertEqual(self.mounts._mounts, [second])

        # The canonical mount and report field fall back to the survivor
        self.assertIs(self.mounts._canonical_mount(self.snapset), second)
        self.assertEqual(self.snapset.mount_root, second.root)

        # With one mount left the mount root is no longer required
        self.mounts.umount(self.snapset)
        self.assertEqual(self.mounts._mounts, [])
        self.assertIsNone(self.mounts._canonical_mount(self.snapset))
        self.assertEqual(self.snapset.mount_root, "")

    def test_umount_removes_only_mount_points_snapm_created(self):
        """Test that a nominated mount point survives the unmount."""
        default = self._add_mount(default_root=True)
        nominated = self._add_mount("one")

        # A path inside the mounts directory selects a mount here, even
        # though mount() refuses one: it is the path mount() reported.
        self.mounts.umount(self.snapset, mount_root=default.root)
        self.assertFalse(os.path.exists(default.root))

        self.mounts.umount(self.snapset, mount_root=nominated.root)
        self.assertTrue(os.path.isdir(nominated.root))

    def test_umount_prunes_a_stale_mount(self):
        """Test that a stale mount is discarded when unmounting."""
        first = self._add_mount(default_root=True)
        second = self._add_mount("two")

        # The first mount goes away behind snapm's back, and is still
        # recorded when the second one is unmounted.
        first.mounted = False

        self.mounts.umount(self.snapset, mount_root=second.root)

        # The stale entry and its mount point go with the unmount.
        self.assertEqual(self.mounts._mounts, [])
        self.assertFalse(os.path.exists(first.root))
        self.assertIsNone(self.mounts._canonical_mount(self.snapset))
        self.assertEqual(self.snapset.mount_root, "")

    def test_umount_stale_mount_is_not_ambiguous(self):
        """Test that a stale mount does not make an unmount ambiguous."""
        first = self._add_mount(default_root=True)
        second = self._add_mount("two")

        # The first mount goes away behind snapm's back, leaving one path
        # the set is still mounted at: no mount root should be needed.
        first.mounted = False

        self.mounts.umount(self.snapset)

        self.assertEqual(self.mounts._mounts, [])
        self.assertFalse(os.path.exists(first.root))
        self.assertTrue(os.path.isdir(second.root))
        self.assertEqual(self.snapset.mount_root, "")

    def test_find_mounts_forgets_a_stale_mount_without_writing(self):
        """Test that listing mounts drops a stale mount but writes nothing.

        Reporting on mounts must not touch the record directory: what a
        dropped entry left behind is removed by the next command that
        mounts or unmounts. The entry still goes from the list in memory
        and from the set's reported mount root, both of which follow from
        the mount table rather than from anything on disk.
        """
        first = self._add_mount(default_root=True)
        second = self._add_mount("two")

        first.mounted = False

        found = self.mounts.find_mounts(snapm.Selection(name=self.snapset.name))

        self.assertEqual(found, [second])
        self.assertEqual(self.mounts._mounts, [second])
        self.assertEqual(self.snapset.mount_root, second.root)
        # Nothing on disk was touched.
        self.assertTrue(os.path.exists(first.root))

    def test_umount_removes_what_find_mounts_left(self):
        """Test that the next unmount clears up after a stale mount."""
        first = self._add_mount(default_root=True)
        second = self._add_mount("two")

        first.mounted = False
        self.mounts.find_mounts(snapm.Selection(name=self.snapset.name))
        self.assertTrue(os.path.exists(first.root))

        self.mounts.umount(self.snapset, mount_root=second.root)

        self.assertFalse(os.path.exists(first.root))
