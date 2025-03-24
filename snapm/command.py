# Copyright Red Hat
#
# snapm/command.py - Snapshot manager command interface
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: GPL-2.0-only
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
from json import dumps
from uuid import UUID
import logging

from snapm import (
    SnapmInvalidIdentifierError,
    SNAPSET_NAME,
    SNAPSET_SOURCES,
    SNAPSET_MOUNT_POINTS,
    SNAPSET_DEVICES,
    SNAPSET_NR_SNAPSHOTS,
    SNAPSET_TIME,
    SNAPSET_TIMESTAMP,
    SNAPSET_UUID,
    SNAPSET_STATUS,
    SNAPSET_AUTOACTIVATE,
    SNAPSET_BOOTABLE,
    SNAPSET_SNAPSHOT_ENTRY,
    SNAPSET_REVERT_ENTRY,
    SNAPSHOT_NAME,
    SNAPSHOT_ORIGIN,
    SNAPSHOT_SOURCE,
    SNAPSHOT_MOUNT_POINT,
    SNAPSHOT_PROVIDER,
    SNAPSHOT_UUID,
    SNAPSHOT_STATUS,
    SNAPSHOT_SIZE,
    SNAPSHOT_FREE,
    SNAPSHOT_SIZE_BYTES,
    SNAPSHOT_FREE_BYTES,
    SNAPSHOT_AUTOACTIVATE,
    SNAPSHOT_DEV_PATH,
    SNAPM_DEBUG_MANAGER,
    SNAPM_DEBUG_COMMAND,
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
    REP_SIZE,
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
_log_warn = _log.warning
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

    def __init__(self, snapset=None, snapshot=None, plugin=None):
        self.snapset = snapset
        self.snapshot = snapshot
        self.plugin = plugin


#: Snapshot set report object type
PR_SNAPSET = 1
#: Snapshot report object type
PR_SNAPSHOT = 2
#: Plugin report object type
PR_PLUGIN = 4

#: Report object types table for ``snapm.command`` reports
_report_obj_types = [
    ReportObjType(PR_SNAPSET, "Snapshot set", "snapset_", lambda o: o.snapset),
    ReportObjType(PR_SNAPSHOT, "Snapshot", "snapshot_", lambda o: o.snapshot),
    ReportObjType(PR_PLUGIN, "Plugin", "plugin_", lambda o: o.plugin),
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
        SNAPSET_NAME,
        "Snapshot set name",
        12,
        REP_STR,
        lambda f, d: f.report_str(d.name),
    ),
    FieldType(
        PR_SNAPSET,
        "uuid",
        SNAPSET_UUID,
        "Snapshot set UUID",
        37,
        REP_UUID,
        lambda f, d: f.report_uuid(d.uuid),
    ),
    FieldType(
        PR_SNAPSET,
        "timestamp",
        SNAPSET_TIMESTAMP,
        "Snapshot set creation time as a UNIX epoch value",
        10,
        REP_NUM,
        lambda f, d: f.report_num(d.timestamp),
    ),
    FieldType(
        PR_SNAPSET,
        "time",
        SNAPSET_TIME,
        "Snapshot set creation time",
        20,
        REP_TIME,
        lambda f, d: f.report_time(d.time),
    ),
    FieldType(
        PR_SNAPSET,
        "nr_snapshots",
        SNAPSET_NR_SNAPSHOTS,
        "Number of snapshots",
        11,
        REP_NUM,
        lambda f, d: f.report_num(d.nr_snapshots),
    ),
    FieldType(
        PR_SNAPSET,
        "sources",
        SNAPSET_SOURCES,
        "Snapshot set sources",
        8,
        REP_STR_LIST,
        lambda f, d: f.report_str_list(d.sources),
    ),
    FieldType(
        PR_SNAPSET,
        "mountpoints",
        SNAPSET_MOUNT_POINTS,
        "Snapshot set mount points",
        24,
        REP_STR_LIST,
        lambda f, d: f.report_str_list(d.mount_points),
    ),
    FieldType(
        PR_SNAPSET,
        "devices",
        SNAPSET_DEVICES,
        "Snapshot set devices",
        9,
        REP_STR_LIST,
        lambda f, d: f.report_str_list(d.devices),
    ),
    FieldType(
        PR_SNAPSET,
        "status",
        SNAPSET_STATUS,
        "Snapshot set status",
        7,
        REP_STR,
        lambda f, d: f.report_str(str(d.status)),
    ),
    FieldType(
        PR_SNAPSET,
        "autoactivate",
        SNAPSET_AUTOACTIVATE,
        "Autoactivation status",
        12,
        REP_STR,
        lambda f, d: f.report_str(_bool_to_yes_no(d.autoactivate)),
    ),
    FieldType(
        PR_SNAPSET,
        "bootable",
        SNAPSET_BOOTABLE,
        "Configured for snapshot boot",
        8,
        REP_STR,
        lambda f, d: f.report_str(_bool_to_yes_no(d.boot_entry is not None)),
    ),
    FieldType(
        PR_SNAPSET,
        "bootentry",
        SNAPSET_SNAPSHOT_ENTRY,
        "Snapshot set boot entry",
        13,
        REP_SHA,
        lambda f, d: f.report_sha("" if not d.boot_entry else d.boot_entry.boot_id),
    ),
    FieldType(
        PR_SNAPSET,
        "revertentry",
        SNAPSET_REVERT_ENTRY,
        "Snapshot set revert boot entry",
        13,
        REP_SHA,
        lambda f, d: f.report_sha("" if not d.revert_entry else d.revert_entry.boot_id),
    ),
]

