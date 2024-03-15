# Copyright (C) 2023 Red Hat, Inc., Bryn M. Reeves <bmr@redhat.com>
#
# command.py - Snapshot manager command interface
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
"""The ``snapm.command`` module provides both the snapm command line
interface infrastructure, and a simple procedural interface to the
``snapm`` library modules.

The procedural interface is used by the ``snapm`` command line tool,
and may be used by application programs, or interactively in the
Python shell by users who do not require all the features present
in the snapm object API.
"""
from argparse import ArgumentParser
from os.path import basename
import logging

from snapm import (
    SnapmInvalidIdentifierError,
    SNAPM_DEBUG_MANAGER,
    SNAPM_DEBUG_COMMAND,
    SNAPM_DEBUG_PLUGINS,
    SNAPM_DEBUG_REPORT,
    SNAPM_DEBUG_ALL,
    set_debug_mask,
    Selection,
    __version__,
)
from snapm.manager import Manager
from snapm.report import (
    REP_NUM,
    REP_SHA,
    REP_STR,
    REP_TIME,
    REP_UUID,
    REP_STR_LIST,
    ReportOpts,
    ReportObjType,
    FieldType,
    Report,
)

_log = logging.getLogger(__name__)
_log.set_debug_mask(SNAPM_DEBUG_COMMAND)

_log_debug = _log.debug
_log_debug_command = _log.debug_masked
_log_info = _log.info
_log_warn = _log.warn
_log_error = _log.error

_DEFAULT_LOG_LEVEL = logging.WARNING
_CONSOLE_HANDLER = None

#
# Reporting object types
#


class ReportObj:
    """
    Common report object for snapm reports
    """

    snapset = None
    snapshot = None

    def __init__(self, snapset=None, snapshot=None):
        self.snapset = snapset
        self.snapshot = snapshot


#: Snapshot set report object type
PR_SNAPSET = 1
#: Snapshot report object type
PR_SNAPSHOT = 2

#: Report object types table for ``snapm.command`` reports
_report_obj_types = [
    ReportObjType(PR_SNAPSET, "Snapshot set", "snapset_", lambda o: o.snapset),
    ReportObjType(PR_SNAPSHOT, "Snapshot", "snapshot_", lambda o: o.snapshot),
]


def _bool_to_yes_no(bval):
    """
    Convert boolean to yes/no string.

    :param bval: A boolean value to evaluate
    :returns: 'yes' if ``bval`` is ``True`` or ``False`` otherwise.
    """
    return "yes" if bval else "no"


#
# Report field definitions
#

_snapshot_set_fields = [
    FieldType(
        PR_SNAPSET,
        "name",
        "SnapsetName",
        "Snapshot set name",
        12,
        REP_STR,
        lambda f, d: f.report_str(d.name),
    ),
    FieldType(
        PR_SNAPSET,
        "uuid",
        "SnapsetUuid",
        "Snapshot set UUID",
        37,
        REP_UUID,
        lambda f, d: f.report_uuid(d.uuid),
    ),
    FieldType(
        PR_SNAPSET,
        "timestamp",
        "Timestamp",
        "Snapshot set creation time as a UNIX epoch value",
        10,
        REP_NUM,
        lambda f, d: f.report_num(d.timestamp),
    ),
    FieldType(
        PR_SNAPSET,
        "time",
        "Time",
        "Snapshot set creation time",
        20,
        REP_TIME,
        lambda f, d: f.report_time(d.time),
    ),
    FieldType(
        PR_SNAPSET,
        "nr_snapshots",
        "NrSnapshots",
        "Number of snapshots",
        11,
        REP_NUM,
        lambda f, d: f.report_num(d.nr_snapshots),
    ),
    FieldType(
        PR_SNAPSET,
        "mountpoints",
        "MountPoints",
        "Snapshot set mount points",
        24,
        REP_STR_LIST,
        lambda f, d: f.report_str_list(d.mount_points),
    ),
    FieldType(
        PR_SNAPSET,
        "status",
        "Status",
        "Snapshot set status",
        7,
        REP_STR,
        lambda f, d: f.report_str(str(d.status)),
    ),
    FieldType(
        PR_SNAPSET,
        "autoactivate",
        "Autoactivate",
        "Autoactivation status",
        12,
        REP_STR,
        lambda f, d: f.report_str(_bool_to_yes_no(d.autoactivate)),
    ),
    FieldType(
        PR_SNAPSET,
        "bootentry",
        "BootEntry",
        "Snapshot set boot entry",
        10,
        REP_SHA,
        lambda f, d: f.report_sha("" if not d.boot_entry else d.boot_entry.boot_id),
    ),
    FieldType(
        PR_SNAPSET,
        "rollbackentry",
        "RollbackEntry",
        "Snapshot set rollback boot entry",
        13,
        REP_SHA,
        lambda f, d: f.report_sha(
            "" if not d.rollback_entry else d.rollback_entry.boot_id
        ),
    ),
]

