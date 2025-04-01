# Copyright Red Hat
#
# tests/test_manager.py - Manager and plugin core unit tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: GPL-2.0-only
import unittest
import logging
import os
from uuid import UUID


import snapm
import snapm.manager as manager
import boom

from tests import have_root, BOOT_ROOT_TEST
from ._util import LvmLoopBacked, StratisLoopBacked


log = logging.getLogger()

boom.set_boot_path(BOOT_ROOT_TEST)


class ManagerTestsSimple(unittest.TestCase):
    """
    Test manager interfaces with mock callouts
    """

    _old_path = None

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)
        bin_path = os.path.abspath("tests/bin")
        cur_path = os.environ["PATH"]
        self._old_path = cur_path
        os.environ["PATH"] = bin_path + os.pathsep + cur_path

    def tearDown(self):
        log.debug("Tearing down %s", self._testMethodName)
        os.environ["PATH"] = self._old_path

    def test_manager(self):
        m = manager.Manager()
        self.assertEqual(len(m.snapshot_sets), 3)

    def test_find_snapshot_sets_all(self):
        m = manager.Manager()
        s = snapm.Selection()
        sets = m.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 3)

    def test_find_snapshots_all(self):
        m = manager.Manager()
        s = snapm.Selection()
        snaps = m.find_snapshots(selection=s)
        self.assertEqual(len(snaps), 16)

    def test_snapset_to_str(self):
        m = manager.Manager()
        sets = m.find_snapshot_sets()
        set_str = str(sets[0])
        self.assertTrue(isinstance(set_str, str))

    def test_snapshot_to_str(self):
        m = manager.Manager()
        snaps = m.find_snapshots()
        snap_str = str(snaps[0])
        self.assertTrue(isinstance(snap_str, str))

    def test_snapset_time(self):
        m = manager.Manager()
        s = snapm.Selection(name="backup")
        sets = m.find_snapshot_sets(selection=s)
        self.assertEqual(sets[0].time, "2023-09-05 13:40:53")

    def test_snapshot_time(self):
        m = manager.Manager()
        s = snapm.Selection(name="backup")
        sets = m.find_snapshot_sets(selection=s)
        snap = sets[0].snapshots[0]
        self.assertEqual(snap.time, "2023-09-05 13:40:53")

    def test_snapset_mount_points(self):
        m = manager.Manager()
        s = snapm.Selection(name="backup")
        sets = m.find_snapshot_sets(selection=s)
        self.assertEqual(sets[0].mount_points, ["/home", "/opt", "/", "/data", "/srv"])

    def test_snapset_find_by_mount_points(self):
        m = manager.Manager()
        s = snapm.Selection(mount_points=["/home", "/opt", "/", "/data", "/srv"])
        sets = m.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 2)

    def test_snapset_find_by_timestamp(self):
        m = manager.Manager()
        s = snapm.Selection(timestamp=1693921253)
        sets = m.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)

    def test_snapset_find_by_nr_snapshots(self):
        m = manager.Manager()
        s = snapm.Selection(nr_snapshots=5)
        sets = m.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 2)

    def test_load_plugins(self):
        from snapm.manager._manager import load_plugins, PluginRegistry

        load_plugins()
        self.assertEqual(len(PluginRegistry.plugins), 3)

    def test_plugin_info(self):
        p = manager.Plugin(log)
        info = p.info()
        self.assertEqual(info, {"name": "plugin", "version": "0.1.0"})

    def _plugin_base_not_implemented_raises(self, method, args):
        p = manager.Plugin(log)
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

    def test_plugins_find(self):
        from snapm.manager._manager import find as plugin_find

        xpaths = [
            "tests/find/a.find",
            "tests/find/b.find",
            "tests/find/subdir/c.find",
        ]
        paths = plugin_find("*.find", "tests/find")
        paths = [path for path in paths]
        self.assertEqual(sorted(paths), sorted(xpaths))