_DEFAULT_SNAPSET_FIELDS = "name,time,nr_snapshots,status,sources"

_snapshot_fields = [
    FieldType(
        PR_SNAPSHOT,
        "name",
        SNAPSHOT_NAME,
        "Snapshot name",
        24,
        REP_STR,
        lambda f, d: f.report_str(d.name),
    ),
    FieldType(
        PR_SNAPSHOT,
        "uuid",
        SNAPSHOT_UUID,
        "Snapshot UUID",
        37,
        REP_UUID,
        lambda f, d: f.report_uuid(d.uuid),
    ),
    FieldType(
        PR_SNAPSHOT,
        "origin",
        "Origin",
        SNAPSHOT_ORIGIN,
        16,
        REP_STR,
        lambda f, d: f.report_str(d.origin),
    ),
    FieldType(
        PR_SNAPSHOT,
        "source",
        SNAPSHOT_SOURCE,
        "Snapshot source",
        7,
        REP_STR,
        lambda f, d: f.report_str(d.source),
    ),
    FieldType(
        PR_SNAPSHOT,
        "mountpoint",
        SNAPSHOT_MOUNT_POINT,
        "Snapshot mount point",
        16,
        REP_STR,
        lambda f, d: f.report_str(d.mount_point),
    ),
    FieldType(
        PR_SNAPSHOT,
        "devpath",
        SNAPSHOT_DEV_PATH,
        "Snapshot device path",
        8,
        REP_STR,
        lambda f, d: f.report_str(d.devpath),
    ),
    FieldType(
        PR_SNAPSHOT,
        "provider",
        SNAPSHOT_PROVIDER,
        "Snapshot provider plugin",
        8,
        REP_STR,
        lambda f, d: f.report_str(d.provider.name),
    ),
    FieldType(
        PR_SNAPSHOT,
        "status",
        SNAPSHOT_STATUS,
        "Snapshot status",
        7,
        REP_STR,
        lambda f, d: f.report_str(str(d.status)),
    ),
    FieldType(
        PR_SNAPSHOT,
        "size",
        SNAPSHOT_SIZE,
        "Snapshot size",
        6,
        REP_SIZE,
        lambda f, d: f.report_size(d.size),
    ),
    FieldType(
        PR_SNAPSHOT,
        "free",
        SNAPSHOT_FREE,
        "Free space available",
        6,
        REP_SIZE,
        lambda f, d: f.report_size(d.free),
    ),
    FieldType(
        PR_SNAPSHOT,
        "size_bytes",
        SNAPSHOT_SIZE_BYTES,
        "Snapshot size in bytes",
        6,
        REP_NUM,
        lambda f, d: f.report_num(d.size),
    ),
    FieldType(
        PR_SNAPSHOT,
        "free_bytes",
        SNAPSHOT_FREE_BYTES,
        "Free space available in bytes",
        6,
        REP_NUM,
        lambda f, d: f.report_num(d.free),
    ),
    FieldType(
        PR_SNAPSHOT,
        "autoactivate",
        SNAPSHOT_AUTOACTIVATE,
        "Autoactivation status",
        12,
        REP_STR,
        lambda f, d: f.report_str(_bool_to_yes_no(d.autoactivate)),
    ),
]

