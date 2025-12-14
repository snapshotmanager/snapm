# Copyright Red Hat
#
# snapm/command.py - Snapshot manager command interface
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""The ``snapm.command`` module provides both the snapm command line
interface infrastructure, and a simple procedural interface to the
``snapm`` library modules.

The procedural interface is used by the ``snapm`` command line tool,
and may be used by application programs, or interactively in the
Python shell by users who do not require all the features present
in the snapm object API.
"""
from argparse import ArgumentParser, REMAINDER
from typing import Union, List, Optional
from os.path import basename
from json import dumps
from uuid import UUID
import logging
import os

from snapm import (
    SnapmNotFoundError,
    SnapmInvalidIdentifierError,
    SnapmPathError,
    SnapmArgumentError,
    SNAPSET_NAME,
    SNAPSET_BASENAME,
    SNAPSET_INDEX,
    SNAPSET_SOURCES,
    SNAPSET_MOUNT_POINTS,
    SNAPSET_DEVICES,
    SNAPSET_NR_SNAPSHOTS,
    SNAPSET_TIME,
    SNAPSET_TIMESTAMP,
    SNAPSET_UUID,
    SNAPSET_STATUS,
    SNAPSET_AUTOACTIVATE,
    SNAPSET_MOUNTED,
    SNAPSET_ORIGIN_MOUNTED,
    SNAPSET_MOUNT_ROOT,
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
    SNAPM_DEBUG_SCHEDULE,
    SNAPM_DEBUG_MOUNTS,
    SNAPM_DEBUG_FSDIFF,
    SNAPM_DEBUG_ALL,
    SNAPM_SUBSYSTEM_COMMAND,
    SubsystemFilter,
    set_debug_mask,
    ProgressAwareHandler,
    bool_to_yes_no,
    Selection,
    __version__,
)
from snapm.manager import Manager, CalendarSpec, GcPolicy
from snapm.report import (
    REP_NUM,
    REP_SHA,
    REP_STR,
    REP_IDX,
    REP_TIME,
    REP_UUID,
    REP_SIZE,
    REP_STR_LIST,
    ReportOpts,
    ReportObjType,
    FieldType,
    Report,
)
from .fsdiff import DiffOptions, FsDiffer, FsDiffResults

DIFF_FORMATS = ["paths", "full", "short", "json", "diff", "tree"]

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


def _log_debug_command(msg, *args, **kwargs):
    """A wrapper for command subsystem debug logs."""
    _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_COMMAND}, **kwargs)


_DEFAULT_LOG_LEVEL = logging.WARNING
_CONSOLE_HANDLER = None

#
# Reporting object types
#


class ReportObj:
    """
    Common report object for snapm reports
    """

    def __init__(
        self, snapset=None, snapshot=None, plugin=None, schedule=None, diff=None
    ):
        self.snapset = snapset
        self.snapshot = snapshot
        self.plugin = plugin
        self.schedule = schedule
        self.diff = diff


#: Snapshot set report object type
PR_SNAPSET = 1
#: Snapshot report object type
PR_SNAPSHOT = 2
#: Plugin report object type
PR_PLUGIN = 4
#: Schedule report object type
PR_SCHEDULE = 8
#: DiffRecord report object type
PR_DIFF = 16

#: Report object types table for ``snapm.command`` reports
_report_obj_types = [
    ReportObjType(PR_SNAPSET, "Snapshot set", "snapset_", lambda o: o.snapset),
    ReportObjType(PR_SNAPSHOT, "Snapshot", "snapshot_", lambda o: o.snapshot),
    ReportObjType(PR_PLUGIN, "Plugin", "plugin_", lambda o: o.plugin),
    ReportObjType(PR_SCHEDULE, "Schedule", "schedule_", lambda o: o.schedule),
    ReportObjType(PR_DIFF, "Diff", "diff_", lambda o: o.diff),
]


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
        "basename",
        SNAPSET_BASENAME,
        "Snapshot set basename",
        12,
        REP_STR,
        lambda f, d: f.report_str(d.basename),
    ),
    FieldType(
        PR_SNAPSET,
        "index",
        SNAPSET_INDEX,
        "Snapshot set index",
        5,
        REP_IDX,
        lambda f, d: f.report_idx(d.index),
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
        lambda f, d: f.report_str(bool_to_yes_no(d.autoactivate)),
    ),
    FieldType(
        PR_SNAPSET,
        "mounted",
        SNAPSET_MOUNTED,
        "SnapshotSet mount status",
        7,
        REP_STR,
        lambda f, d: f.report_str(bool_to_yes_no(d.snapshot_mounted)),
    ),
    FieldType(
        PR_SNAPSET,
        "origin_mounted",
        SNAPSET_ORIGIN_MOUNTED,
        "SnapshotSet origin mount status",
        13,
        REP_STR,
        lambda f, d: f.report_str(bool_to_yes_no(d.origin_mounted)),
    ),
    FieldType(
        PR_SNAPSET,
        "mount_root",
        SNAPSET_MOUNT_ROOT,
        "SnapshotSet mount root directory path",
        9,
        REP_STR,
        lambda f, d: f.report_str(d.mount_root),
    ),
    FieldType(
        PR_SNAPSET,
        "bootable",
        SNAPSET_BOOTABLE,
        "Configured for snapshot boot",
        8,
        REP_STR,
        lambda f, d: f.report_str(bool_to_yes_no(d.boot_entry is not None)),
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

_DEFAULT_SNAPSET_FIELDS = "name,time,nr_snapshots,status,sources,mounted"

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
        SNAPSHOT_ORIGIN,
        "Snapshot origin",
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
        lambda f, d: f.report_str(bool_to_yes_no(d.autoactivate)),
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

_schedule_fields = [
    FieldType(
        PR_SCHEDULE,
        "name",
        "ScheduleName",
        "Name of the schedule",
        12,
        REP_STR,
        lambda f, d: f.report_str(d.name),
    ),
    FieldType(
        PR_SCHEDULE,
        "sources",
        "ScheduleSources",
        "Schedule sources",
        15,
        REP_STR_LIST,
        lambda f, d: f.report_str_list(d.sources),
    ),
    FieldType(
        PR_SCHEDULE,
        "sizepolicy",
        "SizePolicy",
        "Schedule default size policy",
        10,
        REP_STR,
        lambda f, d: f.report_str(d.default_size_policy or ""),
    ),
    FieldType(
        PR_SCHEDULE,
        "autoindex",
        "Autoindex",
        "Schedule autoindex",
        9,
        REP_STR,
        lambda f, d: f.report_str(bool_to_yes_no(d.autoindex)),
    ),
    FieldType(
        PR_SCHEDULE,
        "gcpolicytype",
        "GcPolicyType",
        "Schedule garbage collection policy type",
        12,
        REP_STR,
        lambda f, d: f.report_str(d.gc_policy.type.value),
    ),
    FieldType(
        PR_SCHEDULE,
        "gcpolicyparams",
        "GcPolicyParams",
        "Schedule garbage collection policy parameters",
        14,
        REP_STR,
        lambda f, d: f.report_str(str(d.gc_policy.params)),
    ),
    FieldType(
        PR_SCHEDULE,
        "oncalendar",
        "OnCalendar",
        "Schedule OnCalendar trigger expression",
        10,
        REP_STR,
        lambda f, d: f.report_str(d.calendarspec),
    ),
    FieldType(
        PR_SCHEDULE,
        "nextelapse",
        "NextElapse",
        "Time of next elapse",
        10,
        REP_TIME,
        lambda f, d: f.report_time(str(d.next_elapse)),
    ),
    FieldType(
        PR_SCHEDULE,
        "enabled",
        "Enabled",
        "Schedule enabled",
        7,
        REP_STR,
        lambda f, d: f.report_str(bool_to_yes_no(d.enabled)),
    ),
    FieldType(
        PR_SCHEDULE,
        "running",
        "Running",
        "Schedule running",
        7,
        REP_STR,
        lambda f, d: f.report_str(bool_to_yes_no(d.running)),
    ),
]

_DEFAULT_SCHEDULE_FIELDS = "name,sources,sizepolicy,oncalendar,enabled,nextelapse"

_diff_fields = [
    FieldType(
        PR_DIFF,
        "path",
        "Path",
        "Diff path",
        4,
        REP_STR,
        lambda f, d: f.report_str(d.path),
    ),
    FieldType(
        PR_DIFF,
        "type",
        "Type",
        "Diff type",
        4,
        REP_STR,
        lambda f, d: f.report_str(d.diff_type.value),
    ),
    FieldType(
        PR_DIFF,
        "size_delta",
        "SizeDelta",
        "Diff size change",
        9,
        REP_SIZE,
        lambda f, d: f.report_size(d.size_delta),
    ),
    FieldType(
        PR_DIFF,
        "content",
        "Content",
        "Content changed",
        7,
        REP_STR,
        lambda f, d: f.report_str(bool_to_yes_no(d.content_changed)),
    ),
    FieldType(
        PR_DIFF,
        "metadata",
        "Metadata",
        "Metadata changed",
        8,
        REP_STR,
        lambda f, d: f.report_str(bool_to_yes_no(d.metadata_changed)),
    ),
    FieldType(
        PR_DIFF,
        "mode_old",
        "OldMode",
        "Old path mode",
        7,
        REP_STR,
        lambda f, d: f.report_str(oct(d.mode_old)),
    ),
    FieldType(
        PR_DIFF,
        "mode_new",
        "NewMode",
        "New path mode",
        7,
        REP_STR,
        lambda f, d: f.report_str(oct(d.mode_new)),
    ),
    FieldType(
        PR_DIFF,
        "category",
        "Category",
        "File category",
        8,
        REP_STR,
        lambda f, d: f.report_str(d.file_category),
    ),
    FieldType(
        PR_DIFF,
        "movedfrom",
        "MovedFrom",
        "Move source",
        7,
        REP_STR,
        lambda f, d: f.report_str(d.moved_from if d.moved_from else ""),
    ),
    FieldType(
        PR_DIFF,
        "movedto",
        "MovedTo",
        "Move destination",
        7,
        REP_STR,
        lambda f, d: f.report_str(d.moved_to if d.moved_to else ""),
    ),
    FieldType(
        PR_DIFF,
        "diffsummary",
        "DiffSummary",
        "Summary of content differences",
        11,
        REP_STR,
        lambda f, d: f.report_str(d.content_diff_summary),
    ),
    FieldType(
        PR_DIFF,
        "hash_old",
        "OldHash",
        "Old file content hash",
        7,
        REP_STR,
        lambda f, d: f.report_str(
            d.old_entry.content_hash if d.old_entry.content_hash else ""
        ),
    ),
    FieldType(
        PR_DIFF,
        "hash_new",
        "NewHash",
        "New file content hash",
        7,
        REP_STR,
        lambda f, d: f.report_str(
            d.new_entry.content_hash if d.new_entry.content_hash else ""
        ),
    ),
    FieldType(
        PR_DIFF,
        "filetype",
        "FileType",
        "File type",
        8,
        REP_STR,
        lambda f, d: f.report_str(d.file_type),
    ),
    FieldType(
        PR_DIFF,
        "mtime_old",
        "OldMtime",
        "Old modification time",
        8,
        REP_TIME,
        lambda f, d: f.report_time(d.mtime_old),
    ),
    FieldType(
        PR_DIFF,
        "mtime_new",
        "NewMtime",
        "New modification time",
        8,
        REP_TIME,
        lambda f, d: f.report_time(d.mtime_new),
    ),
]

_DEFAULT_DIFF_FIELDS = "path,type,size_delta,content,metadata,filetype"


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


def create_snapset(
    manager, name, sources, size_policy=None, boot=False, revert=False, autoindex=False
):
    """
    Create a new snapshot set from a list of mount point and block device
    source paths.

    :param manager: The manager context to use
    :param name: The name of the new snapshot set
    :param sources: A list of mount point or block devices to snapshot
    :param size_policy: The default size policy for this snapshot set.
    :param boot: Create a boot entry for this snapshot set.
    :param revert: Create a revert boot entry for this snapshot set.
    :param autoindex: Treat `name` as the basename of a recurring snapshot set
                      and generate and append an appropriate index value.
    """
    return manager.create_snapshot_set(
        name,
        sources,
        default_size_policy=size_policy,
        boot=boot,
        revert=revert,
        autoindex=autoindex,
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


def split_snapset(manager, name, new_name, sources):
    """
    Split an existing snapshot set.

    Split the snapshot set named ``name`` into two snapshot sets, including
    all sources listed in ``sources`` in the newly created snapshot set with
    name ``new_name``.

    :param name: The name of the snapshot set to split.
    :param new_name: The name for the newly created snapshot set.
    :param sources: The sources to split from ``name`` to ``new_name``.
    :returns: A ``SnapshotSet`` object representing the snapshot set named
              ``new_name``.
    """
    return manager.split_snapshot_set(name, new_name, sources)


def prune_snapset(manager, name, sources):
    """
    Prune snapshots from an existing snapshot set.

    Remove snapshots from an existing snapshot set named ``name``. The
    snapshot sources listed in ``sources`` are pruned (deleted) from the
    named snapshot set.

    :param name: The name of the snapshot set to prune.
    :param sources: The sources to prune from ``name``.
    :returns: A ``SnapshotSet`` object representing the pruned snapshot set
    """
    return manager.split_snapshot_set(name, None, sources)


# pylint: disable=too-many-branches
def diff_snapsets(
    manager: Manager, diff_from: str, diff_to: str, options: DiffOptions
) -> FsDiffResults:
    """
    Find differences between snapshot sets or the running system.

    Use the difference engine to compare two snapshot sets or a snapshot set
    and the running system.

    The reserved name '.' is used to reference the running system in either
    ``diff_from`` or ``diff_to``.

    :param manager: The ``Manager`` instance to use.
    :type manager: ``Manager``
    :param diff_from: The name of the snapshot set (or '.' for the running
                      system) to use as the left side of the comparison.
    :type diff_from: ``str``
    :param diff_to: The name of the snapshot set (or '.' for the running
                      system) to use as the right side of the comparison.
    :type diff_to: ``str``
    :param options: Options controlling the comparison.
    :type options: ``DiffOptions``
    :returns: A list of file system diff records.
    :rtype: ``FsDiffResults``
    """
    if diff_from == diff_to:
        target = f"'{diff_from}'" if diff_from != "." else "system root"
        raise SnapmArgumentError(
            f"Cannot compare {target} to itself.",
        )

    did_from_mount = False
    did_to_mount = False
    from_snapset = None
    to_snapset = None

    if diff_from == ".":
        from_root = manager.mounts.get_sys_mount()
    else:
        mounts = manager.mounts.find_mounts(Selection(name=diff_from))
        if not mounts or not mounts[0].mounted:
            snapsets = manager.find_snapshot_sets(Selection(name=diff_from))
            if not snapsets:
                raise SnapmNotFoundError(
                    f"Cannot find snapshot set named '{diff_from}'"
                )
            from_snapset = snapsets[0]
            from_root = manager.mounts.mount(from_snapset)
            did_from_mount = True
        else:
            from_root = mounts[0]

    if diff_to == ".":
        to_root = manager.mounts.get_sys_mount()
    else:
        mounts = manager.mounts.find_mounts(Selection(name=diff_to))
        if not mounts or not mounts[0].mounted:
            snapsets = manager.find_snapshot_sets(Selection(name=diff_to))
            if not snapsets:
                raise SnapmNotFoundError(f"Cannot find snapshot set named '{diff_to}'")
            to_snapset = snapsets[0]
            to_root = manager.mounts.mount(to_snapset)
            did_to_mount = True
        else:
            to_root = mounts[0]

    fsd = FsDiffer(manager, options=options)

    try:
        return fsd.compare_roots(from_root, to_root)
    finally:
        if did_from_mount:
            manager.mounts.umount(from_snapset)
        if did_to_mount:
            manager.mounts.umount(to_snapset)


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
            snap_list.append(snapshot.to_dict())
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
            set_list.append(snapset.to_dict(members=members))
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


def show_schedules(manager, selection=None, _members=False, json=False):
    """
    Show schedules matching selection criteria.
    """
    schedules = manager.scheduler.find_schedules(selection)
    first = True

    if json:
        sched_list = []

    for schedule in schedules:
        if json:
            sched_list.append(schedule.to_dict())
            continue

        wspace = "" if first else "\n"
        print(f"{wspace}{schedule}")
        first = False
        # if members
        # ...

    if json:
        print(dumps(sched_list, indent=4))


def _expand_fields(default_fields, output_fields):
    """
    Expand output fields list from command line arguments.
    """

    if not output_fields:
        output_fields = default_fields
    elif output_fields.startswith("+"):
        output_fields = default_fields + "," + output_fields[1:]
    return output_fields


def create_schedule(
    manager: Manager,
    name: str,
    sources: List[str],
    default_size_policy: str,
    autoindex: bool,
    calendarspec: Union[str, CalendarSpec],
    policy: GcPolicy,
    boot=False,
    revert=False,
):
    """
    Create a new schedule from a list of mount point and block device
    source paths.

    :param manager: The manager context to use.
    :param name: The name of the new schedule.
    :param sources: A list of mount point or block devices to snapshot.
    :param default_size_policy: The default size policy for this snapshot set.
    :param autoindex: Enable autoindex names for this schedule.
    :param boot: Create a boot entry for this snapshot set.
    :param revert: Create a revert boot entry for this snapshot set.
    :param autoindex: Treat `name` as the basename of a recurring snapshot set
                      and generate and append an appropriate index value.
    """
    return manager.scheduler.create(
        name,
        sources,
        default_size_policy,
        autoindex,
        calendarspec,
        policy,
        boot=boot,
        revert=revert,
    )


def delete_schedule(manager: Manager, name: str):
    """
    Delete schedule by name. This disables the schedule and removes all on-disk
    configuration data.

    :param manager: The manager context to use.
    :param name: The name of the schedule to delete.
    """
    manager.scheduler.delete(name)


def edit_schedule(
    manager: Manager,
    name: str,
    sources: Optional[List[str]],
    default_size_policy: Optional[str],
    autoindex: bool,
    calendarspec: Optional[Union[str, CalendarSpec]],
    policy: Optional[GcPolicy],
    boot: Optional[bool] = None,
    revert: Optional[bool] = None,
):
    """
    Edit an existing schedule from a list of mount point and block device
    source paths.

    :param manager: The manager context to use.
    :param name: The name of the new schedule.
    :param sources: A list of mount point or block devices to snapshot.
    :param default_size_policy: The default size policy for this snapshot set.
    :param autoindex: Enable autoindex names for this schedule.
    :param boot: Create a boot entry for this snapshot set.
    :param revert: Create a revert boot entry for this snapshot set.
    :param autoindex: Treat `name` as the basename of a recurring snapshot set
                      and generate and append an appropriate index value.
    """
    schedules = manager.scheduler.find_schedules(Selection(sched_name=name))
    if len(schedules) != 1:
        raise SnapmNotFoundError(f"Schedule named '{name}' does not exist")
    old_schedule = schedules[0]

    if not sources:
        sources = old_schedule.sources
    if not default_size_policy:
        default_size_policy = old_schedule.default_size_policy
    if not calendarspec:
        calendarspec = old_schedule.calendarspec
    if not policy or not policy.has_params:
        policy = old_schedule.gc_policy
    if boot is None:
        boot = old_schedule.boot
    if revert is None:
        revert = old_schedule.revert

    return manager.scheduler.edit(
        name,
        sources,
        default_size_policy,
        autoindex,
        calendarspec,
        policy,
        boot=boot,
        revert=revert,
    )


def enable_schedule(manager: Manager, name: str, start: bool):
    """
    Enable an existing schedule. This enables the systemd timer units
    associated with this schedule.

    :param manager: The manager context to use.
    :param name: The name of the schedule to enable.
    """
    return manager.scheduler.enable(name, start)


def disable_schedule(manager: Manager, name: str):
    """
    Enable an existing schedule. This disables the systemd timer units
    associated with this schedule.

    :param manager: The manager context to use.
    :param name: The name of the schedule to disable.
    """
    return manager.scheduler.disable(name)


def gc_schedule(manager: Manager, name: str) -> List[str]:
    """
    Run garbage collection for an existing schedule. This executes the
    configured garbage collection policy for the schedule named ``name``.

    :param manager: The manager context to use.
    :param name: The name of the schedule to run garbage collection for.
    """
    return manager.scheduler.gc(name)


def print_schedules(
    manager,
    selection: Union[None, Selection] = None,
    output_fields: Union[None, str] = None,
    opts: Union[None, ReportOpts] = None,
    sort_keys: [None, str] = None,
    data: object = None,  # pylint: disable=unused-argument
):
    """
    Print schedules matching selection criteria.

    Format a set of ``snapm.manager.Schedule`` objects matching the given
    criteria, and output them as a report to the file given in
    ``opts.report_file``.

    :param selection: A ``Selection`` object giving selection criteria for
                      the operation.
    :type selection: ``Selection``
    :param output_fields: A comma-separated list of output fields.
    :type output_fields: ``str``
    :param opts: Output formatting and control options.
    :type opts: ``ReportOpts``
    :param sort_keys: A comma-separated list of sort keys.
    :type sort_keys: ``str``
    :param data: Optional data for report (unused).
    :type data: ``NoneType``
    """
    output_fields = _expand_fields(_DEFAULT_SCHEDULE_FIELDS, output_fields)

    schedules = manager.scheduler.find_schedules(selection=selection)
    selected = [ReportObj(schedule=sched) for sched in schedules]

    return _do_print_type(
        _schedule_fields,
        selected,
        output_fields=output_fields,
        opts=opts,
        sort_keys=sort_keys,
        title="Schedules",
    )


def print_plugins(
    manager,
    selection=None,
    output_fields=None,
    opts=None,
    sort_keys=None,
    data: object = None,  # pylint: disable=unused-argument
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
    :param data: Optional data for report (unused).
    :type data: ``NoneType``
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
    manager,
    selection=None,
    output_fields=None,
    opts=None,
    sort_keys=None,
    data: object = None,  # pylint: disable=unused-argument
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
    :param data: Optional data for report (unused).
    :type data: ``NoneType``
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
    manager,
    selection=None,
    output_fields=None,
    opts=None,
    sort_keys=None,
    data: object = None,  # pylint: disable=unused-argument
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
    :param data: Optional data for report (unused).
    :type data: ``NoneType``
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


def print_diffs(
    _manager,
    selection=None,  # pylint: disable=unused-argument
    output_fields=None,
    opts=None,
    sort_keys=None,
    data: Optional[FsDiffResults] = None,
):
    """
    Print diffs matching selection criteria.

    Format a set of ``snapm.fsdiff.FsDiffRecord`` objects matching
    the given criteria, and output them as a report to the file
    given in ``opts.report_file``.

    Selection criteria may be expressed via a Selection object
    passed to the call using the ``selection`` parameter.

    :param _manager: Manager context (unused).
    :type _manager: ``Manager``
    :param _selection: A Selection object giving selection
                       criteria for the operation (unused).
    :type _selection: ``Selection``
    :param output_fields: a comma-separated list of output fields
    :param opts: output formatting and control options
    :param sort_keys: a comma-separated list of sort keys
    :param data: Optional data for report (unused).
    :type data: ``NoneType``
    """
    output_fields = _expand_fields(_DEFAULT_DIFF_FIELDS, output_fields)

    if data and not isinstance(data, FsDiffResults):
        raise ValueError("print_diffs(): data value must be FsDiffResults")

    selected = [ReportObj(diff=diff) for diff in data] if data else []

    return _do_print_type(
        _diff_fields,
        selected,
        output_fields=output_fields,
        opts=opts,
        sort_keys=sort_keys,
        title="FsDiffs",
    )


def _generic_list_cmd(cmd_args, select, opts, manager, print_fn, data: object = None):
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
    :param data: Optional data for report.
    :type data: ``object``
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
            data=data,
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
    create snapshot set command handler.
    attempt to create the specified snapshot set.

    :param cmd_args: command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()

    if hasattr(cmd_args, "config") and cmd_args.config:
        schedules = manager.scheduler.find_schedules(
            Selection(sched_name=cmd_args.config)
        )
        if not schedules:
            _log_error(
                "No schedule configuration matching '%s' found.", cmd_args.config
            )
            return 1
        if len(schedules) > 1:
            _log_error(
                "Ambiguous schedule configuration '%s': %s",
                cmd_args.config,
                ", ".join(sched.name for sched in schedules),
            )
            return 1
        schedule = schedules[0]

        snapset_name = schedule.name
        sources = schedule.sources
        size_policy = schedule.default_size_policy
        autoindex = schedule.autoindex
        boot = schedule.boot
        revert = schedule.revert
    else:
        snapset_name = cmd_args.snapset_name
        sources = cmd_args.sources
        size_policy = cmd_args.size_policy
        boot = cmd_args.bootable
        revert = cmd_args.revert
        autoindex = cmd_args.autoindex

    snapset = create_snapset(
        manager,
        snapset_name,
        sources,
        size_policy=size_policy,
        boot=boot,
        revert=revert,
        autoindex=autoindex,
    )
    if snapset is None:
        return 1
    _log_info(
        "created snapset %s with %d snapshots", snapset.name, snapset.nr_snapshots
    )
    if cmd_args.json:
        print(snapset.json(pretty=True))
    else:
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
    Revert snapshot set command handler.

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
    else:  # pragma: no cover
        raise SnapmInvalidIdentifierError("Revert requires a snapset name or UUID")

    snapset = revert_snapset(manager, name=name, uuid=uuid)
    _log_info("Started revert for snapset %s", snapset.name)
    return 0


def _split_cmd(cmd_args):
    """
    Split snapshot set command handler.

    Attempt to split the specified sources from the first snapshot set
    argument into the second.

    :param cmd_args: Command line arguments for the command.
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()

    snapset = split_snapset(manager, cmd_args.name, cmd_args.new_name, cmd_args.sources)
    _log_info(
        "Split snapset '%s' into '%s' (%s)",
        cmd_args.name,
        snapset.name,
        ", ".join(snapset.sources),
    )
    print(snapset)
    return 0