_DEFAULT_SNAPSET_FIELDS = "name,time,nr_snapshots,status,mountpoints"
_VERBOSE_SNAPSET_FIELDS = _DEFAULT_SNAPSET_FIELDS + ",autoactivate,uuid"

_snapshot_fields = [
    FieldType(
        PR_SNAPSHOT,
        "snapshot_name",
        "SnapshotName",
        "Snapshot name",
        24,
        REP_STR,
        lambda f, d: f.report_str(d.name),
    ),
    FieldType(
        PR_SNAPSHOT,
        "snapshot_uuid",
        "SnapshotUuid",
        "Snapshot UUID",
        37,
        REP_UUID,
        lambda f, d: f.report_uuid(d.uuid),
    ),
    FieldType(
        PR_SNAPSHOT,
        "origin",
        "Origin",
        "Snapshot origin",
        16,
        REP_STR,
        lambda f, d: f.report_str(d.origin),
    ),
    FieldType(
        PR_SNAPSHOT,
        "mountpoint",
        "MountPoint",
        "Snapshot mount point",
        16,
        REP_STR,
        lambda f, d: f.report_str(d.mount_point),
    ),
    FieldType(
        PR_SNAPSHOT,
        "devpath",
        "DevPath",
        "Snapshot device path",
        8,
        REP_STR,
        lambda f, d: f.report_str(d.devpath),
    ),
    FieldType(
        PR_SNAPSHOT,
        "provider",
        "Provider",
        "Snapshot provider plugin",
        8,
        REP_STR,
        lambda f, d: f.report_str(d.provider),
    ),
    FieldType(
        PR_SNAPSHOT,
        "status",
        "Status",
        "Snapshot status",
        7,
        REP_STR,
        lambda f, d: f.report_str(str(d.status)),
    ),
    FieldType(
        PR_SNAPSHOT,
        "autoactivate",
        "Autoactivate",
        "Autoactivation status",
        12,
        REP_STR,
        lambda f, d: f.report_str(_bool_to_yes_no(d.autoactivate)),
    ),
]

_DEFAULT_SNAPSHOT_FIELDS = "name,origin,mountpoint,status,autoactivate,devpath"
_VERBOSE_SNAPSHOT_FIELDS = _DEFAULT_SNAPSHOT_FIELDS + ",provider,snapshot_uuid"


def _str_indent(string, indent):
    """
    Indent all lines of a multi-line string.

    Indent each line of the multi line string ``string`` to the
    specified indentation level.

    :param string: The string to be indented
    :param indent: The number of characters to indent by
    :returns: str
    """
    outstr = ""
    for line in string.splitlines():
        outstr += indent * " " + line + "\n"
    return outstr.rstrip("\n")


