# Copyright Red Hat
#
# tests/test_mounts.py - Mount support tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
from os.path import ismount, join as path_join
from contextlib import contextmanager
from os import unlink, makedirs
from subprocess import run
import unittest
import logging
import tempfile


log = logging.getLogger()


import snapm
import snapm.manager._mounts as mounts
import snapm.manager
from snapm.manager.plugins import format_snapshot_name, encode_mount_point

from tests import have_root
from ._util import LoopBackDevices, LvmLoopBacked

ETC_FSTAB = "/etc/fstab"


@contextmanager
def mounted(what, where, fstype=None, options="defaults"):
    """Context manager for mounting/unmounting."""
    mounts._mount(what, where, fstype=fstype, options=options)
    try:
        yield where
    finally:
        if ismount(where):
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
            mount_point = path_join(tempdir, "mytmpfs")
            makedirs(mount_point, mode=0o700)

            with mounted("none", mount_point, fstype="tmpfs", options="size=1m,nosuid,nodev,noexec"):
                self.assertTrue(ismount(mount_point))
                with open(path_join(mount_point, "testfile"), "w", encoding="utf8") as fpw:
                   fpw.write(somestr)
                with open(path_join(mount_point, "testfile"), "r", encoding="utf8") as fpr:
                   self.assertEqual(fpr.read(), somestr)

        self.assertFalse(ismount(mount_point))

    def test__mount_bad_dev(self):
        with tempfile.TemporaryDirectory(suffix="_test_mount") as tempdir:
            mount_point = path_join(tempdir, "devmp")
            makedirs(mount_point, mode=0o700)
            with self.assertRaises(snapm.SnapmMountError):
                mounts._mount("/not/a/dev", mount_point, fstype="ext4")

    def test__mount_bad_mp(self):
        with self.assertRaises(snapm.SnapmMountError):
            mounts._mount(self.loop_devs.device_nodes()[0], "/last/train/to/trancentral")

    def test__umount_bad_mp(self):
        with self.assertRaises(snapm.SnapmUmountError):
            mounts._umount("/3/am/eternal")


@unittest.skipIf(not have_root(), "requires root privileges")
class MountsTests(unittest.TestCase):
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
                unlink(self._tmp_fstab)

    def cleanup(self):
        self._clear_fstab()
        self._lvm.destroy()

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)
        self._lvm = LvmLoopBacked(self.volumes, thin_volumes=self.thin_volumes)
        snapset_name = self.snapset_name
        snapset_time = self.snapset_time
        for origin, mp in self.mount_volumes:
            self._lvm.create_snapshot(
                origin,
                format_snapshot_name(
                    origin, snapset_name, snapset_time, encode_mount_point(mp)
                ),
            )
        self._set_fstab()
        self.addCleanup(self.cleanup)

    def tearDown(self):
        log.debug("Tearing down %s", self._testMethodName)

    def test_null(self):
        self.assertTrue(True)
