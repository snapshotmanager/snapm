# Copyright Red Hat
#
# tests/test_command.py - CLI layer tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
import logging
import os

log = logging.getLogger()

import snapm
import snapm.command as command
from snapm.report import ReportOpts
import snapm.manager
import boom

from tests import MockArgs, have_root, BOOT_ROOT_TEST

from ._util import LvmLoopBacked, StratisLoopBacked


boom.set_boot_path(BOOT_ROOT_TEST)


class CommandTestsBase(unittest.TestCase):
    def get_main_args(self):
        """
        Return an argument array (in the form of sys.argv) reflecting the
        ``bin/snapm`` command, with verbose logging and debug enabled.

        :returns: A list of command arguments.
        """
        return [os.path.join(os.getcwd(), "bin/snapm")]

    def get_debug_main_args(self):
        """
        Return an argument array (in the form of sys.argv) reflecting the
        ``bin/snapm`` command, with verbose logging and debug enabled.

        :returns: A list of command arguments.
        """
        return self.get_main_args() + ["-vv", "--debug=all"]


class CommandTestsSimple(CommandTestsBase):
    """
    Test command interfaces
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

    @unittest.skipIf(not have_root(), "requires root privileges")
    def test__list_cmd_simple(self):
        args = MockArgs()
        command._list_cmd(args)

    @unittest.skipIf(not have_root(), "requires root privileges")
    def test__list_cmd_simple_bad_field(self):
        args = MockArgs()
        args.options = "nosuchfield"
        command._list_cmd(args)

    @unittest.skipIf(not have_root(), "requires root privileges")
    def test__list_cmd_simple_debug(self):
        args = MockArgs()
        args.debug = "all"
        command._list_cmd(args)

    @unittest.skipIf(not have_root(), "requires root privileges")
    def test__list_cmd_simple_debug_bad_field(self):
        args = MockArgs()
        args.debug = "all"
        args.options = "nosuchfield"
        with self.assertRaises(ValueError):
            command._list_cmd(args)

    @unittest.skipIf(not have_root(), "requires root privileges")
    def test__list_cmd_simple_verbose(self):
        args = MockArgs()
        args.verbose = 1
        command._list_cmd(args)

    @unittest.skipIf(not have_root(), "requires root privileges")
    def test__list_cmd_simple_fields(self):
        args = MockArgs()
        args.options = "name,uuid"
        command._list_cmd(args)

    @unittest.skipIf(not have_root(), "requires root privileges")
    def test__list_snapshot_cmd_simple(self):
        args = MockArgs()
        command._snapshot_list_cmd(args)

    @unittest.skipIf(not have_root(), "requires root privileges")
    def test__show_cmd_simple(self):
        args = MockArgs()
        command._show_cmd(args)

    @unittest.skipIf(not have_root(), "requires root privileges")
    def test__show_cmd_simple_verbose(self):
        args = MockArgs()
        args.verbose = 1
        command._show_cmd(args)

    @unittest.skipIf(not have_root(), "requires root privileges")
    def test__show_snapshot_cmd_simple(self):
        args = MockArgs()
        command._snapshot_show_cmd(args)

    @unittest.skipIf(not have_root(), "requires root privileges")
    def test__plugin_list_cmd_simple(self):
        args = MockArgs()
        command._plugin_list_cmd(args)

    @unittest.skipIf(not have_root(), "requires root privileges")
    def test_main_plugin_list(self):
        args = self.get_debug_main_args()
        args += ["plugin", "list"]
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
        args = self.get_debug_main_args()
        args += ["--version"]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)

    def test_main_too_few_args(self):
        args = self.get_debug_main_args()
        r = command.main(args)
        self.assertEqual(r, 1)

    def test_main_bad_command_type(self):
        args = self.get_debug_main_args()
        args += ["nosuch", "command"]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)

    def test_main_bad_command(self):
        args = self.get_debug_main_args()
        args += ["snapset", "nocommand"]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)

    def test_main_schedule_create_requires_policy_type(self):
        args = self.get_debug_main_args()
        args += [
            "schedule", "create",
            "--keep-days", "7",
            "--calendarspec", "hourly",
            "hourly", "/", "/var",
        ]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)
        self.assertEqual(cm.exception.code, 2)


@unittest.skipIf(not have_root(), "requires root privileges")
class CommandTests(CommandTestsBase):
    """
    Test command interfaces with devices
    """

    volumes = ["root", "home", "var"]
    thin_volumes = ["opt", "srv"]
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

    def test_print_snapsets(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        command.print_snapsets(self.manager)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_print_snapshots(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        command.print_snapshots(self.manager)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_show_snapsets(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        command.show_snapsets(self.manager)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_show_snapsets_with_members(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        command.show_snapsets(self.manager, members=True)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_show_snapsets_with_selection(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        s = snapm.Selection(name="testset0")
        command.show_snapsets(self.manager, selection=s)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_show_snapshots(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        command.show_snapshots(self.manager)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_show_snapshots_json(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        command.show_snapshots(self.manager, json=True)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapset(self):
        sset = command.create_snapset(self.manager, "testset0", self.mount_points())
        self.assertEqual(sset.index, snapm.SNAPSET_INDEX_NONE)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapset_default_size_policy(self):
        command.create_snapset(
            self.manager, "testset0", self.mount_points(), size_policy="10%FREE"
        )
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapset_per_mount_size_policy_10_free(self):
        mount_specs = [f"{mp}:10%FREE" for mp in self.mount_points()]
        command.create_snapset(self.manager, "testset0", mount_specs)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapset_per_mount_size_policy_100_size(self):
        mount_specs = [f"{mp}:100%SIZE" for mp in self.mount_points()]
        command.create_snapset(self.manager, "testset0", mount_specs)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_create_snapset_autoindex(self):
        one = command.create_snapset(self.manager, "hourly", self.mount_points(), autoindex=True)
        two = command.create_snapset(self.manager, "hourly", self.mount_points(), autoindex=True)
        self.assertEqual(one.index, 0)
        self.assertEqual(two.index, 1)
        self.manager.delete_snapshot_sets(snapm.Selection(basename="hourly"))

    def test_create_delete_snapset(self):
        command.create_snapset(self.manager, "testset0", self.mount_points())
        command.delete_snapset(self.manager, snapm.Selection(name="testset0"))

    def test_delete_snapset_ambiguous(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        with self.assertRaises(snapm.SnapmInvalidIdentifierError) as cm:
            command.delete_snapset(self.manager, snapm.Selection(nr_snapshots=2))
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_rename_snapset(self):
        command.create_snapset(self.manager, "testset0", self.mount_points())
        command.rename_snapset(self.manager, "testset0", "testset1")
        sets = self.manager.find_snapshot_sets(snapm.Selection(name="testset1"))
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0].name, "testset1")
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset1"))

    def test_main_snapset_create(self):
        args = self.get_debug_main_args()
        args += ["snapset", "create", "testset0"]
        args.extend(self.mount_points())
        self.assertEqual(command.main(args), 0)

        args = [os.path.join(os.getcwd(), "bin/snapm"), "snapset", "delete", "testset0"]
        self.assertEqual(command.main(args), 0)

    def test_main_snapset_create_autoindex(self):
        args = self.get_debug_main_args()
        args += ["snapset", "create", "--autoindex", "testset0"]
        args.extend(self.mount_points())
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["snapset", "delete", "testset0.0"]
        self.assertEqual(command.main(args), 0)

    def test_main_snapset_create_bad_source(self):
        args = self.get_main_args()
        args += ["snapset", "create", "testset0", "/quux"]
        self.assertEqual(command.main(args), 1)

    def test_main_snapset_delete(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        args = self.get_debug_main_args()
        args += ["snapset", "delete", "testset0"]
        self.assertEqual(command.main(args), 0)

    def test_main_snapset_rename(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "rename", "testset0", "testset1"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["snapset", "delete", "testset1"]
        self.assertEqual(command.main(args), 0)

    def test_main_snapset_rename_missing_newname(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "rename", "testset0"]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_split(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        to_split = self.mount_points()[0]

        args = self.get_debug_main_args()
        args += ["snapset", "split", "testset0", "testset1", to_split]
        self.assertEqual(command.main(args), 0)

        # Refresh manager context
        self.manager.discover_snapshot_sets()

        # Verify split
        sets = self.manager.find_snapshot_sets(snapm.Selection(name="testset1"))
        self.assertEqual(len(sets), 1)
        self.assertTrue(to_split in sets[0].sources)

    def test_main_snapset_prune(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        to_prune = self.mount_points()[0]

        args = self.get_debug_main_args()
        args += ["snapset", "prune", "testset0", to_prune]
        self.assertEqual(command.main(args), 0)

        # Refresh manager context
        self.manager.discover_snapshot_sets()

        # Verify prune
        pruned = self.manager.find_snapshot_sets(snapm.Selection(name="testset0"))[0]
        self.assertFalse(to_prune in pruned.sources)

    def test_main_snapset_list(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "list"]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_list_json(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "list", "--json"]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_list_verbose(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = [os.path.join(os.getcwd(), "bin/snapm"), "-v", "snapset", "list"]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_list_very_verbose_debug(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = [
            os.path.join(os.getcwd(), "bin/snapm"),
            "-vv",
            "--debug=all",
            "snapset",
            "list",
        ]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_list_bad_debug(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())
        args = [
            os.path.join(os.getcwd(), "bin/snapm"),
            "-vv",
            "--debug=quux",
            "snapset",
            "list",
        ]
        self.assertEqual(command.main(args), 1)
        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_activate(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "activate"]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_deactivate(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "deactivate"]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_autoactivate(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "autoactivate", "--yes"]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_activate_nosuch(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "activate", "-N", "nosuch"]
        self.assertEqual(command.main(args), 1)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_deactivate_nosuch(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "deactivate", "-N", "nosuch"]
        self.assertEqual(command.main(args), 1)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_autoactivate_nosuch(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "autoactivate", "--yes", "nosuch"]
        self.assertEqual(command.main(args), 1)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_show(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "show"]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_show_identifier_name(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "show", sset.name]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_show_identifier_uuid(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "show", str(sset.uuid)]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_show_arg_name(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "show", "--name", sset.name]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_show_arg_uuid(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "show", "--uuid", str(sset.uuid)]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_show_arg_uuid_with_identifier(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "show", "--uuid", str(sset.uuid), sset.name]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)
        self.assertEqual(cm.exception.code, 2)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_show_arg_name_with_identifier(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "show", "--name", sset.name, str(sset.uuid)]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)
        self.assertEqual(cm.exception.code, 2)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_show_arg_name_and_arg_uuid(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "show", "--name", sset.name, "--uuid", str(sset.uuid)]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)
        self.assertEqual(cm.exception.code, 2)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_list(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "list"]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_show(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "show"]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_show_identifier_name(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "show", sset.name]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_show_identifier_uuid(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "show", str(sset.uuid)]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_show_arg_name(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "show", "--name", sset.name]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_show_arg_uuid(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "show", "--uuid", str(sset.uuid)]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_show_arg_uuid_with_identifier(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "show", "--uuid", str(sset.uuid), sset.name]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)
        self.assertEqual(cm.exception.code, 2)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_show_arg_name_with_identifier(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "show", "--name", sset.name, str(sset.uuid)]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)
        self.assertEqual(cm.exception.code, 2)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapshot_show_arg_name_and_arg_uuid(self):
        sset = self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapshot", "show", "--name", sset.name, "--uuid", str(sset.uuid)]
        with self.assertRaises(SystemExit) as cm:
            command.main(args)
        self.assertEqual(cm.exception.code, 2)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_activate(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "activate"]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_deactivate(self):
        self.manager.create_snapshot_set("testset0", self._lvm.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "deactivate"]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_autoactivate(self):
        self.manager.create_snapshot_set("testset0", self.mount_points())

        args = self.get_debug_main_args()
        args += ["snapset", "autoactivate", "--yes"]
        self.assertEqual(command.main(args), 0)

        self.manager.delete_snapshot_sets(snapm.Selection(name="testset0"))

    def test_main_snapset_resize(self):
        self.manager.create_snapshot_set("testset0", [self.mount_points()[0] + ":10%SIZE"])

        args = self.get_debug_main_args()
        args += ["snapset", "resize", "--size-policy", "100%SIZE", "testset0"]
        self.assertEqual(command.main(args), 0)

    def test_main_snapset_revert(self):
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
        args = self.get_debug_main_args()
        args += ["snapset", "revert", testset]
        self.assertEqual(command.main(args), 0)

        # Unmount the set, deactivate/reactivate and re-mount
        self._lvm.umount_all()
        self._lvm.deactivate()
        self._lvm.activate()
        self._lvm.mount_all()

        self._stratis.umount_all()
        self._stratis.stop_pool()
        self._stratis.start_pool()
        self._stratis.mount_all()

        # Test that only the origin file exists
        self.assertEqual(self._lvm.test_path(origin_file), True)
        self.assertEqual(self._lvm.test_path(snapshot_file), False)

    def test_main_snapset_revert_by_uuid(self):
        # Revert is only supported on LVM2: don't include Stratis in the test.
        origin_file = "root/origin"
        snapshot_file = "root/snapshot"
        testset = "testset0"

        # Create files in the origin volume and post-snapshot
        self._lvm.touch_path(origin_file)
        sset = self.manager.create_snapshot_set(testset, self.mount_points())
        self._lvm.touch_path(snapshot_file)

        # Test that the origin and snapshot files both exist
        self.assertEqual(self._lvm.test_path(origin_file), True)
        self.assertEqual(self._lvm.test_path(snapshot_file), True)

        # Start revert the snapshot set
        args = self.get_debug_main_args()
        args += ["snapset", "revert", str(sset.uuid)]
        self.assertEqual(command.main(args), 0)

        # Unmount the set, deactivate/reactivate and re-mount
        self._lvm.umount_all()
        self._lvm.deactivate()
        self._lvm.activate()
        self._lvm.mount_all()

        self._stratis.umount_all()
        self._stratis.stop_pool()
        self._stratis.start_pool()
        self._stratis.mount_all()

        # Test that only the origin file exists
        self.assertEqual(self._lvm.test_path(origin_file), True)
        self.assertEqual(self._lvm.test_path(snapshot_file), False)

    def test_main_snapset_revert_no_name_or_uuid(self):
        args = self.get_main_args()
        args += ["snapset", "revert"]
        self.assertEqual(command.main(args), 1)

    def test_main_schedule_create_delete_uppercase_variants(self):
        for p in ["ALL", "COUNT", "AGE", "TIMELINE"]:
            name = f"hourly-{p.lower()}"
            with self.subTest(policy=p):
                args = self.get_debug_main_args()
                args += ["schedule", "create", "--policy-type", p, "--calendarspec", "hourly", name]
                if p == "COUNT":
                    args.extend(["--keep-count", "1"])
                elif p == "AGE":
                    args.extend(["--keep-days", "1"])
                elif p == "TIMELINE":
                    args.extend(["--keep-daily", "1"])
                args.extend(self.mount_points())
                self.assertEqual(command.main(args), 0)

                args = self.get_debug_main_args()
                args += ["schedule", "delete", name]
                self.assertEqual(command.main(args), 0)

    def test_main_schedule_create_show_list_gc_delete_ALL(self):
        args = self.get_debug_main_args()
        args += [
            "schedule",
            "create",
            "--policy-type",
            "all",
            "--calendarspec",
            "hourly",
            "hourly",
        ]
        args.extend(self.mount_points())
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "show", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "show", "--json", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "list"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "gc", "--config", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "delete", "hourly"]
        self.assertEqual(command.main(args), 0)

    def test_main_schedule_create_show_list_gc_delete_COUNT(self):
        args = self.get_debug_main_args()
        args += [
            "schedule",
            "create",
            "--policy-type",
            "count",
            "--keep-count",
            "2",
            "--calendarspec",
            "hourly",
            "hourly",
        ]
        args.extend(self.mount_points())
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "show", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "show", "--json", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "list"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "gc", "--config", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "delete", "hourly"]
        self.assertEqual(command.main(args), 0)

    def test_main_schedule_create_show_list_gc_delete_AGE(self):
        args = self.get_debug_main_args()
        args += [
            "schedule",
            "create",
            "--policy-type",
            "age",
            "--keep-weeks",
            "4",
            "--calendarspec",
            "hourly",
            "hourly",
        ]
        args.extend(self.mount_points())
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "show", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "show", "--json", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "list"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "gc", "--config", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "delete", "hourly"]
        self.assertEqual(command.main(args), 0)

    def test_main_schedule_create_show_list_gc_delete_TIMELINE(self):
        args = self.get_debug_main_args()
        args += [
            "schedule",
            "create",
            "--policy-type",
            "timeline",
            "--keep-monthly",
            "6",
            "--keep-weekly",
            "4",
            "--keep-daily",
            "7",
            "--calendarspec",
            "hourly",
            "hourly",
        ]
        args.extend(self.mount_points())
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "show", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "show", "--json", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "list"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "gc", "--config", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "delete", "hourly"]
        self.assertEqual(command.main(args), 0)

    def test_main_schedule_create_disable_enable_delete(self):
        args = self.get_debug_main_args()
        args += [
            "schedule",
            "create",
            "--policy-type",
            "count",
            "--keep-count",
            "2",
            "--calendarspec",
            "hourly",
            "hourly",
        ]
        args.extend(self.mount_points())
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "disable", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "enable", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "delete", "hourly"]
        self.assertEqual(command.main(args), 0)

    def test_main_schedule_create_scheduled_delete(self):
        args = self.get_debug_main_args()
        args += [
            "schedule",
            "create",
            "--policy-type",
            "count",
            "--keep-count",
            "2",
            "--calendarspec",
            "hourly",
            "hourly",
        ]
        args.extend(self.mount_points())
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["snapset", "create-scheduled", "--config", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["schedule", "delete", "hourly"]
        self.assertEqual(command.main(args), 0)

        args = self.get_debug_main_args()
        args += ["snapset", "delete", "hourly.0"]
        self.assertEqual(command.main(args), 0)