def _do_print_type(
    report_fields, selected, output_fields=None, opts=None, sort_keys=None
):
    """
    Print an object type report (snapshot set, snapshot)

    Helper for list function that generate reports.

    Format a set of snapshot set or snapshot objects matching
    the given criteria and format them as a report, returning
    the output as a string.

    Selection criteria may be expressed via a Selection object
    passed to the call using the ``selection`` parameter.

    :param selection: A Selection object giving selection
                      criteria for the operation
    :param output_fields: a comma-separated list of output fields
    :param opts: output formatting and control options
    :param sort_keys: a comma-separated list of sort keys
    :rtype: str
    """
    opts = opts if opts is not None else ReportOpts()

    report = Report(
        _report_obj_types, report_fields, output_fields, opts, sort_keys, None
    )

    for obj in selected:
        report.report_object(obj)

    return report.report_output()


def create_snapset(manager, name, mount_points, boot=False, rollback=False):
    """
    Create a new snapshot set from a list of mount points.

    :param manager: The manager context to use
    :param name: The name of the new snapshot set
    :param mount_points: A list of mount points to snapshot
    """
    snapset = manager.create_snapshot_set(name, mount_points)

    # Snapshot sets must be active to create boot entries.
    if boot or rollback:
        select = Selection(name=snapset.name)
        manager.activate_snapshot_sets(select)

    if boot:
        try:
            manager.create_snapshot_set_boot_entry(name=snapset.name)
        except (OSError, ValueError) as err:
            _log_error("Failed to create snapshot set boot entry: %s", err)
            manager.delete_snapshot_sets(select)
            return None
    if rollback:
        try:
            manager.create_snapshot_set_rollback_entry(name=snapset.name)
        except (OSError, ValueError) as err:
            _log_error("Failed to create snapshot set rollback boot entry: %s", err)
            manager.delete_snapshot_sets(select)
            return None
    return snapset


def delete_snapset(manager, selection):
    """
    Delete snapshot set matching selection criteria.

    :param manager: The manager context to use
    :param selection: Selection criteria for the snapshot set to remove.
    """
    if not selection.is_single():
        raise SnapmInvalidIdentifierError("Delete requires unique selection criteria")
    return manager.delete_snapshot_sets(selection)


def rename_snapset(manager, old_name, new_name):
    """
    Rename a snapshot set from ``old_name`` to ``new_name``.
    """
    return manager.rename_snapshot_set(old_name, new_name)


def rollback_snapset(manager, selection):
    """
    Roll back snapshot set matching selection criteria.

    :param manager: The manager context to use
    :param selection: Selection criteria for the snapshot set to roll back.
    """
    if not selection.is_single():
        raise SnapmInvalidIdentifierError(
            "Roll back requires unique selection criteria"
        )
    return manager.rollback_snapshot_sets(selection)


def show_snapshots(manager, selection=None):
    """
    Show snapshots matching selection criteria.
    """
    snapshots = manager.find_snapshots(selection=selection)
    for snapshot in snapshots:
        print(snapshot)
        print()


def show_snapsets(manager, selection=None, members=False):
    """
    Show snapshot sets matching selection criteria.
    """
    snapsets = manager.find_snapshot_sets(selection=selection)
    for snapset in snapsets:
        print(snapset)
        if members:
            print("Snapshots:\n")
            for snapshot in snapset.snapshots:
                print(_str_indent(str(snapshot), 4))
                print()
        print()


def _expand_fields(default_fields, output_fields):
    """
    Expand output fields list from command line arguments.
    """

    if not output_fields:
        output_fields = default_fields
    elif output_fields.startswith("+"):
        output_fields = default_fields + "," + output_fields[1:]
    return output_fields


def print_snapshots(
    manager, selection=None, output_fields=None, opts=None, sort_keys=None
):
    """
    Print snapshots matching selection criteria.

    Format a set of ``snapm.manager.Snapshot`` objects matching
    the given criteria, and output them as a report to the file
    given in ``opts.report_file``.

    Selection criteria may be expressed via a Selection object
    passed to the call using the ``selection`` parameter.

    :param selection: A Selection object giving selection
                      criteria for the operation
    :param output_fields: a comma-separated list of output fields
    :param opts: output formatting and control options
    :param sort_keys: a comma-separated list of sort keys
    """
    output_fields = _expand_fields(_DEFAULT_SNAPSHOT_FIELDS, output_fields)

    snapshots = manager.find_snapshots(selection=selection)
    selected = [ReportObj(snap.snapshot_set, snap) for snap in snapshots]

    return _do_print_type(
        _snapshot_fields + _snapshot_set_fields,
        selected,
        output_fields=output_fields,
        opts=opts,
        sort_keys=sort_keys,
    )