def _prune_cmd(cmd_args):
    """
    Prune snapshot set command handler.

    Attempt to prune the specified sources from the given snapshot set.

    :param cmd_args: Command line arguments for the command.
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()

    snapset = prune_snapset(manager, cmd_args.name, cmd_args.sources)
    _log_info(
        "Pruned sources (%s) from snapset '%s'",
        ", ".join(cmd_args.sources),
        snapset.name,
    )
    print(snapset)
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
    auto = cmd_args.autoactivate
    count = manager.set_autoactivate(select, auto=auto)
    _log_info("Set autoactivate=%s for %d snapshot sets", bool_to_yes_no(auto), count)
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


def _mount_cmd(cmd_args):
    """
    Mount snapshot set command handler.

    Mount the specified snapshot set (by default snapshot set mounts appear
    under /run/snapm/mounts/<name>).

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    select = Selection(name=cmd_args.name)
    matches = manager.find_snapshot_sets(selection=select)
    if len(matches) != 1:
        _log_error("Cannot find snapshot set matching name=%s", cmd_args.name)
        return 1
    snapset = matches[0]
    manager.mounts.mount(snapset)
    return 0


def _umount_cmd(cmd_args):
    """
    Unmount snapshot set command handler.

    Unmount the specified snapshot set (by default from
    /run/snapm/mounts/<name>).

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    select = Selection(name=cmd_args.name)
    matches = manager.find_snapshot_sets(selection=select)
    if len(matches) != 1:
        _log_error("Cannot find snapshot set matching name=%s", cmd_args.name)
        return 1
    snapset = matches[0]
    manager.mounts.umount(snapset)
    return 0


def _exec_cmd(cmd_args):
    """
    Execute in snapshot set command handler.

    Execute the specified command in the given snapshot set.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    did_mount = False
    ret = 0

    if not cmd_args.command:
        _log_error("the following arguments are required: COMMAND")
        return 1

    manager = Manager()
    select = Selection(name=cmd_args.name)
    matches = manager.find_snapshot_sets(selection=select)
    if len(matches) != 1:
        _log_error("Cannot find snapshot set matching name=%s", cmd_args.name)
        return 1
    snapset = matches[0]

    pre_existing = manager.mounts.find_mounts(selection=select)
    mount = manager.mounts.mount(snapset)  # idempotent
    did_mount = not any(m.mounted for m in pre_existing)

    try:
        ret = mount.exec(cmd_args.command)
    except (SnapmPathError, SnapmArgumentError) as err:
        _log_error("mount.exec(%s) failed: %s", cmd_args.command, err)
        ret = 1
    finally:
        if did_mount:
            manager.mounts.umount(snapset)

    if ret:
        _log_error(
            "Command '%s' failed in snapshot set %s: %d",
            " ".join(cmd_args.command),
            cmd_args.name,
            ret,
        )

    return ret