@unittest.skipIf(not have_root(), "requires root privileges")
class ManagerTests(unittest.TestCase):
    """
    Tests for snapm.manager.Manager that apply to all supported snapshot
    providers.
    """
    volumes = ["root", "home", "var", "data_vol"]
    thin_volumes = ["opt", "srv", "thin-vol"]
    stratis_volumes = ["fs1", "fs2"]

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)
        def cleanup_lvm():
            log.debug("Cleaning up LVM (%s)", self._testMethodName)
            if hasattr(self, "_lvm"):
                self._lvm.destroy()

        def cleanup_stratis():
            log.debug("Cleaning up Stratis (%s)", self._testMethodName)
            if hasattr(self, "_stratis"):
                self._stratis.destroy()

        self.addCleanup(cleanup_lvm)
        self.addCleanup(cleanup_stratis)

        self._lvm = LvmLoopBacked(self.volumes, thin_volumes=self.thin_volumes)
        self._stratis = StratisLoopBacked(self.stratis_volumes)

        self.manager = snapm.manager.Manager()

    def mount_points(self):
        return self._lvm.mount_points() + self._stratis.mount_points()

    def stop_start_storage(self):
        self._lvm.umount_all()
        self._lvm.deactivate()
        self._lvm.activate()
        self._lvm.mount_all()

        self._stratis.umount_all()
        self._stratis.stop_pool()
        self._stratis.start_pool()
        self._stratis.mount_all()

    def test_find_snapshot_sets_none(self):
        sets = self.manager.find_snapshot_sets()
        self.assertEqual([], sets)

    def test_find_snapshot_sets_one(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        sets = self.manager.find_snapshot_sets()
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_find_snapshot_sets_with_selection_name(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        self.manager.create_snapshot_set("testset1", self.mount_points())
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_find_snapshot_sets_with_selection_uuid(self):
        set1 = self.manager.create_snapshot_set("testset0", self.mount_points())
        self.manager.create_snapshot_set("testset1", self.mount_points())
        s = snapm.Selection(uuid=set1.uuid)
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_create_snapshot_set(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_blockdevs(self):
        snapset = self.manager.create_snapshot_set(
            "testset0", self._lvm.block_devs() + self._stratis.block_devs()
        )
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_blockdevs_unmounted(self):
        self._lvm.umount_all()
        self._stratis.umount_all()
        snapset = self.manager.create_snapshot_set(
            "testset0", self._lvm.block_devs() + self._stratis.block_devs()
        )
        self._lvm.mount_all()
        self._stratis.mount_all()
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_mixed_1(self):
        self._stratis.umount_all()
        snapset = self.manager.create_snapshot_set(
            "testset0", self._lvm.mount_points() + self._stratis.block_devs()
        )
        self._stratis.mount_all()
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_mixed_2(self):
        self._lvm.umount_all()
        snapset = self.manager.create_snapshot_set(
            "testset0", self._lvm.block_devs() + self._stratis.mount_points()
        )
        self._lvm.mount_all()
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_default_size_policy(self):
        self.manager.create_snapshot_set(
            "testset0", self.mount_points(), default_size_policy="10%FREE"
        )
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_size_policies_10_free(self):
        mount_specs = [f"{mp}:10%FREE" for mp in self.mount_points()]
        self.manager.create_snapshot_set("testset0", mount_specs)
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_size_policies_10_size(self):
        mount_specs = [f"{mp}:10%SIZE" for mp in self.mount_points()]
        self.manager.create_snapshot_set("testset0", mount_specs)
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_size_policies_200_used(self):
        mount_specs = [f"{mp}:200%USED" for mp in self.mount_points()]
        self.manager.create_snapshot_set("testset0", mount_specs)
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_size_policies_100_size(self):
        mount_specs = [f"{mp}:100%SIZE" for mp in self.mount_points()]
        self.manager.create_snapshot_set("testset0", mount_specs)
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_size_policies_fixed(self):
        mount_specs = [f"{mp}:512MiB" for mp in self.mount_points()]
        self.manager.create_snapshot_set("testset0", mount_specs)
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_size_policy_size_over_100_raises(self):
        with self.assertRaises(snapm.SnapmSizePolicyError) as cm:
            self.manager.create_snapshot_set(
                "testset0", self.mount_points(), default_size_policy="150%SIZE"
            )

    def test_create_snapshot_set_size_policy_free_over_100_raises(self):
        with self.assertRaises(snapm.SnapmSizePolicyError) as cm:
            self.manager.create_snapshot_set(
                "testset0", self.mount_points(), default_size_policy="150%FREE"
            )

    def test_create_snapshot_set_size_policy_bad_units_raises(self):
        with self.assertRaises(snapm.SnapmSizePolicyError) as cm:
            self.manager.create_snapshot_set(
                "testset0", self.mount_points(), default_size_policy="2FiB"
            )

    def test_create_snapshot_set_size_policy_non_num_raises(self):
        with self.assertRaises(snapm.SnapmSizePolicyError) as cm:
            self.manager.create_snapshot_set(
                "testset0", self.mount_points(), default_size_policy="quux"
            )

    def test_create_snapshot_set_size_policies_blockdev_used_raises(self):
        dev_specs = [f"{dev}:100%USED" for dev in self._lvm.block_devs()]
        self._lvm.umount_all()
        with self.assertRaises(snapm.SnapmSizePolicyError) as cm:
            self.manager.create_snapshot_set("testset0", dev_specs)
        self._lvm.mount_all()

    def test_create_snapshot_set_bad_name_backslash(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set("bad\\name", self.mount_points())

    def test_create_snapshot_set_bad_name_underscore(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set("bad_name", self.mount_points())

    def test_create_snapshot_set_bad_name_slash(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set("bad/name", self.mount_points())

    def test_create_snapshot_set_bad_name_space(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set("bad name", self.mount_points())

    def test_create_snapshot_set_bad_name_at(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set("bad@name", self.mount_points())

    def test_create_snapshot_set_bad_name_pipe(self):
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set("bad|name", self.mount_points())

    def test_create_snapshot_set_name_too_long(self):
        name = "a" * 127
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.create_snapshot_set(name, self.mount_points())

    def test_create_snapshot_set_no_space_raises(self):
        with self.assertRaises(snapm.SnapmNoSpaceError) as cm:
            for i in range(0, 10):
                self.manager.create_snapshot_set(
                    f"testset{i}", self.mount_points()
                )
                self._lvm.dump_lvs()

    def test_create_delete_snapshot_set(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="testset0")
        self.manager.delete_snapshot_sets(s)
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 0)

    def test_delete_snapshot_set_nosuch(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="nosuch")
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.delete_snapshot_sets(s)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_delete_snapshot_set_mounted(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="testset0")

        mnt_snap = sset.snapshots[0]
        mnt_name = mnt_snap.name.split("/")[1]

        self._lvm.make_mount_point(mnt_name)
        self._lvm.mount(mnt_name)

        with self.assertRaises(snapm.SnapmBusyError) as cm:
            self.manager.delete_snapshot_sets(s)

        self._lvm.umount(mnt_name)
        self.manager.delete_snapshot_sets(s)

    def test_delete_err_raises(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())
        selection = snapm.Selection(name="testset0")

        def fail_delete():
            raise snapm.SnapmError("Error deleting snapshot")

        orig_delete = sset.snapshots[1].delete
        sset.snapshots[1].delete = fail_delete

        with self.assertRaises(snapm.SnapmPluginError) as cm:
            self.manager.delete_snapshot_sets(selection)

        sset.snapshots[0].delete = orig_delete
        self.manager.delete_snapshot_sets(selection)

    def test_create_snapshot_set_duplicate(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        with self.assertRaises(snapm.SnapmExistsError) as cm:
            self.manager.create_snapshot_set("testset0", self.mount_points())
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapshot_set_not_a_mount_point(self):
        mount_point = self.mount_points()[0]
        non_mount = os.path.join(mount_point, "etc")
        os.makedirs(non_mount)
        with self.assertRaises(snapm.SnapmPathError) as cm:
            self.manager.create_snapshot_set("testset0", [non_mount])

    def test_create_snapshot_set_no_provider(self):
        with self.assertRaises(snapm.SnapmError) as cm:
            self.manager.create_snapshot_set("testset0", ["/boot"])

    def test_rename_snapshot_set(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="testset0")
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 1)
        self.manager.rename_snapshot_set("testset0", "testset1")
        s1 = snapm.Selection(name="testset1")
        sets = self.manager.find_snapshot_sets(selection=s1)
        self.assertEqual(len(sets), 1)
        sets = self.manager.find_snapshot_sets(selection=s)
        self.assertEqual(len(sets), 0)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_rename_snapshot_set_nosuch(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.rename_snapshot_set("nosuch", "newname")
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_rename_snapshot_set_exists(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        self.manager.create_snapshot_set("testset1", self.mount_points())
        with self.assertRaises(snapm.SnapmExistsError) as cm:
            self.manager.rename_snapshot_set("testset0", "testset1")
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_rename_err_and_rollback(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        def fail_rename(_new_name):
            raise snapm.SnapmError("Error renaming snapshot")

        sset.snapshots[1].rename = fail_rename

        with self.assertRaises(snapm.SnapmPluginError) as cm:
            self.manager.rename_snapshot_set("testset0", "testset1")

        self.assertEqual(sset.name, "testset0")
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_find_snapshots(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        snaps = self.manager.find_snapshots()
        self.assertEqual(len(snaps), len(self.mount_points()))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_find_snapshots_with_selection(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        self.manager.create_snapshot_set("testset1", self.mount_points())
        s = snapm.Selection(name="testset0")
        snaps = self.manager.find_snapshots(selection=s)
        self.assertEqual(len(snaps), len(self.mount_points()))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_activate_deactivate_snapsets(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="testset0")
        self.manager.activate_snapshot_sets(selection=s)
        sets = self.manager.find_snapshot_sets(selection=s)
        for snap in sets[0].snapshots:
            self.assertEqual(snap.status, snapm.SnapStatus.ACTIVE)
        self.manager.deactivate_snapshot_sets(selection=s)
        for snap in sets[0].snapshots:
            if snap.origin.removeprefix("test_vg0/") in self.thin_volumes:
                self.assertEqual(snap.status, snapm.SnapStatus.INACTIVE)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_activate_snapsets_nosuch(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="nosuch")
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.activate_snapshot_sets(selection=s)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_deactivate_snapsets_nosuch(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="nosuch")
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.deactivate_snapshot_sets(selection=s)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_set_autoactivate_snapsets(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="testset0")
        self.manager.set_autoactivate(s, auto=False)
        sets = self.manager.find_snapshot_sets(selection=s)
        # autoactivate cannot be set to False for Stratis snapshots
        #for snap in sets[0].snapshots:
        #    self.assertEqual(snap.autoactivate, False)
        self.manager.set_autoactivate(s, auto=True)
        for snap in sets[0].snapshots:
            self.assertEqual(snap.autoactivate, True)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_set_autoactivate_snapsets_nosuch(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="nosuch")
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.set_autoactivate(s, True)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_revert_snapshot_sets(self):
        # Revert is only supported on LVM2: don't include Stratis in the test.
        origin_file = "root/origin"
        snapshot_file = "root/snapshot"
        testset = "testset0"

        # Create files in the origin volume and post-snapshot
        self._lvm.touch_path(origin_file)
        self.manager.create_snapshot_set(testset, self.mount_points())
        self._lvm.touch_path(snapshot_file)

        # Test that the origin and snapshot files both exist
        self.assertEqual(self._lvm.test_path(origin_file), True)
        self.assertEqual(self._lvm.test_path(snapshot_file), True)

        # Start revert the snapshot set
        selection = snapm.Selection(name=testset)
        self.manager.revert_snapshot_sets(selection)

        # Unmount the set, deactivate/reactivate and re-mount
        self.stop_start_storage()

        # Test that only the origin file exists
        self.assertEqual(self._lvm.test_path(origin_file), True)
        self.assertEqual(self._lvm.test_path(snapshot_file), False)

    def test_revert_reverting_snapshot_set_raises(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        selection = snapm.Selection(name=testset)
        self.manager.revert_snapshot_sets(selection)

        with self.assertRaises(snapm.SnapmBusyError) as cm:
            self.manager.revert_snapshot_sets(selection)

        # Unmount the set, deactivate/reactivate and re-mount
        self.stop_start_storage()

    def test_rename_reverting_snapshot_set_raises(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        selection = snapm.Selection(name=testset)
        self.manager.revert_snapshot_sets(selection)

        with self.assertRaises(snapm.SnapmStateError) as cm:
            self.manager.rename_snapshot_set(testset, "testset1")

        # Unmount the set, deactivate/reactivate and re-mount
        self.stop_start_storage()

    def test_delete_reverting_snapshot_set_raises(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        selection = snapm.Selection(name=testset)
        self.manager.revert_snapshot_sets(selection)

        with self.assertRaises(snapm.SnapmBusyError) as cm:
            self.manager.delete_snapshot_sets(selection)

        # Unmount the set, deactivate/reactivate and re-mount
        self.stop_start_storage()

    def test_revert_snapshot_set_bad_name_raises(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.revert_snapshot_set(name="nosuchset")

    def test_revert_snapshot_set_bad_uuid_raises(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.revert_snapshot_set(uuid=UUID("00000000-0000-0000-0000-000000000000"))

    def test_revert_snapshot_set_name_uuid_conflict_raises(self):
        sset1 = self.manager.create_snapshot_set("testset0", self.mount_points())
        sset2 = self.manager.create_snapshot_set("testset1", self.mount_points())

        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.revert_snapshot_set(name=sset1.name, uuid=sset2.uuid)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_resize_snapshot_set_bad_name_raises(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.resize_snapshot_set([], name="nosuchset")

    def test_resize_snapshot_set_bad_uuid_raises(self):
        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.resize_snapshot_set([], uuid=UUID("00000000-0000-0000-0000-000000000000"))

    def test_resize_snapshot_set_name_uuid_conflict_raises(self):
        sset1 = self.manager.create_snapshot_set("testset0", self.mount_points())
        sset2 = self.manager.create_snapshot_set("testset1", self.mount_points())

        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            self.manager.resize_snapshot_set([], name=sset1.name, uuid=sset2.uuid)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_resize_snapshot_set_non_member_raises(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        with self.assertRaises(snapm.SnapmNotFoundError) as cm:
            self.manager.resize_snapshot_set(["/home:1G"], name="testset0")

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_resize_snapshot_set_no_space_raises(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        with self.assertRaises(snapm.SnapmNoSpaceError) as cm:
            self.manager.resize_snapshot_set([], name="testset0", default_size_policy="20G")

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_resize_snapshot_set_mount_specs(self):
        testset = "testset0"
        mount_specs = [f"{mp}:512MiB" for mp in self.mount_points()]
        self.manager.create_snapshot_set(testset, mount_specs)

        snapset = self.manager.find_snapshot_sets(snapm.Selection(name=testset))[0]
        for snapshot in snapset.snapshots:
            if snapshot.provider.name == "lvm2cow":
                self.assertEqual(snapshot.size, 512 * 1024 ** 2)

        resize_specs = [f"{self._lvm.mount_root}/{name}:1GiB" for name in self._lvm.volumes]
        self.manager.resize_snapshot_set(resize_specs, name=testset)

        snapset = self.manager.find_snapshot_sets(snapm.Selection(name=testset))[0]
        for snapshot in snapset.snapshots:
            if snapshot.provider.name == "lvm2cow":
                self.assertEqual(snapshot.size, 1024 ** 3)

        self.manager.delete_snapshot_sets(snapm.Selection(name=testset))

    def test_resize_snapshot_set_default_size_policy(self):
        testset = "testset0"
        mount_specs = [f"{mp}:512MiB" for mp in self.mount_points()]
        self.manager.create_snapshot_set(testset, mount_specs)

        snapset = self.manager.find_snapshot_sets(snapm.Selection(name=testset))[0]
        for snapshot in snapset.snapshots:
            if snapshot.provider.name == "lvm2cow":
                self.assertEqual(snapshot.size, 512 * 1024 ** 2)

        self.manager.resize_snapshot_set(None, name=testset, default_size_policy="1G")

        snapset = self.manager.find_snapshot_sets(snapm.Selection(name=testset))[0]
        for snapshot in snapset.snapshots:
            if snapshot.provider.name == "lvm2cow":
                self.assertEqual(snapshot.size, 1024 ** 3)

    def test_split_snapshot_set_split(self):
        testset = "testset0"
        splitset = "testset1"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_source = self.mount_points()[0]
        nosplit_sources = self.mount_points()[1:]

        # Split split_source from snapshot set
        split = self.manager.split_snapshot_set(testset, splitset, [split_source])

        # Get updated snapshot set
        test = self.manager.find_snapshot_sets(selection=snapm.Selection(name=testset))[0]

        self.assertEqual(split.name, splitset)
        self.assertTrue(split_source in split.sources)
        self.assertTrue(split_source not in test.sources)
        for source in nosplit_sources:
            self.assertTrue(source in test.sources)

    def test_split_snapshot_set_split_bad_name(self):
        testset = "testset0"
        splitset = "testset1"
        badset = "badset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_source = self.mount_points()[0]

        with self.assertRaises(snapm.SnapmNotFoundError):
            # Attempt to split with bad snapset name
            split = self.manager.split_snapshot_set(badset, splitset, [split_source])

    def test_split_snapshot_set_split_empty_split(self):
        testset = "testset0"
        splitset = "testset1"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_sources = self.mount_points()

        with self.assertRaises(snapm.SnapmArgumentError):
            # Split split_source from snapshot set
            split = self.manager.split_snapshot_set(testset, splitset, split_sources)

    def test_split_snapshot_set_split_size_policy_raises(self):
        testset = "testset0"
        splitset = "testset1"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_sources = [f"{mp}:10%SIZE" for mp in self.mount_points()]

        with self.assertRaises(snapm.SnapmArgumentError):
            # Attempt to split with size policies in source_specs
            split = self.manager.split_snapshot_set(testset, splitset, split_sources)

    def test_split_snapshot_set_split_bad_source_raises(self):
        testset = "testset0"
        splitset = "testset1"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_source = "/quux"

        with self.assertRaises(snapm.SnapmNotFoundError):
            # Split non-existent source from snapshot set
            split = self.manager.split_snapshot_set(testset, splitset, [split_source])

    def test_split_snapshot_set_split_empty_sources(self):
        testset = "testset0"
        splitset = "testset1"
        self.manager.create_snapshot_set(testset, self.mount_points())

        with self.assertRaises(snapm.SnapmArgumentError):
            # Split [] from snapshot set
            split = self.manager.split_snapshot_set(testset, splitset, [])

    def test_split_snapshot_set_prune(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        prune_source = self.mount_points()[0]
        noprune_sources = self.mount_points()[1:]

        # Prune prune_source from snapshot set
        prune = self.manager.split_snapshot_set(testset, None, [prune_source])

        self.assertTrue(prune_source not in prune.sources)
        for source in noprune_sources:
            self.assertTrue(source in prune.sources)

    def test_split_snapshot_set_prune_bad_name(self):
        testset = "testset0"
        badset = "badset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_source = self.mount_points()[0]

        with self.assertRaises(snapm.SnapmNotFoundError):
            # Attempt to split with bad snapset name
            split = self.manager.split_snapshot_set(badset, None, [split_source])

    def test_split_snapshot_set_prune_empty_prune(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_sources = self.mount_points()

        with self.assertRaises(snapm.SnapmArgumentError):
            # Split split_source from snapshot set
            split = self.manager.split_snapshot_set(testset, None, split_sources)

    def test_split_snapshot_set_prune_empty_sources(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        with self.assertRaises(snapm.SnapmArgumentError):
            # Split [] from snapshot set
            split = self.manager.split_snapshot_set(testset, None, [])

    def test_split_snapshot_set_prune_size_policy_raises(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_sources = [f"{mp}:10%SIZE" for mp in self.mount_points()]

        with self.assertRaises(snapm.SnapmArgumentError):
            # Attempt to split with size policies in source_specs
            split = self.manager.split_snapshot_set(testset, None, split_sources)

    def test_split_snapshot_set_prune_bad_source_raises(self):
        testset = "testset0"
        self.manager.create_snapshot_set(testset, self.mount_points())

        split_source = "/quux"

        with self.assertRaises(snapm.SnapmNotFoundError):
            # Split non-existent source from snapshot set
            split = self.manager.split_snapshot_set(testset, None, [split_source])


@unittest.skipIf(not have_root(), "requires root privileges")
class ManagerTestsThin(unittest.TestCase):
    """
    Tests for snapm.manager.Manager that apply only to thin provisioned
    snapshot providers (lvm2thin and stratis).
    """
    volumes = [] # Do not use lvm2cow
    thin_volumes = ["root", "home", "var", "data_vol"]
    stratis_volumes = ["fs1", "fs2"]

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)
        def cleanup_lvm():
            log.debug("Cleaning up LVM (%s)", self._testMethodName)
            if hasattr(self, "_lvm"):
                self._lvm.destroy()

        def cleanup_stratis():
            log.debug("Cleaning up Stratis (%s)", self._testMethodName)
            if hasattr(self, "_stratis"):
                self._stratis.destroy()

        self.addCleanup(cleanup_lvm)
        self.addCleanup(cleanup_stratis)

        self._lvm = LvmLoopBacked(self.volumes, thin_volumes=self.thin_volumes)
        self._stratis = StratisLoopBacked(self.stratis_volumes)

        self.manager = snapm.manager.Manager()

    def mount_points(self):
        return self._lvm.mount_points() + self._stratis.mount_points()

    def test_create_snapshot_set_recursion_raises(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())
        self.manager.activate_snapshot_sets(snapm.Selection(name="testset0"))
        snap_devs = [snapshot.devpath for snapshot in sset.snapshots]
        with self.assertRaises(snapm.SnapmRecursionError) as cm:
            sset = self.manager.create_snapshot_set("testset1", snap_devs)