def print_snapsets(
    manager, selection=None, output_fields=None, opts=None, sort_keys=None
):
    """
    Print snapshot sets matching selection criteria.

    Format a set of ``snapm.manager.SnapshotSet`` objects matching
    the given criteria, and output them as a report to the file
    given in ``opts.report_file``.

    Selection criteria may be expressed via a Selection object
    passed to the call using the ``selection`` parameter.

    :param selection: A Selection object giving selection
                      criteria for the operation
    :param output_fields: a comma-separated list of output fields
    :param opts: output formatting and control options
    :param sort_keys: a comma-separated list of sort keys
    """
    output_fields = _expand_fields(_DEFAULT_SNAPSET_FIELDS, output_fields)

    snapshot_sets = manager.find_snapshot_sets(selection=selection)
    selected = [ReportObj(ss, None) for ss in snapshot_sets]

    return _do_print_type(
        _snapshot_set_fields,
        selected,
        output_fields=output_fields,
        opts=opts,
        sort_keys=sort_keys,
    )


def _generic_list_cmd(cmd_args, select, opts, manager, verbose_fields, print_fn):
    """
    Generic list command implementation.

    Implements a simple list command that applies selection criteria
    and calls a print_*() API function to display results.

    Callers should initialise identifier and select appropriately
    for the specific command arguments.

    :param cmd_args: the command arguments
    :param select: selection criteria
    :param opts: reporting options object
    :param print_fn: the API call to display results. The function
                     must accept the selection, output_fields,
                     opts, and sort_keys keyword arguments
    :returns: None
    """
    if cmd_args.options:
        fields = cmd_args.options
    elif cmd_args.verbose:
        fields = verbose_fields
    else:
        fields = None

    if cmd_args.debug:
        print_fn(
            manager,
            selection=select,
            output_fields=fields,
            opts=opts,
            sort_keys=cmd_args.sort,
        )
    else:
        try:
            print_fn(
                manager,
                selection=select,
                output_fields=fields,
                opts=opts,
                sort_keys=cmd_args.sort,
            )
        except ValueError as err:
            print(err)
            return 1
    return 0


def _create_cmd(cmd_args):
    """
    Create snapshot set command handler.
    Attempt to create the specified snapshot set.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    snapset = create_snapset(
        manager,
        cmd_args.snapset_name,
        cmd_args.mount_points,
        boot=cmd_args.bootable,
        rollback=cmd_args.rollback,
    )
    if snapset is None:
        return 1
    _log_info(
        "Created snapset %s with %d snapshots", snapset.name, snapset.nr_snapshots
    )
    print(snapset)
    return 0


def _delete_cmd(cmd_args):
    """
    Delete snapshot set command handler.

    Attempt to delete the specified snapshot set.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    select = Selection.from_cmd_args(cmd_args)
    count = delete_snapset(manager, select)
    _log_info("Deleted %d snapshot sets", count)
    return 0


def _rename_cmd(cmd_args):
    """
    Rename snapshot set command handler.

    Attempt to rename the specified snapshot set.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    rename_snapset(manager, cmd_args.old_name, cmd_args.new_name)
    _log_info("Renamed snapshot set '%s' to '%s'", cmd_args.old_name, cmd_args.new_name)
    return 0


def _rollback_cmd(cmd_args):
    """
    Delete snapshot set command handler.

    Attempt to roll back the specified snapshot set.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    select = Selection.from_cmd_args(cmd_args)
    count = rollback_snapset(manager, select)
    _log_info("Set %d snapshot sets for roll back", count)
    return 0


