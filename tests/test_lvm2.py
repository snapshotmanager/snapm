# Copyright Red Hat
#
# tests/test_lvm2.py - LVM2 plugin unit tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
import logging
import os.path
import os

from configparser import ConfigParser

log = logging.getLogger()

import snapm.manager.plugins.lvm2 as lvm2
from snapm import SnapmCalloutError

from tests import have_root
from ._util import LvmLoopBacked


class Lvm2TestsSimple(unittest.TestCase):
    """Unit test lvm2 plugin functions with mock tool output"""

    _old_path = None

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)
        def cleanup():
            log.debug("Cleaning up (%s)", self._testMethodName)
            if hasattr(self, "_old_path"):
                os.environ["PATH"] = self._old_path

        self.addCleanup(cleanup)

        bin_path = os.path.abspath("tests/bin")
        cur_path = os.environ["PATH"]
        self._old_path = cur_path
        os.environ["PATH"] = bin_path + os.pathsep + cur_path

    def test__round_up_extents(self):
        # ((size_bytes, extent_size, expected), ...)
        test_values = (
            # Zero always rounds to zero.
            (0, 524288, 0),
            (0, 1048576, 0),
            (0, 4194304, 0),
            (0, 8388608, 0),
            (0, 16777216, 0),
            # An extent size of 1 always rounds to the identity.
            (12345, 1, 12345),
            (67890, 1, 67890),
            (314159265359, 1, 314159265359),
            # Random sizes and extent sizes with expected rounded value.
            (403471635, 524288, 403701760),
            (163534215, 1048576, 163577856),
            (255837780, 16777216, 268435456),
            (114549255, 131072, 114556928),
            (306983115, 131072, 307101696),
            (66724725, 524288, 67108864),
            (14159715, 16777216, 16777216),
            (401138430, 8388608, 402653184),
            (248862855, 131072, 248905728),
            (153398970, 131072, 153485312),
            (147485715, 524288, 147849216),
            (248899890, 1048576, 249561088),
            (41343405, 131072, 41418752),
        )
        for size, extent_size, expected in test_values:
            with self.subTest(size=size, extent_size=extent_size, expected=expected):
                log.debug("Testing _round_up_extents(%d,  %d) -> %d", size, extent_size, expected)
                self.assertEqual(expected, lvm2._round_up_extents(size, extent_size))

    def test__round_up_extents_zero_extent_size_raises(self):
        with self.assertRaises(ValueError):
            lvm2._round_up_extents(1048576, 0)

    def test__round_up_extents_negative_extent_size_raises(self):
        with self.assertRaises(ValueError):
            lvm2._round_up_extents(1048576, -1024)

    def test__round_up_extents_negative_size_raises(self):
        with self.assertRaises(ValueError):
            lvm2._round_up_extents(-4096, 1048576)

    def test_lvm2cow_is_lvm_device(self):
        lvm2cow = lvm2.Lvm2Cow(log, ConfigParser())
        devs = {
            "/dev/mapper/fedora-home": True,
            "/dev/mapper/fedora-root": True,
            "/dev/mapper/fedora-var": True,
            "/dev/mapper/stratis-1-1c7c941a2dba4eb78d57d3fb01aacc61-thin-fs-202ea1667fa54123bd24f0c353b9914c": False,
            "/dev/mapper/mpatha": False,
        }
        for dev in devs.keys():
            if not os.path.exists(dev):
                continue
            self.assertEqual(lvm2cow._is_lvm_device(dev), devs[dev])

    def test_lvm2thin_is_lvm_device(self):
        lvm2thin = lvm2.Lvm2Cow(log, ConfigParser())
        devs = {
            "/dev/mapper/fedora-home": True,
            "/dev/mapper/fedora-root": True,
            "/dev/mapper/fedora-var": True,
            "/dev/mapper/stratis-1-1c7c941a2dba4eb78d57d3fb01aacc61-thin-fs-202ea1667fa54123bd24f0c353b9914c": False,
            "/dev/mapper/mpatha": False,
        }
        for dev in devs.keys():
            if not os.path.exists(dev):
                continue
            self.assertEqual(lvm2thin._is_lvm_device(dev), devs[dev])

    def test_vg_lv_from_device_path(self):
        lvm2cow = lvm2.Lvm2Cow(log, ConfigParser())
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
                self.assertEqual(lvm2cow.vg_lv_from_device_path(dev), devs[dev])
            else:
                with self.assertRaises(SnapmCalloutError) as cm:
                    lvm2cow.vg_lv_from_device_path(dev)

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
        lvm2thin = lvm2.Lvm2Thin(log, ConfigParser())
        devs = {
            "fedora/srv": "pool0",
            "fedora/home": "",
        }
        for dev in devs.keys():
            self.assertEqual(lvm2thin.pool_name_from_vg_lv(dev), devs[dev])

    def test_pool_name_from_vg_lv_bad_lv(self):
        lvm2thin = lvm2.Lvm2Thin(log, ConfigParser())
        with self.assertRaises(SnapmCalloutError) as cm:
            lvm2thin.pool_name_from_vg_lv("some/lv")

    def test_pool_free_space(self):
        lvm2thin = lvm2.Lvm2Thin(log, ConfigParser())
        pools = {
            ("fedora", "pool0"): 933940639,
            ("fedora", "pool1"): 1073741824,
            ("fedora", "pool2"): None,
        }
        for pool in pools.keys():
            if pools[pool] is not None:
                self.assertEqual(lvm2thin.pool_free_space(pool[0], pool[1]), pools[pool])
            else:
                with self.assertRaises(SnapmCalloutError) as cm:
                    lvm2thin.pool_free_space(pool[0], pool[1])

    def test_vg_free_space(self):
        lvm2cow = lvm2.Lvm2Cow(log, ConfigParser())
        groups = {
            "fedora": (9097445376, 4096 * 1024),
            "vg_hex": (16903045120, 4096 * 1024),
            "appdata": (0, 4096 * 1024),
            "nosuch": (-1, 0),
        }
        for vg in groups.keys():
            if groups[vg][0] != -1:
                self.assertEqual(lvm2cow.vg_free_space(vg), groups[vg])
            else:
                with self.assertRaises(SnapmCalloutError) as cm:
                    lvm2cow.vg_free_space(vg)

    def test_lvm2cow_discover_snapshots(self):
        lvm2cow = lvm2.Lvm2Cow(log, ConfigParser())
        snapshots = lvm2cow.discover_snapshots()
        # FIXME: hardcoded value based on test data
        self.assertEqual(len(snapshots), 11)

    def test_lvm2thin_discover_snapshots(self):
        lvm2thin = lvm2.Lvm2Thin(log, ConfigParser())
        snapshots = lvm2thin.discover_snapshots()
        # FIXME: hardcoded value based on test data
        self.assertEqual(len(snapshots), 5)


