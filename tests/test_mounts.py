# Copyright Red Hat
#
# tests/test_mounts.py - Mount support tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
from subprocess import run, CalledProcessError
from contextlib import contextmanager
import unittest
import unittest.mock
import logging
import tempfile
import os.path
import pytest
import os


log = logging.getLogger()


import snapm
import snapm.manager._mounts as mounts
import snapm.manager
from snapm.manager.plugins import format_snapshot_name, encode_mount_point

from tests import have_root, is_redhat, in_rh_ci
from ._util import LoopBackDevices, LvmLoopBacked

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

    @unittest.mock.patch("snapm.manager._mounts.get_device_path")
    @unittest.mock.patch("os.path.exists")
    def test_resolve_device(self, mock_path_exists, mock_get_dev):
        """
        Tests the _resolve_device helper.
        """
        # 1. Test UUID
        mock_get_dev.return_value = "/dev/sda1"
        self.assertEqual(mounts._resolve_device("UUID=some-uuid"), "/dev/sda1")
        mock_get_dev.assert_called_with("uuid", "some-uuid")

        # 2. Test LABEL
        mock_get_dev.return_value = "/dev/sda2"
        self.assertEqual(mounts._resolve_device("LABEL=some-label"), "/dev/sda2")
        mock_get_dev.assert_called_with("label", "some-label")

        # 3. Test PARTUUID
        mock_path_exists.return_value = True
        self.assertEqual(mounts._resolve_device("PARTUUID=part-uuid"), "/dev/disk/by-partuuid/part-uuid")

        # 4. Test PARTUUID not found
        mock_path_exists.return_value = False
        with self.assertRaises(snapm.SnapmNotFoundError):
            mounts._resolve_device("PARTUUID=part-uuid-bad")

        # 5. Test plain device path
        self.assertEqual(mounts._resolve_device("/dev/vda1"), "/dev/vda1")

        # 6. Test UUID not found
        mock_get_dev.return_value = None
        with self.assertRaises(snapm.SnapmNotFoundError):
            mounts._resolve_device("UUID=some-uuid-bad")

        # 7. Test LABEL not found
        mock_get_dev.return_value = None
        with self.assertRaises(snapm.SnapmNotFoundError):
            mounts._resolve_device("LABEL=some-label-bad")

        # 8. Test PARTLABEL
        mock_path_exists.return_value = True
        self.assertEqual(mounts._resolve_device("PARTLABEL=part-label"), "/dev/disk/by-partlabel/part-label")

        # 9. Test PARTLABEL not found
        mock_path_exists.return_value = False
        with self.assertRaises(snapm.SnapmNotFoundError):
            mounts._resolve_device("PARTLABEL=part-label-bad")

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
                file.write(f"/dev/test_vg0/{origin}\t{mp}\text4\tdefaults 0 0\n")
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
            with mounted("/dev/test_vg0/root", tempdir):
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
        self._mounts_root_dir_obj = tempfile.TemporaryDirectory(prefix="snapm_mnt_")
        self.mounts_root_dir = self._mounts_root_dir_obj.name
        self.addCleanup(self._mounts_root_dir_obj.cleanup)

        # 6. Setup Manager and find the SnapshotSet
        self.manager = snapm.manager.Manager()
        self.manager.discover_snapshot_sets()
        self.snapset = self.manager.by_name[self.snapset_name]

        # 7. Initialize the Mounts object
        self.mounts = mounts.Mounts(self.manager, self.mounts_root_dir)

        # 8. Cleanup mounts - this MUST be executed before _mounts_root_dir_obj.cleanup
        # or very bad things will happen (rm -rf /dev/* /run/*)
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

        self.assertIn(self.snapset.name, self.mounts._mounts_by_name)
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

        self.assertNotIn(self.snapset.name, self.mounts._mounts_by_name)
        self.assertNotIn(mount_obj, self.mounts._mounts)
        self.assertFalse(mount_obj.mounted) # Check object state
        self.assertFalse(os.path.ismount(mount_obj.root)) # Check system state
        self.assertEqual(self.snapset.mount_root, "") # Check snapset state

        # Check that the mount directory itself is removed
        self.assertFalse(os.path.exists(mount_obj.root))

    def test_discover_mounts(self):
        """
        Tests that a new Mounts instance can discover pre-existing mounts.
        """
        # 1. Mount a snapset
        mount_obj = self.mounts.mount(self.snapset)
        self.assertTrue(mount_obj.mounted)
        self.assertEqual(len(self.mounts._mounts), 1)

        # 2. Create a *new* Mounts instance pointing to the same directory
        new_mounts = mounts.Mounts(self.manager, self.mounts_root_dir)

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
        # Note: self.snapset exists, but it's not in self.mounts._mounts_by_name
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

        # 2. Run discovery
        with self.assertLogs(mounts._log, level='INFO') as cm:
            new_mounts = mounts.Mounts(self.manager, self.mounts_root_dir)

            log_output = "\n".join(cm.output)
            self.assertIn("Ignoring invalid mount path", log_output)
            self.assertIn(dummy_path, log_output)

        # 3. Check that no mount was discovered
        self.assertEqual(len(new_mounts._mounts), 0)

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
        # 1. Create a directory that isn't a known snapset
        dummy_path = os.path.join(self.mounts_root_dir, "not-a-real-snapset")
        os.makedirs(dummy_path)

        # 2. Run discovery
        with self.assertLogs(mounts._log, level='INFO') as cm:
            new_mounts = mounts.Mounts(self.manager, self.mounts_root_dir)

            log_output = "\n".join(cm.output)
            self.assertIn("Skipping non-snapshot set path", log_output)
            self.assertIn("not-a-real-snapset", log_output)

        # 3. Check that no mount was discovered
        self.assertEqual(len(new_mounts._mounts), 0)

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
            new_mounts = mounts.Mounts(self.manager, self.mounts_root_dir)

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
            _ = mounts.Mounts(self.manager, self.mounts_root_dir)

            log_output = "\n".join(cm.output)
            self.assertNotIn("Missing snapshot set submounts", log_output)
            self.assertIn("Missing API file system submounts", log_output)
            self.assertIn("/proc", log_output)

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