def _activate_cmd(cmd_args):
    """
    Activate snapshot set command handler.

    Attempt to activate the snapshot sets that match the given
    selection criteria.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    select = Selection.from_cmd_args(cmd_args)
    count = manager.activate_snapshot_sets(select)
    _log_info("Activated %d snapshot sets", count)
    return 0


def _deactivate_cmd(cmd_args):
    """
    Deactivate snapshot set command handler.

    Attempt to deactivate the snapshot sets that match the given
    selection criteria.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    select = Selection.from_cmd_args(cmd_args)
    count = manager.deactivate_snapshot_sets(select)
    _log_info("Deactivated %d snapshot sets", count)
    return 0


def _autoactivate_cmd(cmd_args):
    """
    Autoactivation status snapshot set command handler.

    Attempt to set the autoactivation status for snapshot set that match
    the given selection criteria.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    select = Selection.from_cmd_args(cmd_args)
    auto = bool(cmd_args.yes)
    count = manager.set_autoactivate(select, auto=auto)
    _log_info("Set autoactivate=%s for %d snapshot sets", auto, count)
    return 0


def _list_cmd(cmd_args):
    """
    List snapshot sets command handler.

    List the snapshot sets that match the given selection criteria as
    a tabular report, with one snapshot set per row.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    opts = _report_opts_from_args(cmd_args)
    select = Selection.from_cmd_args(cmd_args)
    return _generic_list_cmd(
        cmd_args, select, opts, manager, _VERBOSE_SNAPSET_FIELDS, print_snapsets
    )


def _show_cmd(cmd_args):
    """
    Show snapshot set command handler.

    Show the snapshot sets that match the given selection criteria as
    a multi-line report.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    select = Selection.from_cmd_args(cmd_args)
    show_snapsets(manager, selection=select, members=cmd_args.verbose)
    return 0


def _activate_snapshot_cmd(cmd_args):
    """
    Activate snapshot command handler.

    Attempt to activate the snapshots that match the given
    selection criteria.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    select = Selection.from_cmd_args(cmd_args)
    snapshots = manager.find_snapshots(select)
    if not snapshots:
        _log_error("Could not find snapshots matching %s", select)
        return 1
    count = 0
    for snapshot in snapshots:
        snapshot.activate()
        count += 1
    _log_info("Activated %d snapshots", count)
    return 0


def _deactivate_snapshot_cmd(cmd_args):
    """
    Deactivate snapshot command handler.

    Attempt to deactivate the snapshots that match the given
    selection criteria.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    select = Selection.from_cmd_args(cmd_args)
    snapshots = manager.find_snapshots(select)
    if not snapshots:
        _log_error("Could not find snapshots matching %s", select)
        return 1
    count = 0
    for snapshot in snapshots:
        snapshot.deactivate()
        count += 1
    _log_info("Deactivated %d snapshots", count)
    return 0


def _autoactivate_snapshot_cmd(cmd_args):
    """
    Autoactivate snapshot command handler.

    Attempt to set autoactivation status for the snapshots that match
    the given selection criteria.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    select = Selection.from_cmd_args(cmd_args)
    snapshots = manager.find_snapshots(select)
    if not snapshots:
        _log_error("Could not find snapshots matching %s", select)
        return 1
    count = 0
    auto = bool(cmd_args.yes)
    for snapshot in snapshots:
        snapshot.set_autoactivate(auto=auto)
        count += 1
    _log_info("Set autoactivation status for %d snapshots", count)
    return 0


def _list_snapshot_cmd(cmd_args):
    """
    List snapshots command handler.

    List the snapshot that match the given selection criteria as
    a tabular report, with one snapshot per row.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    opts = _report_opts_from_args(cmd_args)
    select = Selection.from_cmd_args(cmd_args)
    return _generic_list_cmd(
        cmd_args, select, opts, manager, _VERBOSE_SNAPSHOT_FIELDS, print_snapshots
    )


def _show_snapshot_cmd(cmd_args):
    """
    Show snapshots command handler.

    Show the snapshots that match the given selection criteria as
    a multi-line report.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    select = Selection.from_cmd_args(cmd_args)
    show_snapshots(manager, selection=select)
    return 0