_DEFAULT_SNAPSHOT_FIELDS = (
    "snapset_name,name,origin,source,status,size,free,autoactivate,provider"
)

_plugin_fields = [
    FieldType(
        PR_PLUGIN,
        "name",
        "PluginName",
        "Name of the plugin",
        10,
        REP_STR,
        lambda f, d: f.report_str(d.name),
    ),
    FieldType(
        PR_PLUGIN,
        "version",
        "PluginVersion",
        "Version of the plugin",
        13,
        REP_STR,
        lambda f, d: f.report_str(d.version),
    ),
    FieldType(
        PR_PLUGIN,
        "type",
        "PluginType",
        "The snapshot type created by this plugin",
        10,
        REP_STR,
        lambda f, d: f.report_str(d.snapshot_class.__name__),
    ),
]

_DEFAULT_PLUGIN_FIELDS = "name,version,type"


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
    report_fields, selected, output_fields=None, opts=None, sort_keys=None, title=None
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
        _report_obj_types, report_fields, output_fields, opts, sort_keys, title
    )

    for obj in selected:
        report.report_object(obj)

    return report.report_output()


def create_snapset(manager, name, sources, size_policy=None, boot=False, revert=False):
    """
    Create a new snapshot set from a list of mount point and block device
    source paths.

    :param manager: The manager context to use
    :param name: The name of the new snapshot set
    :param sources: A list of mount point or block devices to snapshot
    :param size_policy: The default size policy for this snapshot set.
    :param boot: Create a boot entry for this snapshot set.
    :param revert: Create a revert boot entry for this snapshot set.
    """
    return manager.create_snapshot_set(
        name, sources, default_size_policy=size_policy, boot=boot, revert=revert
    )


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


def resize_snapset(manager, sources, name=None, uuid=None, default_size_policy=None):
    """
    Resize snapshot set by name or UUID.

    :param manager: The manager context to use
    :param sources: A list of mount point or block devices to snapshot
    :param name: The name of the snapshot set to resize.
    :param uuid: The uuid of the snapshot set to resize.
    :param default_size_policy: The default size policy for this snapshot set.
    """
    return manager.resize_snapshot_set(
        sources, name=name, uuid=uuid, default_size_policy=default_size_policy
    )


def revert_snapset(manager, name=None, uuid=None):
    """
    Revert snapshot set matching selection criteria.

    :param manager: The manager context to use
    :param selection: Selection criteria for the snapshot set to revert.
    """
    return manager.revert_snapshot_set(name=name, uuid=uuid)


def show_snapshots(manager, selection=None, json=False):
    """
    Show snapshots matching selection criteria.
    """
    snapshots = manager.find_snapshots(selection=selection)
    first = True

    if json:
        snap_list = []

    for snapshot in snapshots:
        if json:
            snap_list.append(snapshot.as_dict())
            continue
        wspace = "" if first else "\n"
        print(f"{wspace}{snapshot}")
        first = False

    if json:
        print(dumps(snap_list, indent=4))


