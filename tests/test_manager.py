# Copyright (C) 2023 Red Hat, Inc., Bryn M. Reeves <bmr@redhat.com>
#
# tests/test_manager.py - Plugin core unit tests
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

import snapm
import snapm.manager as manager
import boom

from tests import have_root, BOOT_ROOT_TEST
from ._util import LvmLoopBacked, StratisLoopBacked


_log = logging.getLogger()
_log.level = logging.DEBUG
_log.addHandler(logging.FileHandler("test.log"))

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error

boom.set_boot_path(BOOT_ROOT_TEST)


class ManagerTestsSimple(unittest.TestCase):
    """
    Test manager interfaces with mock callouts
    """

    _old_path = None

    def setUp(self):
        bin_path = os.path.abspath("tests/bin")
        cur_path = os.environ["PATH"]
        self._old_path = cur_path
        os.environ["PATH"] = bin_path + os.pathsep + cur_path

    def tearDown(self):
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
        p = manager.Plugin(_log)
        info = p.info()
        self.assertEqual(info, {"name": "plugin", "version": "0.1.0"})

    def _plugin_base_not_implemented_raises(self, method, args):
        p = manager.Plugin(_log)
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
    volumes = ["root", "home", "var", "data_vol"]
    thin_volumes = ["opt", "srv", "thin-vol"]
    stratis_volumes = ["fs1", "fs2"]

    def setUp(self):
        def cleanup_lvm():
            if hasattr(self, "_lvm"):
                self._lvm.destroy()

        def cleanup_stratis():
            if hasattr(self, "_stratis"):
                self._stratis.destroy()

        self.addCleanup(cleanup_lvm)
        self.addCleanup(cleanup_stratis)

        self._lvm = LvmLoopBacked(self.volumes, thin_volumes=self.thin_volumes)
        self._stratis = StratisLoopBacked(self.stratis_volumes)

        self.manager = snapm.manager.Manager()

    def mount_points(self):
        return self._lvm.mount_points() + self._stratis.mount_points()

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

    def test_rename_snapshot_set_exists(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        self.manager.create_snapshot_set("testset1", self.mount_points())
        with self.assertRaises(snapm.SnapmExistsError) as cm:
            self.manager.rename_snapshot_set("testset0", "testset1")
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

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
        self.manager.create_snapshot_set(testset, self._lvm.mount_points())
        self._lvm.touch_path(snapshot_file)

        # Test that the origin and snapshot files both exist
        self.assertEqual(self._lvm.test_path(origin_file), True)
        self.assertEqual(self._lvm.test_path(snapshot_file), True)

        # Start revert the snapshot set
        selection = snapm.Selection(name=testset)
        self.manager.revert_snapshot_sets(selection)

        # Unmount the set, deactivate/reactivate and re-mount
        self._lvm.umount_all()
        self._lvm.deactivate()
        self._lvm.activate()
        self._lvm.mount_all()

        # Test that only the origin file exists
        self.assertEqual(self._lvm.test_path(origin_file), True)
        self.assertEqual(self._lvm.test_path(snapshot_file), False)