def _shell_cmd(cmd_args):
    """
    Shell snapshot set command handler.

    Start an interactive shell in the given snapshot set. The shell invoked
    defaults to the SHELL environment variable if set, or the program at
    /bin/bash otherwise.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    cmd_args.command = [os.environ.get("SHELL") or "/bin/bash"]
    return _exec_cmd(cmd_args)


# pylint: disable=too-many-locals
def _diff_cmd(cmd_args):
    """
    Diff snapshot set command handler.

    Compare between snapshot sets or the running system.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    options = DiffOptions.from_cmd_args(cmd_args)
    diff_from = cmd_args.diff_from
    diff_to = cmd_args.diff_to
    output_format = cmd_args.output_format
    pretty = cmd_args.pretty
    color = cmd_args.color

    if not cmd_args.diff_from or not cmd_args.diff_to:
        _log_error(
            "snapm snapset diff: error: the following "
            "arguments are required: FROM, TO"
        )
        return 1

    if diff_from == diff_to:
        _log_error(
            "Cannot compare %s to itself.",
            f"'{diff_from}'" if diff_from != "." else "system root",
        )
        return 1

    if output_format not in DIFF_FORMATS:
        # Belts and braces: should be unreachable since ArgumentParser validates
        # that `output_format` is a member of DIFF_FORMATS.
        _log_error("Unknown diff format: %s", cmd_args.output_format)
        return 1

    # Initialise Manager context for mounts and snapshot set discover.
    manager = Manager()

    results = diff_snapsets(manager, diff_from, diff_to, options)
    if not options.quiet:
        print(f"Found {results.total_changes} differences")

    if output_format == "paths":
        print("\n".join(results.paths()))
    elif output_format == "full":
        print(results.full())
    elif output_format == "short":
        print(results.short())
    elif output_format == "json":
        print(results.json(pretty=pretty))
    elif output_format == "diff":
        if not options.quiet:
            print(f"Found {results.content_changes} content differences")
        print(results.diff(color=color))
    elif output_format == "tree":
        print(results.tree())

    return 0