def show_snapsets(manager, selection=None, members=False, json=False):
    """
    Show snapshot sets matching selection criteria.
    """
    snapsets = manager.find_snapshot_sets(selection=selection)
    first = True

    if json:
        set_list = []

    for snapset in snapsets:
        if json:
            set_list.append(snapset.as_dict(members=members))
            continue
        wspace = "" if first else "\n"
        print(f"{wspace}{snapset}")
        first = False
        if members:
            print("Snapshots:")
            first_snapshot = True
            for snapshot in snapset.snapshots:
                wspace = "" if first_snapshot else "\n"
                print(wspace + _str_indent(str(snapshot), 4))
                first_snapshot = False

    if json:
        print(dumps(set_list, indent=4))


def _expand_fields(default_fields, output_fields):
    """
    Expand output fields list from command line arguments.
    """

    if not output_fields:
        output_fields = default_fields
    elif output_fields.startswith("+"):
        output_fields = default_fields + "," + output_fields[1:]
    return output_fields


def print_plugins(
    manager, selection=None, output_fields=None, opts=None, sort_keys=None
):
    """
    Print plugins matching selection criteria.

    Format a set of ``snapm.manager.Plugin`` objects matching
    the given criteria, and output them as a report to the file
    given in ``opts.report_file``.

    Selection criteria are currently ignored for plugin reports.

    :param selection: A Selection object giving selection
                      criteria for the operation
    :param output_fields: a comma-separated list of output fields
    :param opts: output formatting and control options
    :param sort_keys: a comma-separated list of sort keys
    """
    del selection
    output_fields = _expand_fields(_DEFAULT_PLUGIN_FIELDS, output_fields)

    plugins = manager.plugins
    selected = [ReportObj(None, None, plugin) for plugin in plugins]

    return _do_print_type(
        _plugin_fields,
        selected,
        output_fields=output_fields,
        opts=opts,
        sort_keys=sort_keys,
        title="Plugins",
    )


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
        title="Snapshots",
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
        title="Snapsets",
    )


def _generic_list_cmd(cmd_args, select, opts, manager, print_fn):
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
        cmd_args.sources,
        size_policy=cmd_args.size_policy,
        boot=cmd_args.bootable,
        revert=cmd_args.revert,
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
    _log_info("Deleted %d snapshot set%s", count, "s" if count > 1 else "")
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


def _resize_cmd(cmd_args):
    """
    Resize snapshot set command handler.

    Attempt to resize the snapshots contained in the given snapshot set
    according to the corresponding size policy.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    name = None
    uuid = None

    selection = Selection.from_cmd_args(cmd_args)
    if not selection.is_single():
        raise SnapmInvalidIdentifierError("Resize requires unique selection criteria")
    if selection.name:
        name = selection.name
    elif selection.uuid:
        uuid = selection.uuid
    else:
        raise SnapmInvalidIdentifierError("Resize requires a snapset name or UUID")

    resize_snapset(
        manager,
        cmd_args.sources,
        name=name,
        uuid=uuid,
        default_size_policy=cmd_args.size_policy,
    )
    return 0


def _revert_cmd(cmd_args):
    """
    Delete snapshot set command handler.

    Attempt to revert the specified snapshot set.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    name = None
    uuid = None

    selection = Selection.from_cmd_args(cmd_args)
    if not selection.is_single():
        raise SnapmInvalidIdentifierError("Revert requires unique selection criteria")
    if selection.name:
        name = selection.name
    elif selection.uuid:
        uuid = selection.uuid
    else:
        raise SnapmInvalidIdentifierError("Revert requires a snapset name or UUID")

    snapset = revert_snapset(manager, name=name, uuid=uuid)
    _log_info("Started revert for snapset %s", snapset.name)
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
    return _generic_list_cmd(cmd_args, select, opts, manager, print_snapsets)


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
    show_snapsets(
        manager, selection=select, members=cmd_args.members, json=cmd_args.json
    )
    return 0


def _snapshot_activate_cmd(cmd_args):
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


