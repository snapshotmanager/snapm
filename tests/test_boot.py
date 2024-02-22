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
import logging
import os
from subprocess import run

log = logging.getLogger()
log.level = logging.DEBUG
log.addHandler(logging.FileHandler("test.log"))

import snapm
import snapm.manager.boot as boot
import snapm.manager
from snapm.manager.plugins import format_snapshot_name, encode_mount_point

from ._util import LvmLoopBacked


class BootTestsSimple(unittest.TestCase):
    """
    Test boot helpers
    """
    def test__get_uts_release(self):
        uname_cmd_args = ["uname", "-r"]
        uname_cmd = run(uname_cmd_args, capture_output=True, check=True)
        sys_uts_release = uname_cmd.stdout.decode('utf8').strip()
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

    def setUp(self):
        self._lvm = LvmLoopBacked(self.volumes, thin_volumes=self.thin_volumes)
        snapset_name = "bootset0"
        snapset_time = 1707923080
        boot_volumes = [
            ("root", "/"),
            ("home", "/home"),
            ("var", "/var"),
        ]
        for origin, mp in boot_volumes:
            self._lvm.create_snapshot(origin, format_snapshot_name(origin, snapset_name, snapset_time, encode_mount_point(mp)))
        self.manager = snapm.manager.Manager()

    def tearDown(self):
        self._lvm.destroy()

    def test_create_snapshot_boot_entry(self):
        self.manager.create_snapshot_set_boot_entry(name="bootset0")
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

    def test_create_snapshot_rollback_entry(self):
        sset = self.manager.find_snapshot_sets(snapm.Selection(name="bootset0"))[0]
        self.manager.create_snapshot_set_rollback_entry(name="bootset0")
        print(f"Deleting boot entry {sset.rollback_entry}")
        self.manager.delete_snapshot_sets(snapm.Selection(name="bootset0"))

