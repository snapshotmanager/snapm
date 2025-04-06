# Copyright Red Hat
#
# tests/test_plugin.py - Plugin core unit tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: GPL-2.0-only
import unittest
import logging

log = logging.getLogger()

import snapm.manager.plugins as plugins


def _find_device_mounts():
    """
    Find mount points on the running system backed by a device in /dev.

    :returns: A list of device-backed mount points.
    """
    mount_points = []
    with open("/proc/mounts", "r", encoding="utf8") as mounts:
        for line in mounts.readlines():
            (device, mount_point, _) = line.split(maxsplit=2)
            if device.startswith("/dev"):
                mount_points.append(mount_point)
    return mount_points


class PluginTests(unittest.TestCase):
    """Test plugin helper functions"""

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)

    def tearDown(self):
        log.debug("Tearing down %s", self._testMethodName)

    def test_plugin_info(self):
        p = plugins.Plugin(log)
        info = p.info()
        self.assertEqual(info, {"name": "plugin", "version": "0.1.0"})

    def _plugin_base_not_implemented_raises(self, method, args):
        p = plugins.Plugin(log)
        with self.assertRaises(NotImplementedError):
            getattr(p, method)(*args)

    def test_plugin_base_discover_snapshots_raises(self):
        args = []
        self._plugin_base_not_implemented_raises("discover_snapshots", args)

    def test_plugin_base_can_snapshot_raises(self):
        args = ["/mount"]
        self._plugin_base_not_implemented_raises("can_snapshot", args)

    def test_plugin_base_check_create_snapshot_raises(self):
        args = [
            "fedora/root",
            "testset0",
            1693921253,
            "/",
            None,
        ]
        self._plugin_base_not_implemented_raises("check_create_snapshot", args)

    def test_plugin_base_create_snapshot_raises(self):
        args = [
            "fedora/root",
            "testset0",
            1693921253,
            "/",
            None,
        ]
        self._plugin_base_not_implemented_raises("create_snapshot", args)

    def test_plugin_base_rename_snapshot_raises(self):
        args = [
            "old_name",
            "fedora/root",
            "testset0",
            1693921253,
            "/",
        ]
        self._plugin_base_not_implemented_raises("rename_snapshot", args)

    def test_plugin_base_delete_snapshot_raises(self):
        args = ["aname"]
        self._plugin_base_not_implemented_raises("delete_snapshot", args)

    def test_plugin_base_activate_snapshot_raises(self):
        args = ["aname"]
        self._plugin_base_not_implemented_raises("activate_snapshot", args)

    def test_plugin_base_deactivate_snapshot_raises(self):
        args = ["aname"]
        self._plugin_base_not_implemented_raises("deactivate_snapshot", args)

    def test_plugin_base_set_autoactivate_raises(self):
        args = ["aname"]
        self._plugin_base_not_implemented_raises("set_autoactivate", args)

    def test_plugin_base_origin_from_mount_point_raises(self):
        args = ["/mount"]
        self._plugin_base_not_implemented_raises("origin_from_mount_point", args)

    def test_plugin_base_check_resize_snapshot_raises(self):
        args = [
            "aname",
            "/dev/fedora/aname-orig",
            "/mount",
            "1G",
        ]
        self._plugin_base_not_implemented_raises("check_resize_snapshot", args)

    def test_plugin_base_resize_snapshot_raises(self):
        args = [
            "aname",
            "/dev/fedora/aname-orig",
            "/mount",
            "1G",
        ]
        self._plugin_base_not_implemented_raises("resize_snapshot", args)

    def test_plugin_base_check_revert_snapshot_raises(self):
        args = [
            "aname",
            "/dev/fedora/aname-orig",
        ]
        self._plugin_base_not_implemented_raises("check_revert_snapshot", args)

    def test_plugin_base_revert_snapshot_raises(self):
        args = ["aname"]
        self._plugin_base_not_implemented_raises("revert_snapshot", args)

    def test_parse_snapshot_name(self):
        snapshot_names = {
            "root-snapset_backup_1693921253_-": ("backup", 1693921253, "/"),
            "data-snapset_backup_1693921253_-data": (
                "backup",
                1693921253,
                "/data",
            ),
            "home-snapset_myset_1693921266_-home": (
                "myset",
                1693921266,
                "/home",
            ),
            "srv-snapset_newset_1694704292_-srv": ("newset", 1694704292, "/srv"),
        }
        for snapshot_name in snapshot_names.keys():
            (origin, _) = snapshot_name.split("-", maxsplit=1)
            self.assertEqual(
                plugins.parse_snapshot_name(snapshot_name, origin),
                snapshot_names[snapshot_name],
            )

    def test_parse_snapshot_name_none(self):
        snapshot_names = {
            "some_snapshot_volume": "origin_volume",
            "somesnapshot": "some",
            "some_snapshot": "some",
            "root-notsnapset_backup_1693921253_-": "root",
        }
        for name, origin in snapshot_names.items():
            self.assertEqual(None, plugins.parse_snapshot_name(name, origin))

    def test_device_from_mount_point(self):
        mounts = _find_device_mounts()
        for mount in mounts:
            dev = plugins.device_from_mount_point(mount)
            self.assertTrue(dev.startswith("/dev"))

    def test_device_from_mount_point_bad_mount(self):
        with self.assertRaises(KeyError) as cm:
            dev = plugins.device_from_mount_point("/quuxfoo")

    def test_mount_point_space_used(self):
        used = plugins.mount_point_space_used("/sys")
        self.assertEqual(0, used)

    def test_encode_mount_point(self):
        mounts = {
            "/": "-",
            "/home": "-home",
            "/var": "-var",
            "/opt": "-opt",
            "/var/tmp": "-var-tmp",
            "/var/foo-bar": "-var-foo--bar",
            "/foo_bar": "-foo_bar",
            "/data:storage": "-data.3astorage",
            "/data.storage": "-data..storage",
        }
        for mount in mounts.keys():
            self.assertEqual(plugins.encode_mount_point(mount), mounts[mount])

    def test_decode_mount_point(self):
        enc_mounts = {
            "-": "/",
            "-home": "/home",
            "-var": "/var",
            "-opt": "/opt",
            "-var-tmp": "/var/tmp",
            "-var-foo--bar": "/var/foo-bar",
            "-foo_bar": "/foo_bar",
            "-data.3astorage": "/data:storage",
            "-data..storage" : "/data.storage",
        }
        for enc_mount in enc_mounts.keys():
            self.assertEqual(
                plugins.decode_mount_point(enc_mount), enc_mounts[enc_mount]
            )

    def test_format_snapshot_name(self):
        snapshot_parts = {
            (
                "data",
                "backup",
                1693921253,
                "/data",
            ): "data-snapset_backup_1693921253_-data",
            (
                "home",
                "myset",
                1693921266,
                "/home",
            ): "home-snapset_myset_1693921266_-home",
            (
                "srv",
                "newset",
                1694704292,
                "/srv",
            ): "srv-snapset_newset_1694704292_-srv",
        }
        for parts in snapshot_parts.keys():
            mount = plugins.encode_mount_point(parts[3])
            self.assertEqual(
                plugins.format_snapshot_name(parts[0], parts[1], parts[2], mount),
                snapshot_parts[parts],
            )

    def test__parse_proc_mounts_line(self):
        from snapm.manager.plugins._plugin import _parse_proc_mounts_line

        lines = {
            "/dev/mapper/fedora-root / xfs rw": (
                "/dev/mapper/fedora-root",
                "/",
            ),
            "/dev/mapper/fedora-data /data xfs rw": (
                "/dev/mapper/fedora-data",
                "/data",
            ),
            "/dev/vda2 /boot xfs rw": ("/dev/vda2", "/boot"),
        }
        for line in lines:
            self.assertEqual(_parse_proc_mounts_line(line), lines[line])