def _report_opts_from_args(cmd_args):
    opts = ReportOpts()

    if not cmd_args:
        return opts

    if cmd_args.rows:
        opts.columns_as_rows = True

    if cmd_args.separator:
        opts.separator = cmd_args.separator

    if cmd_args.name_prefixes:
        opts.field_name_prefix = "SNAPM_"
        opts.unquoted = False
        opts.aligned = False

    if cmd_args.no_headings:
        opts.headings = False

    return opts


def setup_logging(cmd_args):
    """
    Set up snapm logging.
    """
    global _CONSOLE_HANDLER
    level = _DEFAULT_LOG_LEVEL
    if cmd_args.verbose and cmd_args.verbose > 1:
        level = logging.DEBUG
    elif cmd_args.verbose and cmd_args.verbose > 0:
        level = logging.INFO
    snapm_log = logging.getLogger("snapm")
    formatter = logging.Formatter("%(levelname)s - %(message)s")
    snapm_log.setLevel(level)
    _CONSOLE_HANDLER = logging.StreamHandler()
    _CONSOLE_HANDLER.setLevel(level)
    _CONSOLE_HANDLER.setFormatter(formatter)
    snapm_log.addHandler(_CONSOLE_HANDLER)


def shutdown_logging():
    """
    Shut down snapm logging.
    """
    logging.shutdown()


def set_debug(debug_arg):
    """
    Set debugging mask from command line argument.
    """
    if not debug_arg:
        return

    mask_map = {
        "manager": SNAPM_DEBUG_MANAGER,
        "command": SNAPM_DEBUG_COMMAND,
        "plugins": SNAPM_DEBUG_PLUGINS,
        "report": SNAPM_DEBUG_REPORT,
        "all": SNAPM_DEBUG_ALL,
    }

    mask = 0
    for name in debug_arg.split(","):
        if name not in mask_map:
            raise ValueError(f"Unknown debug option: {name}")
        mask |= mask_map[name]
    set_debug_mask(mask)


def _add_identifier_args(parser, snapset=False, snapshot=False):
    """
    Add snapshot set or snapshot identifier command line arguments.
    """
    if snapset:
        parser.add_argument(
            "-n",
            "--name",
            metavar="NAME",
            type=str,
            help="A snapset name",
        )
        parser.add_argument(
            "-u",
            "--uuid",
            metavar="UUID",
            type=str,
            help="A snapset UUID",
        )
    if snapshot:
        parser.add_argument(
            "-N",
            "--snapshot-name",
            metavar="SNAPSHOT_NAME",
            type=str,
            help="A snapshot name",
        )
        parser.add_argument(
            "-U",
            "--snapshot-uuid",
            metavar="SNAPSHOT_UUID",
            type=str,
            help="A snapshot UUID",
        )
    parser.add_argument(
        "identifier",
        metavar="ID",
        type=str,
        action="store",
        help="An optional snapset name or UUID to operate on",
        nargs="?",
        default=None,
    )


def _add_report_args(parser):
    """
    Add common reporting command line arguments.
    """
    parser.add_argument(
        "--name-prefixes",
        "--nameprefixes",
        help="Add a prefix to report field names",
        action="store_true",
    )
    parser.add_argument(
        "--no-headings",
        "--noheadings",
        action="store_true",
        help="Suppress output of report headings",
    )
    parser.add_argument(
        "-o",
        "--options",
        metavar="FIELDS",
        type=str,
        help="Specify which fields to display",
    )
    parser.add_argument(
        "-O",
        "--sort",
        metavar="SORTFIELDS",
        type=str,
        help="Specify which fields to sort by",
    )
    parser.add_argument(
        "--rows", action="store_true", help="Output report columnes as rows"
    )
    parser.add_argument(
        "--separator", metavar="SEP", type=str, help="Report field separator"
    )


