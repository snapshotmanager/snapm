# Copyright Red Hat
#
# tests/test_boot.py - Boot support tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
import tempfile
import logging
import os
from subprocess import run
from shutil import rmtree
from uuid import UUID

log = logging.getLogger()

import snapm
import snapm.manager._boot as boot
import snapm.manager
from snapm.manager.plugins import format_snapshot_name, encode_mount_point
import boom

from tests import have_root, BOOT_ROOT_TEST
from ._util import LvmLoopBacked

ETC_FSTAB = "/etc/fstab"
TMP_FSTAB = "/tmp/fstab"

_VAR_TMP = "/var/tmp"


def is_redhat():
    # Exclude CentOS for now until the fix for boom-boot #59 is available
    # in the CentOS repositories.
    return (os.path.exists("/etc/redhat-release")
            and not os.path.exists("/etc/centos-release"))


class BootTestsSimple(unittest.TestCase):
    """
    Test boot helpers
    """

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)

    def tearDown(self):
        log.debug("Tearing down %s", self._testMethodName)

    def test__get_uts_release(self):
        uname_cmd_args = ["uname", "-r"]
        uname_cmd = run(uname_cmd_args, capture_output=True, check=True)
        sys_uts_release = uname_cmd.stdout.decode("utf8").strip()
        self.assertEqual(sys_uts_release, boot._get_uts_release())

    def test__get_machine_id(self):
        with open("/etc/machine-id", "r", encoding="utf8") as id_file:
            sys_machine_id = id_file.read().strip()
        self.assertEqual(sys_machine_id, boot._get_machine_id())


@unittest.skipIf(not have_root(), "requires root privileges")
class BootTestsBase(unittest.TestCase):
    """
    Base class for boot tests.
    """
    volumes = ["root", "home", "var"]
    thin_volumes = ["opt", "srv"]
    boot_volumes = [
        ("root", "/"),
        ("home", "/home"),
        ("var", "/var"),
    ]
    snapset_name = "bootset0"
    snapset_time = 1707923080
    with_root = True

    def _set_fstab(self):
        """
        Set up a fake /etc/fstab to be used for the duration of the test run.
        """
        with open(TMP_FSTAB, "w", encoding="utf8") as file:
            file.write("# Test fstab\n")
            for origin, mp in self.boot_volumes:
                file.write(f"/dev/test_vg0/{origin}\t{mp}\text4\tdefaults 0 0\n")
        run(["mount", "--bind", TMP_FSTAB, ETC_FSTAB], check=True)

    def _clear_fstab(self):
        """
        Remove the fake /etc/fstab.
        """
        run(["umount", ETC_FSTAB], check=True)
        os.unlink(TMP_FSTAB)

    def _populate_boom_root_path(self):
        """
        Populate a mock /boot directory for testing.
        """
        boot_dir = tempfile.mkdtemp("_snapm_boom_dir", dir=_VAR_TMP)
        boom_dir = os.path.join(boot_dir, "boom")
        os.makedirs(os.path.join(boot_dir, "loader", "entries"), exist_ok=True)

        subdirs = ["cache", "hosts", "profiles"]
        for subdir in subdirs:
            os.makedirs(os.path.join(boom_dir, subdir), exist_ok=True)

        boom_conf = [
            "[global]\n",
            f"boot_root = {boot_dir}\n",
            "boom_root = %(boot_root)s/boom\n",
            "[legacy]\n",
            "enable = False\n",
            "format = grub1\n",
            "sync = False\n",
            "enable = True\n",
            "auto_clean = True\n",
            "cache_path = %(boom_root)/cache\n",
        ]
        with open(os.path.join(boom_dir, "boom.conf"), "w", encoding="utf8") as file:
            file.writelines(boom_conf)

        # Create fake kernel and initramfs for current uname -r
        version = os.uname()[2]
        with open(
            os.path.join(boot_dir, f"vmlinuz-{version}"), "w", encoding="utf8"
        ) as file:
            file.write("test\n")
        with open(
            os.path.join(boot_dir, f"initramfs-{version}.img"), "w", encoding="utf8"
        ) as file:
            file.write("test\n")

        return boot_dir

    def _cleanup_boom_root_path(self, boot_path):
        boom.set_boot_path(BOOT_ROOT_TEST)
        boom.osprofile.load_profiles()
        boom.bootloader.load_entries()
        rmtree(boot_path)

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)
        boom.set_boot_path("/boot")
        self._lvm = LvmLoopBacked(self.volumes, thin_volumes=self.thin_volumes)
        snapset_name = "bootset0"
        snapset_time = 1707923080
        for origin, mp in self.boot_volumes:
            if origin == "root" and not self.with_root:
                continue
            self._lvm.create_snapshot(
                origin,
                format_snapshot_name(
                    origin, snapset_name, snapset_time, encode_mount_point(mp)
                ),
            )
        self.manager = snapm.manager.Manager()
        self._set_fstab()

    def tearDown(self):
        log.debug("Tearing down %s", self._testMethodName)
        boom.set_boot_path(BOOT_ROOT_TEST)
        self._clear_fstab()
        self._lvm.destroy()


