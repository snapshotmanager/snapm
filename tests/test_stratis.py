# Copyright Red Hat
#
# tests/test_stratis.py - Stratis plugin unit tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
import logging
import os.path
import psutil
import os
from subprocess import run

from dbus_client_gen import DbusClientUniqueResultError

log = logging.getLogger()

from snapm.manager.plugins.stratislib import (
    get_object,
    TOP_OBJECT,
    ObjectManager,
)
from snapm import SnapmNotFoundError
import snapm.manager.plugins.stratis as stratis
from snapm.manager.plugins import device_from_mount_point

from tests import have_root
from ._util import StratisLoopBacked


def _systemctl_args(command):
    return [
        "systemctl",
        f"{command}",
        "stratisd"
    ]


class StratisTestsSimple(unittest.TestCase):
    """Test Stratis plugin functions"""

    _old_path = None

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)

        def cleanup():
            log.debug("Cleaning up (%s)", self._testMethodName)
            os.environ["PATH"] = self._old_path

        self.addCleanup(cleanup)

        bin_path = os.path.abspath("tests/bin")
        cur_path = os.environ["PATH"]
        self._old_path = cur_path
        os.environ["PATH"] = bin_path + os.pathsep + cur_path

    def test_is_stratis_device(self):
        devs = {
            "/dev/mapper/fedora-home": False,
            "/dev/mapper/fedora-root": False,
            "/dev/mapper/fedora-var": False,
            "/dev/mapper/stratis-1-1c7c941a2dba4eb78d57d3fb01aacc61-thin-fs-202ea1667fa54123bd24f0c353b9914c": True,
            "/dev/stratis/p1/fs1": True,
            "/dev/mapper/mpatha": False,
            "/dev/vda2": False,
            "/dev/quux": False,
        }
        for dev in devs.keys():
            self.assertEqual(stratis.is_stratis_device(dev), devs[dev])

    def pool_fs_from_origin(self):
        devs = {
            "/dev/stratis/p1/fs1": ("p1", "fs1"),
            "/dev/stratis/p1/fs2": ("p1", "fs2"),
            "/dev/stratis/pool0/fs0": ("pool0", "fs0"),
            "/dev/stratis/data/vol0": ("data", "vol0"),
        }
        for dev in devs.keys():
            self.assertEqual(stratis.pool_fs_from_origin(dev), devs[dev])

    def test_is_stratisd_running(self):
        running = False
        for proc in psutil.process_iter():
            try:
                if proc.name() == "stratisd":
                    running = True
            except psutil.NoSuchProcess:
                running = False
        self.assertEqual(running, stratis.is_stratisd_running())

    @unittest.skipIf(not have_root(), "requires root privileges")
    def test_is_stratisd_running_stopped(self):
        systemctl_stop_args = _systemctl_args("stop")
        run(systemctl_stop_args, check=True)
        self.assertEqual(False, stratis.is_stratisd_running())
        systemctl_start_args = _systemctl_args("start")
        run(systemctl_start_args, check=True)

    def test_stratis_devices_present(self):
        self.assertEqual(stratis.stratis_devices_present(), True)

    def test__snapshot_min_size(self):
        sizes = [
            (256 * 2**20, 512 * 2**20),  # 256MiB/512MiB
            (512 * 2**20, 512 * 2**20),  # 512MiB/512MiB
            (1 * 2**30, 1 * 2**30),      # 1GiB/1GiB
            (100 * 2**30, 100 * 2**30),  # 100GiB/100GiB
            (1 * 2**40, 1 * 2**40),      # 1TiB/1TiB
        ]
        for (size, xsize) in sizes:
            self.assertEqual(xsize, stratis._snapshot_min_size(size))