def _diffreport_cmd(cmd_args):
    """
    Diff Report snapshot set command handler.

    Compare between snapshot sets or the running system.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    options = DiffOptions.from_cmd_args(cmd_args)
    diff_from = cmd_args.diff_from
    diff_to = cmd_args.diff_to

    if not cmd_args.options or "help" not in cmd_args.options:
        if not cmd_args.diff_from or not cmd_args.diff_to:
            _log_error(
                "snapm snapset diff: error: the following "
                "arguments are required: FROM, TO (unless -ohelp)"
            )
            return 1

        if diff_from == diff_to:
            _log_error(
                "Cannot compare %s to itself.",
                f"'{diff_from}'" if diff_from != "." else "system root",
            )
            return 1

    opts = _report_opts_from_args(cmd_args)
    select = Selection.from_cmd_args(cmd_args)

    manager = Manager()

    if cmd_args.options and "help" in cmd_args.options:
        results = None
    else:
        results = diff_snapsets(manager, diff_from, diff_to, options)
        if not options.quiet:
            print(f"Found {results.total_changes} differences")

    return _generic_list_cmd(cmd_args, select, opts, manager, print_diffs, data=results)


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
    auto = cmd_args.autoactivate
    for snapshot in snapshots:
        snapshot.autoactivate = auto
        count += 1
    _log_info("Set autoactivate=%s for %d snapshots", bool_to_yes_no(auto), count)
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


def _schedule_create_cmd(cmd_args):
    """
    Create schedule.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    # Note: validate policy arguments!
    policy = GcPolicy.from_cmd_args(cmd_args)
    schedule = create_schedule(
        manager,
        cmd_args.schedule_name,
        cmd_args.sources,
        cmd_args.size_policy,
        True,
        cmd_args.calendarspec,
        policy,
        boot=cmd_args.bootable,
        revert=cmd_args.revert,
    )
    if not schedule:
        return 1
    _log_info(
        "Created schedule %s",
        schedule.name,
    )
    if cmd_args.json:
        print(schedule.json(pretty=True))
    else:
        print(schedule)
    return 0