def _add_autoactivate_args(parser):
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Enable snapshot autoactivation",
    )
    parser.add_argument(
        "--no",
        action="store_true",
        help="Disable snapshot autoactivation",
    )


CREATE_CMD = "create"
DELETE_CMD = "delete"
RENAME_CMD = "rename"
ROLLBACK_CMD = "rollback"
ACTIVATE_CMD = "activate"
DEACTIVATE_CMD = "deactivate"
AUTOACTIVATE_CMD = "autoactivate"
SHOW_CMD = "show"
LIST_CMD = "list"

SNAPSET_TYPE = "snapset"
SNAPSHOT_TYPE = "snapshot"


def main(args):
    """
    Main entry point for snapm.
    """
    parser = ArgumentParser(description="Snapshot Manager", prog=basename(args[0]))

    # Global arguments
    parser.add_argument(
        "-d",
        "--debug",
        metavar="DEBUGOPTS",
        type=str,
        help="A list of debug options to enable",
    )
    parser.add_argument("-v", "--verbose", help="Enable verbose output", action="count")
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        help="Report the version number of snapm",
        version=__version__,
    )
    # Subparser for command type
    type_subparser = parser.add_subparsers(dest="type", help="Command type")

    # Subparser for snapset commands
    snapset_parser = type_subparser.add_parser(
        SNAPSET_TYPE, help="Snapshot set commands"
    )
    snapset_subparser = snapset_parser.add_subparsers(dest="command")

    # snapset create subcommand
    snapset_create_parser = snapset_subparser.add_parser(
        CREATE_CMD, help="Create snapshot sets"
    )
    snapset_create_parser.set_defaults(func=_create_cmd)
    snapset_create_parser.add_argument(
        "snapset_name",
        metavar="SNAPSET_NAME",
        type=str,
        action="store",
        help="The name of the snapshot set to create",
    )
    snapset_create_parser.add_argument(
        "mount_points",
        metavar="MOUNT_POINT",
        type=str,
        nargs="+",
        help="A list of mount points to include in this snapshot set",
    )
    snapset_create_parser.add_argument(
        "-b",
        "--bootable",
        action="store_true",
        help="Create a boot entry for this snapshot set",
    )
    snapset_create_parser.add_argument(
        "-r",
        "--rollback",
        action="store_true",
        help="Create a rollback boot entry for this snapshot set",
    )

    # snapset delete subcommand
    snapset_delete_parser = snapset_subparser.add_parser(
        DELETE_CMD, help="Delete snapshot sets"
    )
    snapset_delete_parser.set_defaults(func=_delete_cmd)
    _add_identifier_args(snapset_delete_parser, snapset=True)

    # snapset rename subcommand
    snapset_rename_parser = snapset_subparser.add_parser(
        RENAME_CMD, help="Rename a snapshot set"
    )
    snapset_rename_parser.set_defaults(func=_rename_cmd)
    snapset_rename_parser.add_argument(
        "old_name",
        metavar="OLD_NAME",
        type=str,
        action="store",
        help="The name of the snapshot set to be renamed",
    )
    snapset_rename_parser.add_argument(
        "new_name",
        metavar="NEW_NAME",
        type=str,
        action="store",
        help="The new name of the snapshot set to be renamed",
    )

    # snapset rollback command
    snapset_rollback_parser = snapset_subparser.add_parser(
        ROLLBACK_CMD, help="Roll back snapshot sets"
    )
    snapset_rollback_parser.set_defaults(func=_rollback_cmd)
    _add_identifier_args(snapset_rollback_parser, snapset=True)

    # snapset activate subcommand
    snapset_activate_parser = snapset_subparser.add_parser(
        ACTIVATE_CMD, help="Activate snapshot sets"
    )
    snapset_activate_parser.set_defaults(func=_activate_cmd)
    _add_identifier_args(snapset_activate_parser, snapset=True)

    # snapset deactivate subcommand
    snapset_deactivate_parser = snapset_subparser.add_parser(
        DEACTIVATE_CMD, help="Deactivate snapshot sets"
    )
    snapset_deactivate_parser.set_defaults(func=_deactivate_cmd)
    _add_identifier_args(snapset_deactivate_parser, snapset=True)

    # snapset autoactivate command
    snapset_autoactivate_parser = snapset_subparser.add_parser(
        AUTOACTIVATE_CMD, help="Set autoactivation status for snapshot sets"
    )
    snapset_autoactivate_parser.set_defaults(func=_autoactivate_cmd)
    _add_identifier_args(snapset_autoactivate_parser, snapset=True)
    _add_autoactivate_args(snapset_autoactivate_parser)

    # snapset list subcommand
    snapset_list_parser = snapset_subparser.add_parser(
        LIST_CMD, help="List snapshot sets"
    )
    snapset_list_parser.set_defaults(func=_list_cmd)
    _add_report_args(snapset_list_parser)
    _add_identifier_args(snapset_list_parser, snapset=True)

    # snapset show subcommand
    snapset_show_parser = snapset_subparser.add_parser(
        SHOW_CMD, help="Display snapshot sets"
    )
    snapset_show_parser.set_defaults(func=_show_cmd)
    _add_identifier_args(snapset_show_parser, snapset=True)

    # Subparser for snapshot commands
    snapshot_parser = type_subparser.add_parser(SNAPSHOT_TYPE, help="Snapshot commands")
    snapshot_subparser = snapshot_parser.add_subparsers(dest="command")

    # snapshot activate subcommand
    snapshot_activate_parser = snapshot_subparser.add_parser(
        ACTIVATE_CMD, help="Activate snapshots"
    )
    _add_identifier_args(snapshot_activate_parser, snapset=True, snapshot=True)
    snapshot_activate_parser.set_defaults(func=_activate_snapshot_cmd)

    # snapshot deactivate subcommand
    snapshot_deactivate_parser = snapshot_subparser.add_parser(
        DEACTIVATE_CMD, help="Deactivate snapshots"
    )
    _add_identifier_args(snapshot_deactivate_parser, snapset=True, snapshot=True)
    snapshot_deactivate_parser.set_defaults(func=_deactivate_snapshot_cmd)

    # snapshot autoactivate command
    snapshot_autoactivate_parser = snapshot_subparser.add_parser(
        AUTOACTIVATE_CMD, help="Set autoactivation status for snapshots"
    )
    snapshot_autoactivate_parser.set_defaults(func=_autoactivate_snapshot_cmd)
    _add_identifier_args(snapshot_autoactivate_parser, snapset=True, snapshot=True)
    _add_autoactivate_args(snapshot_autoactivate_parser)

    # snapshot list subcommand
    snapshot_list_parser = snapshot_subparser.add_parser(
        LIST_CMD, help="List snapshots"
    )
    snapshot_list_parser.set_defaults(func=_list_snapshot_cmd)
    _add_report_args(snapshot_list_parser)
    _add_identifier_args(snapshot_list_parser, snapset=True, snapshot=True)

    # snapshot show subcommand
    snapshot_show_parser = snapshot_subparser.add_parser(
        SHOW_CMD, help="Display snapshots"
    )
    snapshot_show_parser.set_defaults(func=_show_snapshot_cmd)
    _add_identifier_args(snapshot_show_parser, snapset=True, snapshot=True)

    cmd_args = parser.parse_args(args[1:])

    status = 1

    try:
        set_debug(cmd_args.debug)
    except ValueError as err:
        print(err)
        parser.print_help()
        return status

    setup_logging(cmd_args)

    _log_debug_command("Parsed %s", " ".join(args[1:]))

    if "func" not in cmd_args:
        parser.print_help()
        return status

    if cmd_args.debug:
        status = cmd_args.func(cmd_args)
    else:
        try:
            status = cmd_args.func(cmd_args)
        except Exception as err:
            _log_error("Command failed: %s", err)

    shutdown_logging()
    return status


# vim: set et ts=4 sw=4 :