@unittest.skipIf(not have_root(), "requires root privileges")
class BootTestsNonRoot(BootTestsBase):

    with_root = False

    def test_create_snapshot_boot_entry(self):
        self.manager.create_snapshot_set_boot_entry(name=self.snapset_name)

        # Clean up boot entry
        self.manager.delete_snapshot_sets(snapm.Selection(name=self.snapset_name))


@unittest.skipIf(not have_root(), "requires root privileges")
class BootTestsWithRoot(BootTestsBase):

    with_root = True

    def test_create_snapshot_boot_entry_no_id(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError):
            self.manager.create_snapshot_set_boot_entry()

    def test_create_snapshot_revert_entry_no_id(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError):
            self.manager.create_snapshot_set_revert_entry()

    def test_create_snapshot_boot_entry_bad_name(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.create_snapshot_set_boot_entry(name="bootset1")

    def test_create_snapshot_revert_entry_bad_name(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.create_snapshot_set_revert_entry(name="bootset1")

    def test_create_snapshot_boot_entry(self):
        self.manager.create_snapshot_set_boot_entry(name="bootset0")

        # Clean up boot entry
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    def test_create_snapshot_boot_entry_dupe_raises(self):
        self.manager.create_snapshot_set_boot_entry(name="bootset0")

        with self.assertRaises(snapm.SnapmExistsError) as cm:
            self.manager.create_snapshot_set_boot_entry(name="bootset0")

        # Clean up boot entry
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    def test_create_snapshot_revert_entry(self):
        self.manager.create_snapshot_set_revert_entry(name="bootset0")

        # Clean up revert entry
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    def test_create_snapshot_revert_entry_dupe_raises(self):
        self.manager.create_snapshot_set_revert_entry(name="bootset0")

        with self.assertRaises(snapm.SnapmExistsError) as cm:
            self.manager.create_snapshot_set_revert_entry(name="bootset0")

        # Clean up revert entry
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    def test_create_snapshot_boot_entry_bad_uuid(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.create_snapshot_set_boot_entry(
                uuid=UUID("00000000-0000-0000-0000-000000000000")
            )

    def test_create_snapshot_revert_entry_bad_uuid(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.create_snapshot_set_revert_entry(
                uuid=UUID("00000000-0000-0000-0000-000000000000")
            )

    def test_create_snapshot_boot_entry_uuid(self):
        sset = self.manager.find_snapshot_sets(snapm.Selection(name="bootset0"))[0]
        self.manager.create_snapshot_set_boot_entry(uuid=sset.uuid)

        # Clean up boot entry
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    def test_create_snapshot_revert_entry_uuid(self):
        sset = self.manager.find_snapshot_sets(snapm.Selection(name="bootset0"))[0]
        self.manager.create_snapshot_set_revert_entry(uuid=sset.uuid)

        # Clean up revert entry
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    def test_create_boot_entries_and_discovery(self):
        sset = self.manager.find_snapshot_sets(snapm.Selection(name="bootset0"))[0]
        self.manager.create_snapshot_set_boot_entry(name="bootset0")
        self.manager.create_snapshot_set_revert_entry(name="bootset0")

        # Re-discover snapshot sets w/attached boot entries
        self.manager.discover_snapshot_sets()
        sset = self.manager.find_snapshot_sets(snapm.Selection(name="bootset0"))[0]

        # Validate boot entry
        boot_entry = sset.boot_entry
        for snapshot in sset.snapshots:
            self.assertIn(snapshot.devpath, boot_entry.options)

        # Validate revert entry
        revert_entry = sset.revert_entry
        root_snapshot = sset.snapshot_by_mount_point("/")
        self.assertIn(root_snapshot.origin, revert_entry.options)

        self.assertTrue(sset.boot_entry.boot_id[0:7] in str(sset))
        self.assertTrue(sset.revert_entry.boot_id[0:7] in str(sset))

        # Clean up boot entries
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    @unittest.skipIf(not is_redhat(), "profile auto-creation not supported")
    def test_auto_profile_create_boot_entry(self):
        boot_dir = self._populate_boom_root_path()
        boom.set_boot_path(boot_dir)
        boom.osprofile.load_profiles()
        boom.bootloader.load_entries()
        self.addCleanup(self._cleanup_boom_root_path, boot_dir)
        sset = self.manager.find_snapshot_sets(snapm.Selection(name="bootset0"))[0]
        self.manager.create_snapshot_set_boot_entry(uuid=sset.uuid)

        # Clean up boot entry
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    def test_create_boot_entries_and_rename(self):
        sset = self.manager.find_snapshot_sets(snapm.Selection(name="bootset0"))[0]
        self.manager.create_snapshot_set_boot_entry(name="bootset0")
        self.manager.create_snapshot_set_revert_entry(name="bootset0")

        self.assertIsNotNone(sset.boot_entry)
        self.assertIsNotNone(sset.revert_entry)

        # Stash original boot_id values
        orig_boot_id = sset.boot_entry.boot_id
        orig_revert_id = sset.revert_entry.boot_id

        self.manager.rename_snapshot_set("bootset0", "bootset1")

        self.assertIsNotNone(sset.boot_entry)
        self.assertIsNotNone(sset.revert_entry)

        # Verify boot_id values are changed
        self.assertNotEqual(orig_boot_id, sset.boot_entry.boot_id)
        self.assertNotEqual(orig_revert_id, sset.revert_entry.boot_id)

        # Clean up boot entries
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset1"))
