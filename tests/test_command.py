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

log = logging.getLogger()
log.level = logging.DEBUG
log.addHandler(logging.FileHandler("test.log"))

import snapm
import snapm.command as command
from snapm.report import ReportOpts
import snapm.manager
import boom

from tests import MockArgs, have_root, BOOT_ROOT_TEST

from ._util import LvmLoopBacked


boom.set_boot_path(BOOT_ROOT_TEST)


class CommandTestsSimple(unittest.TestCase):
    """
    Test command interfaces
    """

    _old_path = None

    def setUp(self):
        bin_path = os.path.abspath("tests/bin")
        cur_path = os.environ["PATH"]
        self._old_path = cur_path
        os.environ["PATH"] = bin_path + os.pathsep + cur_path

    def tearDown(self):
        os.environ["PATH"] = self._old_path

    def test__str_indent(self):
        test_str = "a\nb\nc\nd"
        xstr = "    a\n    b\n    c\n    d"
        output = command._str_indent(test_str, 4)
        self.assertEqual(output, xstr)

    def test__expand_fields_default(self):
        default_fields = "one,two,three"
        xfields = default_fields
        output = command._expand_fields(default_fields, "")
        self.assertEqual(output, xfields)

    def test__expand_fields_add(self):
        default_fields = "one,two,three"
        user_fields = "+four"
        xfields = default_fields + ",four"
        output = command._expand_fields(default_fields, user_fields)
        self.assertEqual(output, xfields)

    def test__expand_fields_replace(self):
        default_fields = "one,two,three"
        user_fields = "four,five,six"
        xfields = user_fields
        output = command._expand_fields(default_fields, user_fields)
        self.assertEqual(output, xfields)

    def test__report_opts_from_args_none(self):
        args = MockArgs()
        xopts = ReportOpts()
        opts = command._report_opts_from_args(args)
        self.assertEqual(opts, xopts)

    def test__report_opts_from_args_no_args(self):
        args = None
        xopts = ReportOpts()
        opts = command._report_opts_from_args(args)
        self.assertEqual(opts, xopts)

    def test__report_opts_from_args(self):
        args = MockArgs()
        args.rows = True
        args.separator = ":"
        args.name_prefixes = True
        args.no_headings = True

        xopts = ReportOpts()
        xopts.columns_as_rows = True
        xopts.separator = ":"
        xopts.field_name_prefix = "SNAPM_"
        xopts.unquoted = False
        xopts.aligned = False
        xopts.headings = False

        opts = command._report_opts_from_args(args)

        self.assertEqual(opts, xopts)

    def test__list_cmd_simple(self):
        args = MockArgs()
        command._list_cmd(args)

    def test__list_cmd_simple_bad_field(self):
        args = MockArgs()
        args.options = "nosuchfield"
        command._list_cmd(args)

    def test__list_cmd_simple_debug(self):
        args = MockArgs()
        args.debug = "all"
        command._list_cmd(args)

    def test__list_cmd_simple_debug_bad_field(self):
        args = MockArgs()
        args.debug = "all"
        args.options = "nosuchfield"
        with self.assertRaises(ValueError) as cm:
            command._list_cmd(args)

    def test__list_cmd_simple_verbose(self):
        args = MockArgs()
        args.verbose = 1
        command._list_cmd(args)

    def test__list_cmd_simple_fields(self):
        args = MockArgs()
        args.options = "name,uuid"
        command._list_cmd(args)

    def test__list_snapshot_cmd_simple(self):
        args = MockArgs()
        command._snapshot_list_cmd(args)

    def test__show_cmd_simple(self):
        args = MockArgs()
        command._show_cmd(args)

    def test__show_cmd_simple_verbose(self):
        args = MockArgs()
        args.verbose = 1
        command._show_cmd(args)

    def test__show_snapshot_cmd_simple(self):
        args = MockArgs()
        command._snapshot_show_cmd(args)

    def test__plugin_list_cmd_simple(self):
        args = MockArgs()
        command._plugin_list_cmd(args)

    def test_main_plugin_list(self):
        args = [os.path.join(os.getcwd(), "bin/snapm"), "plugin", "list"]
        self.assertEqual(command.main(args), 0)

    def test_set_debug_none(self):
        args = MockArgs()
        command.set_debug(args.debug)

    def test_set_debug_single(self):
        from snapm import get_debug_mask, SNAPM_DEBUG_COMMAND

        args = MockArgs()
        args.debug = "command"
        command.set_debug(args.debug)
        self.assertEqual(get_debug_mask(), SNAPM_DEBUG_COMMAND)

    def test_set_debug_list(self):
        from snapm import get_debug_mask, SNAPM_DEBUG_ALL

        args = MockArgs()
        args.debug = "manager,command,report"
        command.set_debug(args.debug)
        self.assertEqual(get_debug_mask(), SNAPM_DEBUG_ALL)

    def test_set_debug_all(self):
        from snapm import get_debug_mask, SNAPM_DEBUG_ALL

        args = MockArgs()
        args.debug = "all"
        command.set_debug(args.debug)
        self.assertEqual(get_debug_mask(), SNAPM_DEBUG_ALL)

    def test_set_debug_single_bad(self):
        args = MockArgs()
        args.debug = "nosuch"
        with self.assertRaises(ValueError) as cm:
            command.set_debug(args.debug)

    def test_main_version(self):
        args = [os.path.join(os.getcwd(), "bin/snapm"), "--version"]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)

    def test_main_too_few_args(self):
        args = [os.path.join(os.getcwd(), "bin/snapm")]
        r = command.main(args)
        self.assertEqual(r, 1)

    def test_main_bad_command_type(self):
        args = [os.path.join(os.getcwd(), "bin/snapm"), "nosuch", "command"]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)

    def test_main_bad_command(self):
        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapset", "nocommand"]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)