def _schedule_delete_cmd(cmd_args):
    """
    Delete schedule.

    :param cmd_args: Command line arguments for the command.
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    manager.scheduler.delete(cmd_args.schedule_name)
    return 0


def _schedule_edit_cmd(cmd_args):
    """
    Edit schedule.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    policy = (
        GcPolicy.from_cmd_args(cmd_args)
        if getattr(cmd_args, "policy_type", None)
        else None
    )
    schedule = edit_schedule(
        manager,
        cmd_args.schedule_name,
        cmd_args.sources,
        cmd_args.size_policy,
        True,
        cmd_args.calendarspec,
        policy,
        boot=cmd_args.bootable,
        revert=cmd_args.revert,
    )
    if not schedule:
        return 1
    _log_info(
        "Edited schedule %s",
        schedule.name,
    )
    if cmd_args.json:
        print(schedule.json(pretty=True))
    else:
        print(schedule)
    return 0


def _schedule_enable_cmd(cmd_args):
    """
    Enable schedule.

    :param cmd_args: Command line arguments for the command.
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    schedule = enable_schedule(manager, cmd_args.schedule_name, cmd_args.start)
    print(schedule)
    return 0


def _schedule_disable_cmd(cmd_args):
    """
    Disable schedule.

    :param cmd_args: Command line arguments for the command.
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    schedule = disable_schedule(manager, cmd_args.schedule_name)
    print(schedule)
    return 0


def _schedule_gc_cmd(cmd_args):
    """
    Garbage collect schedule.

    :param cmd_args: Command line arguments for the command.
    :returns: integer status code returned from ``main()``.
    """
    manager = Manager()
    garbage = gc_schedule(manager, cmd_args.config)
    if garbage:
        names = ", ".join(garbage)
        print(f"Garbage collected snapshot sets: {names}")
    return 0