def _snapshot_deactivate_cmd(cmd_args):
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


def _snapshot_autoactivate_cmd(cmd_args):
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
        snapshot.autoactivate = auto
        count += 1
    _log_info("Set autoactivation status for %d snapshots", count)
    return 0


def _snapshot_list_cmd(cmd_args):
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
    return _generic_list_cmd(cmd_args, select, opts, manager, print_snapshots)


def _snapshot_show_cmd(cmd_args):
    """
    Show snapshots command handler.

    Show the snapshots that match the given selection criteria as
    a multi-line report.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    select = Selection.from_cmd_args(cmd_args)
    show_snapshots(manager, selection=select, json=cmd_args.json)
    return 0


def _plugin_list_cmd(cmd_args):
    """
    List available plugins.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    opts = _report_opts_from_args(cmd_args)
    return _generic_list_cmd(cmd_args, None, opts, manager, print_plugins)


def _report_opts_from_args(cmd_args):
    opts = ReportOpts()

    if not cmd_args:
        return opts

    if cmd_args.json:
        opts.json = True

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
    # pylint: disable=global-statement
    global _CONSOLE_HANDLER
    level = _DEFAULT_LOG_LEVEL
    if cmd_args.verbose and cmd_args.verbose > 1:
        level = logging.DEBUG
    elif cmd_args.verbose and cmd_args.verbose > 0:
        level = logging.INFO
    snapm_log = logging.getLogger("snapm")
    snapm_log.setLevel(level)
    formatter = logging.Formatter("%(levelname)s - %(message)s")
    if _CONSOLE_HANDLER not in snapm_log.handlers:
        _CONSOLE_HANDLER = logging.StreamHandler()
        snapm_log.addHandler(_CONSOLE_HANDLER)
    _CONSOLE_HANDLER.setLevel(level)
    _CONSOLE_HANDLER.setFormatter(formatter)


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
            type=UUID,
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
            type=UUID,
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
    format_group = parser.add_mutually_exclusive_group()
    format_group.add_argument(
        "--json", action="store_true", help="Output report as JSON"
    )
    format_group.add_argument(
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


def _add_json_arg(parser):
    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        help="Display output in JSON notation",
    )


CREATE_CMD = "create"
DELETE_CMD = "delete"
RENAME_CMD = "rename"
RESIZE_CMD = "resize"
REVERT_CMD = "revert"
ACTIVATE_CMD = "activate"
DEACTIVATE_CMD = "deactivate"
AUTOACTIVATE_CMD = "autoactivate"
SHOW_CMD = "show"
LIST_CMD = "list"

SNAPSET_TYPE = "snapset"
SNAPSHOT_TYPE = "snapshot"
PLUGIN_TYPE = "plugin"