@unittest.skipIf(not have_root(), "requires root privileges")
class StratisTests(unittest.TestCase):

    stratis_volumes = ["fs1", "fs2"]

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)
        def cleanup():
            log.debug("Cleaning up Stratis (%s)", self._testMethodName)
            if hasattr(self, "_stratis"):
                self._stratis.destroy()

        self.addCleanup(cleanup)

        self._stratis = StratisLoopBacked(self.stratis_volumes)

    def test__get_pool_filesystem(self):
        proxy = get_object(TOP_OBJECT)
        managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})

        (pool, filesystem) = stratis._get_pool_filesystem(managed_objects, "pool1", "fs1")
        self.assertTrue(str(pool.Name()) == "pool1")
        self.assertTrue(str(filesystem.Name()) == "fs1")

    def test__get_pool_filesystem_bad_pool(self):
        proxy = get_object(TOP_OBJECT)
        managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})

        with self.assertRaises(DbusClientUniqueResultError) as cm:
            (pool, filesystem) = stratis._get_pool_filesystem(managed_objects, "nosuchpool1", "fs1")

    def test__get_pool_filesystem_bad_fs(self):
        proxy = get_object(TOP_OBJECT)
        managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})

        with self.assertRaises(DbusClientUniqueResultError) as cm:
            (pool, filesystem) = stratis._get_pool_filesystem(managed_objects, "pool1", "nosuchfs1")

    def test_pool_free_space_bytes(self):
        proxy = get_object(TOP_OBJECT)
        managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})

        (pool, filesystem) = stratis._get_pool_filesystem(managed_objects, "pool1", "fs1")
        free_bytes = stratis._pool_free_space_bytes(managed_objects, "pool1")
        self.assertEqual(
            int(pool.TotalPhysicalSize()) - int(pool.TotalPhysicalUsed()[1]) if pool.TotalPhysicalUsed()[0] else 0,
            free_bytes
        )

    def test_fs_size_bytes(self):
        proxy = get_object(TOP_OBJECT)
        managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})

        size_bytes = stratis._fs_size_bytes(managed_objects, "pool1", "fs1")
        self.assertEqual(size_bytes, 2**30)

    def test_pool_fs_from_device_path(self):
        devpath = device_from_mount_point(self._stratis.mount_points()[0])
        (pool, fs) = stratis.pool_fs_from_device_path(devpath)
        self.assertEqual(pool, "pool1")
        self.assertEqual(fs, "fs1")

    def test_stratis_discover_snapshots(self):
        self._stratis.create_snapshot("fs1", "fs1-snapset_test_1721136677_-opt")
        self._stratis.create_snapshot("fs2", "fs2-snapset_test_1721136677_-data")
        stratis_plugin = stratis.Stratis(log)
        snapshots = stratis_plugin.discover_snapshots()
        self.assertEqual(len(snapshots), 2)

    def test_stratis_can_snapshot_no_stratisd(self):
        systemctl_stop_args = _systemctl_args("stop")
        run(systemctl_stop_args, check=True)

        with self.assertRaises(SnapmNotFoundError) as cm:
            stratis_plugin = stratis.Stratis(log)

        systemctl_start_args = _systemctl_args("start")
        run(systemctl_start_args, check=True)

    def test__origin_uuid_to_fs_name(self):
        self._stratis.create_snapshot("fs1", "fs1-snapset_test_1721136677_-opt")

        proxy = get_object(TOP_OBJECT)
        managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})

        (pool, fs) = stratis._get_pool_filesystem(managed_objects, "pool1", "fs1-snapset_test_1721136677_-opt")

        managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})

        origin = stratis._origin_uuid_to_fs_name(
            managed_objects, fs.Pool(), str(fs.Origin()[1])
        )

        self.assertEqual(origin, "fs1")

    def test_filter_stratis_snapshot_snapshot(self):
        self._stratis.create_snapshot("fs1", "fs1-snapset_test_1721136677_-opt")

        proxy = get_object(TOP_OBJECT)
        managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})

        (pool, fs) = stratis._get_pool_filesystem(managed_objects, "pool1", "fs1-snapset_test_1721136677_-opt")
        self.assertEqual(True, stratis.filter_stratis_snapshot(fs))

    def test_filter_stratis_snapshot_nonsnapshot(self):
        proxy = get_object(TOP_OBJECT)
        managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})

        (pool, fs) = stratis._get_pool_filesystem(managed_objects, "pool1", "fs1")
        self.assertEqual(False, stratis.filter_stratis_snapshot(fs))