@unittest.skipIf(not have_root(), "requires root privileges")
class Lvm2Tests(unittest.TestCase):
    """
    Test command interfaces with devices
    """

    volumes = ["root"]
    thin_volumes = ["opt"]

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)
        def cleanup_lvm():
            log.debug("Cleaning up LVM (%s)", self._testMethodName)
            if hasattr(self, "_lvm"):
                self._lvm.destroy()

        self.addCleanup(cleanup_lvm)

        self._lvm = LvmLoopBacked(self.volumes, thin_volumes=self.thin_volumes)

    def test_lvm2cow_create_delete_origin_accounting(self):
        # Verify that origin snapshot counters are properly updated around
        # create/delete operations.

        # Plugin setup
        lvm2cow_plugin = lvm2.Lvm2Cow(log, ConfigParser())
        snapshots = lvm2cow_plugin.discover_snapshots()

        # Create snapshot via Plugin.create_snapshot()
        lvm2cow_plugin.start_transaction()
        lvm2cow_plugin.check_create_snapshot("test_vg0/root", "test", 1721136677, "/", "1%SIZE")
        lvm2cow_plugin.create_snapshot("test_vg0/root", "test", 1721136677, "/", "1%SIZE")
        lvm2cow_plugin.end_transaction()

        # Verify counter set to one
        origin_count = lvm2cow_plugin.origins["/dev/test_vg0/root"]
        self.assertEqual(origin_count, 1)

        # Delete snapshot & verify decrement
        lvm2cow_plugin.delete_snapshot("test_vg0/root-snapset_test_1721136677_-")
        origin_count = lvm2cow_plugin.origins["/dev/test_vg0/root"]
        self.assertEqual(origin_count, 0)

    def test_lvm2thin_create_delete_pool_accounting(self):
        # Verify that pool snapshot counters are properly updated around
        # create/delete operations.

        # Plugin setup
        lvm2thin_plugin = lvm2.Lvm2Thin(log, ConfigParser())
        snapshots = lvm2thin_plugin.discover_snapshots()

        # Create snapshot via Plugin.create_snapshot()
        lvm2thin_plugin.start_transaction()
        lvm2thin_plugin.check_create_snapshot("test_vg0/opt", "test", 1721136677, "/opt", "1%SIZE")
        lvm2thin_plugin.create_snapshot("test_vg0/opt", "test", 1721136677, "/opt", "1%SIZE")
        lvm2thin_plugin.end_transaction()

        # Verify counter incremented
        pool0_count = lvm2thin_plugin.pools["test_vg0/pool0"]
        self.assertEqual(pool0_count, 1)

        # Delete snapshot & verify decrement
        lvm2thin_plugin.delete_snapshot("test_vg0/opt-snapset_test_1721136677_-opt")
        pool0_count = lvm2thin_plugin.pools["test_vg0/pool0"]
        self.assertEqual(pool0_count, 0)
