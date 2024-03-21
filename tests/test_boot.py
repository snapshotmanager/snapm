# Copyright (C) 2024 Red Hat, Inc., Bryn M. Reeves <bmr@redhat.com>
#
# tests/test_boot.py - Boot support tests
#
# This file is part of the snapm project.
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions
# of the GNU General Public License v.2.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
import unittest
import tempfile
import logging
import os
from subprocess import run
from shutil import rmtree

log = logging.getLogger()
log.level = logging.DEBUG
log.addHandler(logging.FileHandler("test.log"))

import snapm
import snapm.manager.boot as boot
import snapm.manager
from snapm.manager.plugins import format_snapshot_name, encode_mount_point
import boom

from ._util import LvmLoopBacked

ETC_FSTAB = "/etc/fstab"
TMP_FSTAB = "/tmp/fstab"

_VAR_TMP = "/var/tmp"

class BootTestsSimple(unittest.TestCase):
    """
    Test boot helpers
    """

    def test__get_uts_release(self):
        uname_cmd_args = ["uname", "-r"]
        uname_cmd = run(uname_cmd_args, capture_output=True, check=True)
        sys_uts_release = uname_cmd.stdout.decode("utf8").strip()
        self.assertEqual(sys_uts_release, boot._get_uts_release())

    def test__get_machine_id(self):
        with open("/etc/machine-id", "r", encoding="utf8") as id_file:
            sys_machine_id = id_file.read().strip()
        self.assertEqual(sys_machine_id, boot._get_machine_id())


class BootTests(unittest.TestCase):
    """
    Test boot integration with devices
    """

    volumes = ["root", "home", "var"]
    thin_volumes = ["opt", "srv"]
    boot_volumes = [
        ("root", "/"),
        ("home", "/home"),
        ("var", "/var"),
    ]

    def _set_fstab(self):
        """
        Set up a fake /etc/fstab to be used for the duration of the test run.
        """
        with open(TMP_FSTAB, "w", encoding="utf8") as file:
            for origin, mp in self.boot_volumes:
                file.write(f"/dev/test_vg0/{origin}\t{mp}\text4\tdefaults 0 0\n")
            run(["mount", "--bind", TMP_FSTAB, ETC_FSTAB])

    def _clear_fstab(self):
        """
        Remove the fake /etc/fstab.
        """
        run(["umount", ETC_FSTAB])
        os.unlink(TMP_FSTAB)

    def _populate_boom_root_path(self):
        boot_dir = tempfile.mkdtemp("_snapm_boom_dir", dir=_VAR_TMP)
        boom_dir = os.path.join(boot_dir, "boom")
        os.makedirs(os.path.join(boot_dir, "loader", "entries"), exist_ok=True)
        subdirs = ["cache", "hosts", "profiles"]
        for subdir in subdirs:
            os.makedirs(os.path.join(boom_dir, subdir), exist_ok=True)
        boom_conf = [
            "[global]\n",
            f"boot_root = {boot_dir}\n",
            f"boom_root = %(boot_root)s/boom\n",
            "[legacy]\n",
            "enable = False\n",
            "format = grub1\n",
            "sync = False\n",
        ]
        with open(os.path.join(boom_dir, "boom.conf"), "w", encoding="utf8") as file:
            file.writelines(boom_conf)
        return boot_dir

    def _cleanup_boom_root_path(self, boot_path):
        boom.set_boot_path("/boot")
        boom.osprofile.load_profiles()
        boom.bootloader.load_entries()
        rmtree(boot_path)

    def setUp(self):
        self._lvm = LvmLoopBacked(self.volumes, thin_volumes=self.thin_volumes)
        snapset_name = "bootset0"
        snapset_time = 1707923080
        for origin, mp in self.boot_volumes:
            self._lvm.create_snapshot(
                origin,
                format_snapshot_name(
                    origin, snapset_name, snapset_time, encode_mount_point(mp)
                ),
            )
        self.manager = snapm.manager.Manager()
        self._set_fstab()

    def tearDown(self):
        self._clear_fstab()
        self._lvm.destroy()

    def test_create_snapshot_boot_entry_no_id(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.create_snapshot_set_boot_entry()

    def test_create_snapshot_rollback_entry_no_id(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.create_snapshot_set_rollback_entry()

    def test_create_snapshot_boot_entry_bad_name(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.create_snapshot_set_boot_entry(name="bootset1")

    def test_create_snapshot_rollback_entry_bad_name(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.create_snapshot_set_rollback_entry(name="bootset1")

    def test_create_snapshot_boot_entry(self):
        self.manager.create_snapshot_set_boot_entry(name="bootset0")

        # Clean up boot entry
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    def test_create_snapshot_rollback_entry(self):
        self.manager.create_snapshot_set_rollback_entry(name="bootset0")

        # Clean up rollback entry
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    def test_create_snapshot_boot_entry_bad_uuid(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.create_snapshot_set_boot_entry(
                uuid="00000000-0000-0000-0000-000000000000"
            )

    def test_create_snapshot_rollback_entry_bad_uuid(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.create_snapshot_set_rollback_entry(
                uuid="00000000-0000-0000-0000-000000000000"
            )

    def test_create_snapshot_boot_entry_uuid(self):
        sset = self.manager.find_snapshot_sets(snapm.Selection(name="bootset0"))[0]
        self.manager.create_snapshot_set_boot_entry(uuid=str(sset.uuid))

        # Clean up boot entry
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    def test_create_snapshot_rollback_entry_uuid(self):
        sset = self.manager.find_snapshot_sets(snapm.Selection(name="bootset0"))[0]
        self.manager.create_snapshot_set_rollback_entry(uuid=str(sset.uuid))

        # Clean up rollback entry
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    def test_create_boot_entries_and_discovery(self):
        sset = self.manager.find_snapshot_sets(snapm.Selection(name="bootset0"))[0]
        self.manager.create_snapshot_set_boot_entry(name="bootset0")
        self.manager.create_snapshot_set_rollback_entry(name="bootset0")

        # Re-discover snapshot sets w/attached boot entries
        self.manager.discover_snapshot_sets()
        sset = self.manager.find_snapshot_sets(snapm.Selection(name="bootset0"))[0]

        # Validate boot entry
        boot_entry = sset.boot_entry
        for snapshot in sset.snapshots:
            self.assertIn(snapshot.devpath, boot_entry.options)

        # Validate roll back entry
        rollback_entry = sset.rollback_entry
        root_snapshot = sset.snapshot_by_mount_point("/")
        self.assertIn(root_snapshot.origin, rollback_entry.options)

        # Clean up boot entries
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    def test_auto_profile_create_boot_entry(self):
        boot_dir = self._populate_boom_root_path()
        boom.set_boot_path(boot_dir)
        boom.osprofile.load_profiles()
        boom.bootloader.load_entries()
        self.addCleanup(self._cleanup_boom_root_path, boot_dir)
        sset = self.manager.find_snapshot_sets(snapm.Selection(name="bootset0"))[0]
        self.manager.create_snapshot_set_boot_entry(uuid=str(sset.uuid))

        # Clean up boot entry
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))