def _schedule_list_cmd(cmd_args):
    """
    List configured schedules.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    opts = _report_opts_from_args(cmd_args)
    return _generic_list_cmd(cmd_args, None, opts, manager, print_schedules)


def _schedule_show_cmd(cmd_args):
    """
    List configured schedules.

    :param cmd_args: Command line arguments for the command
    :returns: integer status code returned from ``main()``
    """
    manager = Manager()
    selection = Selection.from_cmd_args(cmd_args)
    show_schedules(manager, selection, json=cmd_args.json)
    return 0


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
    formatter = logging.Formatter("%(levelname)s - %(message)s")
    snapm_log.setLevel(level)
    if snapm_log.hasHandlers():
        snapm_log.handlers.clear()

    # Subsystem log filtering
    _snapm_subsystem_filter = SubsystemFilter("snapm")

    # Main console handler
    _CONSOLE_HANDLER = ProgressAwareHandler()

    _CONSOLE_HANDLER.setLevel(level)
    _CONSOLE_HANDLER.setFormatter(formatter)
    _CONSOLE_HANDLER.addFilter(_snapm_subsystem_filter)

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
        "report": SNAPM_DEBUG_REPORT,
        "schedule": SNAPM_DEBUG_SCHEDULE,
        "mounts": SNAPM_DEBUG_MOUNTS,
        "fsdiff": SNAPM_DEBUG_FSDIFF,
        "all": SNAPM_DEBUG_ALL,
    }

    mask = 0
    for name in debug_arg.split(","):
        if name not in mask_map:
            raise ValueError(f"Unknown debug option: {name}")
        mask |= mask_map[name]
    set_debug_mask(mask)


def _add_create_args(parser):
    """
    Add create command arguments.
    """
    parser.add_argument(
        "sources",
        metavar="SOURCE",
        type=str,
        nargs="+",
        help="A device or mount point path to include in this snapshot set",
    )
    parser.add_argument(
        "-s",
        "--size-policy",
        type=str,
        action="store",
        help="A default size policy for fixed size snapshots",
    )
    parser.add_argument(
        "-b",
        "--bootable",
        action="store_true",
        help="Create a boot entry for this snapshot set",
    )
    parser.add_argument(
        "-r",
        "--revert",
        action="store_true",
        help="Create a revert boot entry for this snapshot set",
    )


def _add_identifier_args(parser, snapset=False, snapshot=False):
    """
    Add snapshot set or snapshot identifier command line arguments.
    """
    group = None
    if snapset:
        name_uuid_group = parser.add_mutually_exclusive_group()
        name_uuid_group.add_argument(
            "-n",
            "--name",
            metavar="NAME",
            type=str,
            help="A snapset name",
        )
        name_uuid_group.add_argument(
            "-u",
            "--uuid",
            metavar="UUID",
            type=UUID,
            help="A snapset UUID",
        )
        group = name_uuid_group
    if snapshot:
        snapshot_name_uuid_group = group or parser.add_mutually_exclusive_group()
        snapshot_name_uuid_group.add_argument(
            "-N",
            "--snapshot-name",
            metavar="SNAPSHOT_NAME",
            type=str,
            help="A snapshot name",
        )
        snapshot_name_uuid_group.add_argument(
            "-U",
            "--snapshot-uuid",
            metavar="SNAPSHOT_UUID",
            type=UUID,
            help="A snapshot UUID",
        )
        group = snapshot_name_uuid_group
    group = group or parser.add_mutually_exclusive_group()
    group.add_argument(
        "identifier",
        metavar="ID",
        type=str,
        action="store",
        help=(
            "An optional snapset identifier (name or UUID) "
            "if neither -n/--name nor -u/--uuid is given"
        ),
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
        "--rows", action="store_true", help="Output report columns as rows"
    )
    parser.add_argument(
        "--separator", metavar="SEP", type=str, help="Report field separator"
    )


def _add_autoactivate_args(parser):
    yes_no_mutex_group = parser.add_mutually_exclusive_group(required=True)
    yes_no_mutex_group.add_argument(
        "--yes",
        dest="autoactivate",
        action="store_true",
        help="Enable autoactivation",
    )
    yes_no_mutex_group.add_argument(
        "--no",
        action="store_false",
        dest="autoactivate",
        help="Disable autoactivation",
    )


def _add_json_arg(parser):
    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        help="Display output in JSON notation",
    )


def _add_policy_args(parser, required=True):
    parser.add_argument(
        "-p",
        "--policy-type",
        type=lambda s: s.lower(),
        required=required,
        choices=["all", "count", "age", "timeline"],
        help="Garbage collection policy type",
    )

    # ArgumentParser does not properly support nesting argument groupswithin a
    # MutuallyExclusiveGroup: https://github.com/python/cpython/issues/66246
    # Add the GcPolicyType arguments as groups within the schedule create
    # subparser and validate the arguments after parsing.

    gc_params_count_group = parser.add_argument_group(title="Count")
    gc_params_count_group.add_argument(
        "--keep-count",
        type=int,
        default=0,
        metavar="COUNT",
        help="Keep COUNT snapshot sets",
    )

    gc_params_age_group = parser.add_argument_group(title="Age")
    gc_params_age_group.add_argument(
        "--keep-years",
        type=int,
        default=0,
        metavar="YEARS",
        help="Keep YEARS snapshot sets",
    )
    gc_params_age_group.add_argument(
        "--keep-months",
        type=int,
        default=0,
        metavar="MONTHS",
        help="Keep MONTHS snapshot sets",
    )
    gc_params_age_group.add_argument(
        "--keep-weeks",
        type=int,
        default=0,
        metavar="WEEKS",
        help="Keep WEEKS snapshot sets",
    )
    gc_params_age_group.add_argument(
        "--keep-days",
        type=int,
        default=0,
        metavar="DAYS",
        help="Keep DAYS snapshot sets",
    )

    gc_params_timeline_group = parser.add_argument_group(title="Timeline")
    gc_params_timeline_group.add_argument(
        "--keep-yearly",
        type=int,
        default=0,
        metavar="COUNT",
        help="Keep COUNT yearly snapshot sets",
    )
    gc_params_timeline_group.add_argument(
        "--keep-quarterly",
        type=int,
        default=0,
        metavar="COUNT",
        help="Keep COUNT quarterly snapshot sets",
    )
    gc_params_timeline_group.add_argument(
        "--keep-monthly",
        type=int,
        default=0,
        metavar="COUNT",
        help="Keep COUNT monthly snapshot sets",
    )
    gc_params_timeline_group.add_argument(
        "--keep-weekly",
        type=int,
        default=0,
        metavar="COUNT",
        help="Keep COUNT weekly snapshot sets",
    )
    gc_params_timeline_group.add_argument(
        "--keep-daily",
        type=int,
        default=0,
        metavar="COUNT",
        help="Keep COUNT daily snapshot sets",
    )
    gc_params_timeline_group.add_argument(
        "--keep-hourly",
        type=int,
        default=0,
        metavar="COUNT",
        help="Keep COUNT hourly snapshot sets",
    )


def _add_schedule_config_arg(parser):
    parser.add_argument(
        "-c",
        "--config",
        metavar="SCHEDULE_NAME",
        type=str,
        action="store",
        required=True,
        help="The name of a configured schedule to create snapshot sets for",
    )


def _add_diff_args(parser):
    parser.add_argument(
        "-t",
        "--ignore-timestamps",
        action="store_true",
        help="Ignore timestamps when computing diffs",
    )
    parser.add_argument(
        "-p",
        "--ignore-permissions",
        action="store_true",
        help="Ignore permissions when computing diffs",
    )
    parser.add_argument(
        "-w",
        "--ignore-ownership",
        action="store_true",
        help="Ignore ownership when computing diffs",
    )
    parser.add_argument(
        "-c",
        "--content-only",
        action="store_true",
        help="Only consider content changes",
    )
    parser.add_argument(
        "--include-system-dirs",
        action="store_true",
        help="Include system directories in diff comparisons",
    )
    parser.add_argument(
        "-C",
        "--no-content-diff",
        dest="include_content_diffs",
        action="store_false",
        help="Do not generate content diffs for detected file modifications",
    )
    parser.add_argument(
        "-f",
        "--file-types",
        dest="include_file_type",
        action="store_true",
        help="Generate file type information using libmagic",
    )
    parser.add_argument(
        "-F",
        "--follow-symlinks",
        action="store_true",
        help="Follow symlinks when walking file system trees",
    )
    parser.add_argument(
        "-m",
        "--max-file-size",
        type=int,
        default=0,
        help="Maximum file size for comparisons (0=unlimited)",
    )
    parser.add_argument(
        "-d",
        "--max-diff-size",
        type=int,
        dest="max_content_diff_size",
        default=2**20,
        help="Maximum file size for generating content diffs (default: 1MiB)",
    )
    parser.add_argument(
        "-H",
        "--max-hash-size",
        type=int,
        dest="max_content_hash_size",
        default=2**20,
        help="Maximum file size for generating content hashes (default: 1MiB)",
    )
    parser.add_argument(
        "-i",
        "--include-pattern",
        type=str,
        action="append",
        metavar="PATTERN",
        dest="file_patterns",
        default=[],
        help="File patterns to include (glob notation)",
    )
    parser.add_argument(
        "-x",
        "--exclude-pattern",
        type=str,
        action="append",
        metavar="PATTERN",
        dest="exclude_patterns",
        default=[],
        help="File patterns to exclude (glob notation)",
    )
    parser.add_argument(
        "-s",
        "--start-path",
        type=str,
        metavar="PATH",
        dest="from_path",
        help="Start traversal from PATH",
    )
    parser.add_argument(
        "-P",
        "--pretty",
        action="store_true",
        help="Pretty print output if supported by output format",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Do not output progress or status information",
    )
    colors = ["auto", "never", "always"]
    parser.add_argument(
        "--color",
        type=str,
        choices=colors,
        default=colors[0],
        help=f"Enable colored diff output ({', '.join(colors)})",
    )
    parser.add_argument(
        "diff_from",
        type=str,
        metavar="FROM",
        nargs="?",
        help="Compare from snapshot set name or '.' for running system",
    )
    parser.add_argument(
        "diff_to",
        type=str,
        metavar="TO",
        nargs="?",
        help="Compare to snapshot set name or '.' for running system",
    )


CREATE_CMD = "create"
CREATE_SCHEDULED_CMD = "create-scheduled"
DELETE_CMD = "delete"
EDIT_CMD = "edit"
RENAME_CMD = "rename"
RESIZE_CMD = "resize"
REVERT_CMD = "revert"
SPLIT_CMD = "split"
PRUNE_CMD = "prune"
ACTIVATE_CMD = "activate"
DEACTIVATE_CMD = "deactivate"
AUTOACTIVATE_CMD = "autoactivate"
DIFF_CMD = "diff"
DIFFREPORT_CMD = "diffreport"
ENABLE_CMD = "enable"
DISABLE_CMD = "disable"
SHOW_CMD = "show"
LIST_CMD = "list"
GC_CMD = "gc"
MOUNT_CMD = "mount"
UMOUNT_CMD = "umount"
EXEC_CMD = "exec"
SHELL_CMD = "shell"

SNAPSET_TYPE = "snapset"
SNAPSHOT_TYPE = "snapshot"
PLUGIN_TYPE = "plugin"
SCHEDULE_TYPE = "schedule"


# pylint: disable=too-many-statements,too-many-locals
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
    _add_create_args(snapset_create_parser)
    snapset_create_parser.add_argument(
        "-a",
        "--autoindex",
        action="store_true",
        help="Automatically create a unique index for recurring snapshot sets",
    )
    _add_json_arg(snapset_create_parser)

    # snapset create-scheduled subcommand
    snapset_create_scheduled_parser = snapset_subparser.add_parser(
        CREATE_SCHEDULED_CMD,
        help="Create scheduled snapshot sets",
    )
    _add_schedule_config_arg(snapset_create_scheduled_parser)
    snapset_create_scheduled_parser.set_defaults(func=_create_cmd)
    _add_json_arg(snapset_create_scheduled_parser)

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

    # snapset split command
    snapset_split_parser = snapset_subparser.add_parser(
        SPLIT_CMD, help="Split snapshot sets"
    )
    snapset_split_parser.set_defaults(func=_split_cmd)
    snapset_split_parser.add_argument(
        "name",
        metavar="NAME",
        type=str,
        action="store",
        help="The name of the snapshot set to be split",
    )
    snapset_split_parser.add_argument(
        "new_name",
        metavar="NEW_NAME",
        type=str,
        action="store",
        help="The new name of the new snapshot to split into",
    )
    snapset_split_parser.add_argument(
        "sources",
        metavar="SOURCE",
        type=str,
        nargs="+",
        help="A device or mount point to be split from this snapshot set",
    )

    # snapset prune command
    snapset_prune_parser = snapset_subparser.add_parser(
        PRUNE_CMD, help="Prune snapshots from snapshot sets"
    )
    snapset_prune_parser.set_defaults(func=_prune_cmd)
    snapset_prune_parser.add_argument(
        "name",
        metavar="NAME",
        type=str,
        action="store",
        help="The name of the snapshot set to be split",
    )
    snapset_prune_parser.add_argument(
        "sources",
        metavar="SOURCE",
        type=str,
        nargs="+",
        help="A device or mount point to be pruned from this snapshot set",
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
    _add_identifier_args(snapset_show_parser, snapset=True)
    snapset_show_parser.add_argument(
        "-m",
        "--members",
        action="store_true",
        help="Show snapshots that are part of each snapshot set",
    )
    _add_json_arg(snapset_show_parser)
    snapset_show_parser.set_defaults(func=_show_cmd)

    # snapset mount subcommand
    snapset_mount_parser = snapset_subparser.add_parser(
        MOUNT_CMD, help="Mount snapshot set"
    )
    snapset_mount_parser.add_argument(
        "name",
        metavar="NAME",
        type=str,
        action="store",
        help="The name of the snapshot set to be mounted",
    )
    snapset_mount_parser.set_defaults(func=_mount_cmd)

    # snapset umount subcommand
    snapset_umount_parser = snapset_subparser.add_parser(
        UMOUNT_CMD, help="Unmount snapshot set"
    )
    snapset_umount_parser.add_argument(
        "name",
        metavar="NAME",
        type=str,
        action="store",
        help="The name of the snapshot set to be unmounted",
    )
    snapset_umount_parser.set_defaults(func=_umount_cmd)

    # snapset exec subcommand
    snapset_exec_parser = snapset_subparser.add_parser(
        EXEC_CMD,
        help="Execute command in snapshot set",
    )
    snapset_exec_parser.add_argument(
        "name",
        metavar="NAME",
        type=str,
        action="store",
        help="The name of the snapshot set in which to execute the command",
    )
    snapset_exec_parser.add_argument(
        "command",
        metavar="COMMAND",
        default=[],
        nargs=REMAINDER,
        action="extend",
        help="The command to be executed with its arguments",
    )
    snapset_exec_parser.set_defaults(func=_exec_cmd)

    # snapset shell subcommand
    snapset_shell_parser = snapset_subparser.add_parser(
        SHELL_CMD,
        help="Start interactive shell in snapshot set",
    )
    snapset_shell_parser.add_argument(
        "name",
        metavar="NAME",
        type=str,
        action="store",
        help="The name of the snapshot set in which to start the shell",
    )
    snapset_shell_parser.set_defaults(func=_shell_cmd)

    # snapset diff subcommand
    snapset_diff_parser = snapset_subparser.add_parser(
        DIFF_CMD,
        help="Compare between snapshot sets or the running system",
    )
    snapset_diff_parser.add_argument(
        "-o",
        "--output-format",
        type=str,
        metavar="FORMAT",
        choices=DIFF_FORMATS,
        default=DIFF_FORMATS[0],
        help=f"Output format for diff data ({', '.join(DIFF_FORMATS)})",
    )
    _add_diff_args(snapset_diff_parser)
    snapset_diff_parser.set_defaults(func=_diff_cmd)

    # snapset diffreport subcommand
    snapset_diffreport_parser = snapset_subparser.add_parser(
        DIFFREPORT_CMD,
        help=(
            "Generate report of differences between snapshot sets or the "
            "running system"
        ),
    )
    _add_report_args(snapset_diffreport_parser)
    _add_diff_args(snapset_diffreport_parser)
    snapset_diffreport_parser.set_defaults(func=_diffreport_cmd)


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


def _add_schedule_subparser(type_subparser):
    """
    Add subparser for 'schedule' commands.

    :param type_subparser: Command type subparser
    """
    # Subparser for schedule commands
    schedule_parser = type_subparser.add_parser(
        SCHEDULE_TYPE,
        help="Schedule commands",
    )
    schedule_subparser = schedule_parser.add_subparsers(dest="command")

    # schedule create subcommand
    schedule_create_parser = schedule_subparser.add_parser(
        CREATE_CMD,
        help="Create schedule",
    )

    schedule_create_parser.add_argument(
        "schedule_name",
        metavar="SCHEDULE_NAME",
        type=str,
        action="store",
        help="The name of the schedule to create",
    )
    schedule_create_parser.add_argument(
        "-C",
        "--calendarspec",
        type=str,
        metavar="CALENDARSPEC",
        help="Calendar trigger expression",
    )
    _add_create_args(schedule_create_parser)
    _add_policy_args(schedule_create_parser)
    _add_json_arg(schedule_create_parser)
    schedule_create_parser.set_defaults(func=_schedule_create_cmd)

    # schedule delete subcommand
    schedule_delete_parser = schedule_subparser.add_parser(
        DELETE_CMD,
        help="Delete schedule by name",
    )

    schedule_delete_parser.add_argument(
        "schedule_name",
        metavar="SCHEDULE_NAME",
        type=str,
        action="store",
        help="The name of the schedule to delete",
    )
    schedule_delete_parser.set_defaults(func=_schedule_delete_cmd)

    # schedule edit subcommand
    schedule_edit_parser = schedule_subparser.add_parser(
        EDIT_CMD,
        help="Edit schedule",
    )

    schedule_edit_parser.add_argument(
        "schedule_name",
        metavar="SCHEDULE_NAME",
        type=str,
        action="store",
        help="The name of the schedule to edit",
    )
    schedule_edit_parser.add_argument(
        "-C",
        "--calendarspec",
        type=str,
        metavar="CALENDARSPEC",
        help="Calendar trigger expression",
    )
    schedule_edit_parser.add_argument(
        "sources",
        metavar="SOURCE",
        type=str,
        nargs="*",
        help="A device or mount point path to include in this snapshot set",
    )
    schedule_edit_parser.add_argument(
        "-s",
        "--size-policy",
        type=str,
        action="store",
        help="A default size policy for fixed size snapshots",
    )
    schedule_edit_parser.add_argument(
        "-b",
        "--bootable",
        action="store_true",
        default=None,
        help="Create a boot entry for this snapshot set",
    )
    schedule_edit_parser.add_argument(
        "-r",
        "--revert",
        action="store_true",
        default=None,
        help="Create a revert boot entry for this snapshot set",
    )
    _add_policy_args(schedule_edit_parser, required=False)
    _add_json_arg(schedule_edit_parser)
    schedule_edit_parser.set_defaults(func=_schedule_edit_cmd)

    # schedule enable subcommand
    schedule_enable_parser = schedule_subparser.add_parser(
        ENABLE_CMD,
        help="Enable schedule by name",
    )

    schedule_enable_parser.add_argument(
        "schedule_name",
        metavar="SCHEDULE_NAME",
        type=str,
        action="store",
        help="The name of the schedule to enable",
    )
    schedule_enable_parser.add_argument(
        "-s",
        "--start",
        action="store_true",
        help="Start the schedule immediately",
    )
    schedule_enable_parser.set_defaults(func=_schedule_enable_cmd)

    # schedule disable subcommand
    schedule_disable_parser = schedule_subparser.add_parser(
        DISABLE_CMD,
        help="Disable schedule by name",
    )

    schedule_disable_parser.add_argument(
        "schedule_name",
        metavar="SCHEDULE_NAME",
        type=str,
        action="store",
        help="The name of the schedule to disable",
    )
    schedule_disable_parser.set_defaults(func=_schedule_disable_cmd)

    # schedule list subcommand
    schedule_list_parser = schedule_subparser.add_parser(
        LIST_CMD,
        help="List schedules",
    )
    _add_report_args(schedule_list_parser)
    schedule_list_parser.set_defaults(func=_schedule_list_cmd)

    # schedule show subcommand
    schedule_show_parser = schedule_subparser.add_parser(
        SHOW_CMD,
        help="Show schedules",
    )
    schedule_show_parser.add_argument(
        "schedule_name",
        metavar="SCHEDULE_NAME",
        type=str,
        action="store",
        help="The name of the schedule to show",
    )
    _add_json_arg(schedule_show_parser)
    schedule_show_parser.set_defaults(func=_schedule_show_cmd)

    schedule_gc_parser = schedule_subparser.add_parser(
        GC_CMD,
        help="Run garbage collection for schedule",
    )
    _add_schedule_config_arg(schedule_gc_parser)
    schedule_gc_parser.set_defaults(func=_schedule_gc_cmd)


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

    _add_schedule_subparser(type_subparser)

    cmd_args = parser.parse_args(args[1:])

    status = 1

    try:
        set_debug(cmd_args.debug)
    except ValueError as err:
        print(err)
        parser.print_help()
        return status

    setup_logging(cmd_args)

    if os.geteuid() != 0:
        _log_error("snapm must be run as the root user")
        shutdown_logging()
        return status

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
        except KeyboardInterrupt:  # pragma: no cover
            _log_error("Exiting on user cancel")
        except Exception as err:
            _log_error("Command failed: %s", err)

    shutdown_logging()
    return status


# vim: set et ts=4 sw=4 :