@unittest.skipIf(not have_root(), "requires root privileges")
class CommandTests(unittest.TestCase):
    """
    Test command interfaces with devices
    """

    volumes = ["root", "home", "var"]
    thin_volumes = ["opt", "srv"]

    def setUp(self):
        self._lvm = LvmLoopBacked(self.volumes, thin_volumes=self.thin_volumes)
        self.manager = snapm.manager.Manager()

    def tearDown(self):
        self._lvm.destroy()

    def test_print_snapsets(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        command.print_snapsets(self.manager)

    def test_print_snapshots(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        command.print_snapshots(self.manager)

    def test_show_snapsets(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        command.show_snapsets(self.manager)

    def test_show_snapsets_with_members(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        command.show_snapsets(self.manager, members=True)

    def test_show_snapsets_with_selection(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        s = snapm.Selection(name="testset0")
        command.show_snapsets(self.manager, selection=s)

    def test_show_snapshots(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        command.show_snapshots(self.manager)

    def test_create_snapset(self):
        command.create_snapset(self.manager, "testset0", self._lvm.mount_points())

    def test_create_snapset_default_size_policy(self):
        command.create_snapset(
            self.manager, "testset0", self._lvm.mount_points(), size_policy="10%FREE"
        )

    def test_create_snapset_per_mount_size_policy_10_free(self):
        mount_specs = [f"{mp}:10%FREE" for mp in self._lvm.mount_points()]
        command.create_snapset(self.manager, "testset0", mount_specs)

    def test_create_snapset_per_mount_size_policy_100_size(self):
        mount_specs = [f"{mp}:100%SIZE" for mp in self._lvm.mount_points()]
        command.create_snapset(self.manager, "testset0", mount_specs)

    def test_create_delete_snapset(self):
        command.create_snapset(self.manager, "testset0", self._lvm.mount_points())
        command.delete_snapset(self.manager, snapm.Selection(name="testset0"))

    def test_delete_snapset_ambiguous(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            command.delete_snapset(self.manager, snapm.Selection(nr_snapshots=2))

    def test_rename_snapset(self):
        command.create_snapset(self.manager, "testset0", self._lvm.mount_points())
        command.rename_snapset(self.manager, "testset0", "testset1")
        sets = self.manager.find_snapshot_sets(snapm.Selection(name="testset1"))
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0].name, "testset1")

    def test_main_snapset_create(self):
        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapset", "create", "testset0"]
        args.extend(self._lvm.mount_points())
        command.main(args)

    def test_main_snapset_delete(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapset", "delete", "testset0"]
        command.main(args)

    def test_main_snapset_rename(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [
            os.path.join(os.getcwd(), "bin/snapm"),
            "snapset",
            "rename",
            "testset0",
            "testset1",
        ]
        command.main(args)

    def test_main_snapset_rename_missing_newname(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapset", "rename", "testset0"]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)

    def test_main_snapset_list(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapset", "list"]
        command.main(args)

    def test_main_snapset_list_verbose(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [os.path.join(os.getcwd(), "bin/snapm"), "-v", "snapset", "list"]
        command.main(args)

    def test_main_snapset_list_very_verbose_debug(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [
            os.path.join(os.getcwd(), "bin/snapm"),
            "-vv",
            "--debug=all",
            "snapset",
            "list",
        ]
        command.main(args)

    def test_main_snapset_list_bad_debug(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [
            os.path.join(os.getcwd(), "bin/snapm"),
            "-vv",
            "--debug=quux",
            "snapset",
            "list",
        ]
        r = command.main(args)
        self.assertEqual(r, 1)

    def test_main_snapshot_activate(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapshot", "activate"]
        self.assertEqual(command.main(args), 0)

    def test_main_snapshot_deactivate(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapshot", "deactivate"]
        self.assertEqual(command.main(args), 0)

    def test_main_snapshot_autoactivate(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [
            os.path.join(os.getcwd(), "bin/snapm"),
            "snapshot",
            "autoactivate",
            "--yes",
        ]
        self.assertEqual(command.main(args), 0)

    def test_main_snapshot_activate_nosuch(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [
            os.path.join(os.getcwd(), "bin/snapm"),
            "snapshot",
            "activate",
            "-N",
            "nosuch",
        ]
        self.assertEqual(command.main(args), 1)

    def test_main_snapshot_deactivate_nosuch(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [
            os.path.join(os.getcwd(), "bin/snapm"),
            "snapshot",
            "deactivate",
            "-N",
            "nosuch",
        ]
        self.assertEqual(command.main(args), 1)

    def test_main_snapshot_autoactivate_nosuch(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [
            os.path.join(os.getcwd(), "bin/snapm"),
            "snapshot",
            "autoactivate",
            "--yes",
            "-N",
            "nosuch",
        ]
        self.assertEqual(command.main(args), 1)

    def test_main_snapset_show(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapset", "show"]
        self.assertEqual(command.main(args), 0)

    def test_main_snapshot_list(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapshot", "list"]
        self.assertEqual(command.main(args), 0)

    def test_main_snapshot_show(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapshot", "show"]
        self.assertEqual(command.main(args), 0)

    def test_main_snapset_activate(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapset", "activate"]
        self.assertEqual(command.main(args), 0)

    def test_main_snapset_deactivate(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapset", "deactivate"]
        self.assertEqual(command.main(args), 0)

    def test_main_snapset_autoactivate(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())
        args = [
            os.path.join(os.getcwd(), "bin/snapm"),
            "snapset",
            "autoactivate",
            "--yes",
        ]
        self.assertEqual(command.main(args), 0)

    def test_main_snapset_revert(self):
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
        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapset", "revert", testset]
        self.assertEqual(command.main(args), 0)

        # Unmount the set, deactivate/reactivate and re-mount
        self._lvm.umount_all()
        self._lvm.deactivate()
        self._lvm.activate()
        self._lvm.mount_all()

        # Test that only the origin file exists
        self.assertEqual(self._lvm.test_path(origin_file), True)
        self.assertEqual(self._lvm.test_path(snapshot_file), False)