def _add_snapset_subparser(type_subparser):
    """
    Add subparser for 'snapset' commands.

    :param type_subparser: Command type subparser
    """
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
        "sources",
        metavar="SOURCE",
        type=str,
        nargs="+",
        help="A device or mount point path to include in this snapshot set",
    )
    snapset_create_parser.add_argument(
        "-s",
        "--size-policy",
        type=str,
        action="store",
        help="A default size policy for fixed size snapshots",
    )
    snapset_create_parser.add_argument(
        "-b",
        "--bootable",
        action="store_true",
        help="Create a boot entry for this snapshot set",
    )
    snapset_create_parser.add_argument(
        "-r",
        "--revert",
        action="store_true",
        help="Create a revert boot entry for this snapshot set",
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

    # snapset resize command
    snapset_resize_parser = snapset_subparser.add_parser(
        RESIZE_CMD, help="Resize snapshot sets"
    )
    snapset_resize_parser.set_defaults(func=_resize_cmd)
    _add_identifier_args(snapset_resize_parser, snapset=True)
    snapset_resize_parser.add_argument(
        "sources",
        metavar="SOURCE",
        type=str,
        nargs="*",
        help="A device or mount point path to include in this snapshot set",
    )
    snapset_resize_parser.add_argument(
        "-s",
        "--size-policy",
        type=str,
        action="store",
        help="A default size policy for fixed size snapshots",
    )

    # snapset revert command
    snapset_revert_parser = snapset_subparser.add_parser(
        REVERT_CMD, help="Revert snapshot sets"
    )
    snapset_revert_parser.set_defaults(func=_revert_cmd)
    _add_identifier_args(snapset_revert_parser, snapset=True)

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
    snapset_show_parser.add_argument(
        "-m",
        "--members",
        action="store_true",
        help="Show snapshots that are part of each snapshot set",
    )
    _add_json_arg(snapset_show_parser)


def _add_snapshot_subparser(type_subparser):
    """
    Add subparser for 'snapshot' commands.

    :param type_subparser: Command type subparser
    """
    # Subparser for snapshot commands
    snapshot_parser = type_subparser.add_parser(SNAPSHOT_TYPE, help="Snapshot commands")
    snapshot_subparser = snapshot_parser.add_subparsers(dest="command")

    # snapshot activate subcommand
    snapshot_activate_parser = snapshot_subparser.add_parser(
        ACTIVATE_CMD, help="Activate snapshots"
    )
    _add_identifier_args(snapshot_activate_parser, snapset=True, snapshot=True)
    snapshot_activate_parser.set_defaults(func=_snapshot_activate_cmd)

    # snapshot deactivate subcommand
    snapshot_deactivate_parser = snapshot_subparser.add_parser(
        DEACTIVATE_CMD, help="Deactivate snapshots"
    )
    _add_identifier_args(snapshot_deactivate_parser, snapset=True, snapshot=True)
    snapshot_deactivate_parser.set_defaults(func=_snapshot_deactivate_cmd)

    # snapshot autoactivate command
    snapshot_autoactivate_parser = snapshot_subparser.add_parser(
        AUTOACTIVATE_CMD, help="Set autoactivation status for snapshots"
    )
    snapshot_autoactivate_parser.set_defaults(func=_snapshot_autoactivate_cmd)
    _add_identifier_args(snapshot_autoactivate_parser, snapset=True, snapshot=True)
    _add_autoactivate_args(snapshot_autoactivate_parser)

    # snapshot list subcommand
    snapshot_list_parser = snapshot_subparser.add_parser(
        LIST_CMD, help="List snapshots"
    )
    snapshot_list_parser.set_defaults(func=_snapshot_list_cmd)
    _add_report_args(snapshot_list_parser)
    _add_identifier_args(snapshot_list_parser, snapset=True, snapshot=True)

    # snapshot show subcommand
    snapshot_show_parser = snapshot_subparser.add_parser(
        SHOW_CMD, help="Display snapshots"
    )
    snapshot_show_parser.set_defaults(func=_snapshot_show_cmd)
    _add_identifier_args(snapshot_show_parser, snapset=True, snapshot=True)
    _add_json_arg(snapshot_show_parser)


def _add_plugin_subparser(type_subparser):
    """
    Add subparser for 'plugin' commands.

    :param type_subparser: Command type subparser
    """
    # Subparser for plugin commands
    plugin_parser = type_subparser.add_parser(PLUGIN_TYPE, help="Plugin commands")
    plugin_subparser = plugin_parser.add_subparsers(dest="command")

    # plugin list subcommand
    plugin_list_parser = plugin_subparser.add_parser(LIST_CMD, help="List plugins")
    plugin_list_parser.set_defaults(func=_plugin_list_cmd)
    _add_report_args(plugin_list_parser)


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

    _add_snapset_subparser(type_subparser)

    _add_snapshot_subparser(type_subparser)

    _add_plugin_subparser(type_subparser)

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
        # pylint: disable=broad-except
        except KeyboardInterrupt:
            _log_error("Exiting on user cancel")
        except Exception as err:
            _log_error("Command failed: %s", err)

    shutdown_logging()
    return status


# vim: set et ts=4 sw=4 :
