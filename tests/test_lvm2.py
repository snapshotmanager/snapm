# Copyright (C) 2023 Red Hat, Inc., Bryn M. Reeves <bmr@redhat.com>
#
# tests/test_lvm2.py - LVM2 plugin unit tests
#
# This file is part of the snapm project.
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions
# of the GNU General Public License v.2.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
import unittest
import logging
import os.path
import os

log = logging.getLogger()
log.level = logging.DEBUG
log.addHandler(logging.FileHandler("test.log"))

import snapm.manager.plugins.lvm2 as lvm2
from snapm import SnapmCalloutError


class Lvm2Tests(unittest.TestCase):
    """Test lvm2 plugin functions"""

    _old_path = None

    def setUp(self):
        def cleanup():
            if hasattr(self, "_old_path"):
                os.environ["PATH"] = self._old_path

        self.addCleanup(cleanup)

        bin_path = os.path.abspath("tests/bin")
        cur_path = os.environ["PATH"]
        self._old_path = cur_path
        os.environ["PATH"] = bin_path + os.pathsep + cur_path

    def test_is_lvm_device(self):
        devs = {
            "/dev/mapper/fedora-home": True,
            "/dev/mapper/fedora-root": True,
            "/dev/mapper/fedora-var": True,
            "/dev/mapper/stratis-1-1c7c941a2dba4eb78d57d3fb01aacc61-thin-fs-202ea1667fa54123bd24f0c353b9914c": False,
            "/dev/mapper/mpatha": False,
        }
        for dev in devs.keys():
            self.assertEqual(lvm2.is_lvm_device(dev), devs[dev])

    def test_vg_lv_from_device_path(self):
        devs = {
            "/dev/mapper/fedora-home": ("fedora", "home"),
            "/dev/mapper/fedora-root": ("fedora", "root"),
            "/dev/mapper/fedora-var": ("fedora", "var"),
            "/dev/mapper/appdata-datavol1--test": ("appdata", "datavol1-test"),
            "/dev/mapper/vg_test0-lv_test0": ("vg_test0", "lv_test0"),
            "/dev/mapper/notadev": None,
            "/dev/fedora/home": ("fedora", "home"),
            "/dev/fedora/root": ("fedora", "root"),
            "/dev/fedora/var": ("fedora", "var"),
            "/dev/appdata/datavol1-test": ("appdata", "datavol1-test"),
            "/dev/vg_test0/lv_test0": ("vg_test0", "lv_test0"),
            "/dev/not/a/dev": None,
        }
        for dev in devs.keys():
            if devs[dev] is not None:
                self.assertEqual(lvm2.vg_lv_from_device_path(dev), devs[dev])
            else:
                with self.assertRaises(SnapmCalloutError) as cm:
                    lvm2.vg_lv_from_device_path(dev)

    def test_vg_lv_from_origin(self):
        devs = {
            "/dev/fedora/root": ("fedora", "root"),
            "/dev/fedora/home": ("fedora", "home"),
            "/dev/rhel/var": ("rhel", "var"),
            "/dev/vg00/lvol00": ("vg00", "lvol00"),
        }
        for dev in devs.keys():
            self.assertEqual(lvm2.vg_lv_from_origin(dev), devs[dev])

    def test_pool_name_from_vg_lv(self):
        devs = {
            "fedora/srv": "pool0",
            "fedora/home": "",
        }
        for dev in devs.keys():
            self.assertEqual(lvm2.pool_name_from_vg_lv(dev), devs[dev])

    def test_pool_name_from_vg_lv_bad_lv(self):
        with self.assertRaises(SnapmCalloutError) as cm:
            lvm2.pool_name_from_vg_lv("some/lv")

    def test_pool_free_space(self):
        pools = {
            ("fedora", "pool0"): 933940639,
            ("fedora", "pool1"): 1073741824,
            ("fedora", "pool2"): None,
        }
        for pool in pools.keys():
            if pools[pool] is not None:
                self.assertEqual(lvm2.pool_free_space(pool[0], pool[1]), pools[pool])
            else:
                with self.assertRaises(SnapmCalloutError) as cm:
                    lvm2.pool_free_space(pool[0], pool[1])

    def test_vg_free_space(self):
        groups = {
            "fedora": 9097445376,
            "vg_hex": 16903045120,
            "appdata": 0,
            "nosuch": -1,
        }
        for vg in groups.keys():
            if groups[vg] != -1:
                self.assertEqual(lvm2.vg_free_space(vg), groups[vg])
            else:
                with self.assertRaises(SnapmCalloutError) as cm:
                    lvm2.vg_free_space(vg)

    def test_lvm2cow_discover_snapshots(self):
        lvm2cow = lvm2.Lvm2Cow(log)
        snapshots = lvm2cow.discover_snapshots()
        # FIXME: hardcoded value based on test data
        self.assertEqual(len(snapshots), 11)

    def test_lvm2thin_discover_snapshots(self):
        lvm2thin = lvm2.Lvm2Thin(log)
        snapshots = lvm2thin.discover_snapshots()
        # FIXME: hardcoded value based on test data
        self.assertEqual(len(snapshots), 5)
