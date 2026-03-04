# Copyright Red Hat
#
# snapm/manager/plugins/__init__.py - Snapshot Manager plugins
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Snapshot manager plugin helpers.
"""
import os
from os.path import sep as path_sep, ismount
from abc import ABC, abstractmethod
import logging

from snapm import SNAPM_VALID_NAME_CHARS, SNAPM_SUBSYSTEM_PLUGIN, Snapshot

_MOUNT_SEPARATOR = "-"
_ESCAPED_MOUNT_SEPARATOR = "--"

_PROC_MOUNTS = "/proc/self/mounts"

DEV_PREFIX = "/dev"
DEV_MAPPER_PREFIX = "/dev/mapper/"

# dmsetup options
DMSETUP_CMD = "dmsetup"
DMSETUP_SPLITNAME = "splitname"
DMSETUP_INFO = "info"
DMSETUP_LVM_SUBSYS = "LVM"
DMSETUP_NO_HEADINGS = "--noheadings"
DMSETUP_COLUMNS = "--columns"
DMSETUP_FIELDS_VG_LV = "-ovg_name,lv_name"
DMSETUP_FIELDS_UUID = "-ouuid"
DMSETUP_REPORT_SEP = ":"

# Fields: origin_name-snapset_snapset-name_timestamp_mount-point
SNAPSHOT_NAME_FORMAT = "%s-snapset_%s_%d_%s"

#: Plugin configuration Limits section
_PLUGIN_CFG_LIMITS = "Limits"

#: Plugin configuration snapshots per origin
_PLUGIN_CFG_SNAPS_PER_ORIGIN = "MaxSnapshotsPerOrigin"

#: Plugin configuration snapshots per pool
_PLUGIN_CFG_SNAPS_PER_POOL = "MaxSnapshotsPerPool"

#: Plugin Priority section
_PLUGIN_CFG_PRIORITY = "Priority"

#: PluginPriority value
_PLUGIN_CFG_PLUGIN_PRIORITY = "PluginPriority"

#: Plugin priority if unset: subclasses should check for this and set.
PLUGIN_NO_PRIORITY = 0

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


def _log_debug_plugin(msg, *args, **kwargs):
    """A wrapper for plugin subsystem debug logs."""
    _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_PLUGIN}, **kwargs)


class PluginLimits:
    """
    Per-plugin resource limits.
    """

    def __init__(self, cfg):
        """
        Initialise a new ``PluginLimits`` instance.
        """
        self.snapshots_per_origin = 0
        self.snapshots_per_pool = 0

        if cfg.has_section(_PLUGIN_CFG_LIMITS):
            if cfg.has_option(_PLUGIN_CFG_LIMITS, _PLUGIN_CFG_SNAPS_PER_ORIGIN):
                try:
                    self.snapshots_per_origin = cfg.getint(
                        _PLUGIN_CFG_LIMITS, _PLUGIN_CFG_SNAPS_PER_ORIGIN
                    )
                except ValueError:
                    value = cfg.get(_PLUGIN_CFG_LIMITS, _PLUGIN_CFG_SNAPS_PER_ORIGIN)
                    _log_warn(
                        "Ignoring invalid plugin %s value in %s: %s",
                        _PLUGIN_CFG_SNAPS_PER_ORIGIN,
                        cfg.plugin_conf_file,
                        str(value),
                    )
            if cfg.has_option(_PLUGIN_CFG_LIMITS, _PLUGIN_CFG_SNAPS_PER_POOL):
                try:
                    self.snapshots_per_pool = cfg.getint(
                        _PLUGIN_CFG_LIMITS, _PLUGIN_CFG_SNAPS_PER_POOL
                    )
                except ValueError:
                    value = cfg.get(_PLUGIN_CFG_LIMITS, _PLUGIN_CFG_SNAPS_PER_POOL)
                    _log_warn(
                        "Ignoring invalid plugin %s value in %s: %s",
                        _PLUGIN_CFG_SNAPS_PER_POOL,
                        cfg.plugin_conf_file,
                        str(value),
                    )


class Plugin(ABC):
    """
    Abstract base class for snapshot manager plugins.
    """

    name = "plugin"
    version = "0.2.0"
    snapshot_class = Snapshot

    def __init__(self, logger, plugin_cfg):
        self.size_map = None
        self.logger = logger

        self.limits = PluginLimits(plugin_cfg)

        priority = PLUGIN_NO_PRIORITY

        if plugin_cfg.has_section(_PLUGIN_CFG_PRIORITY):
            if plugin_cfg.has_option(_PLUGIN_CFG_PRIORITY, _PLUGIN_CFG_PLUGIN_PRIORITY):
                try:
                    priority = plugin_cfg.getint(
                        _PLUGIN_CFG_PRIORITY,
                        _PLUGIN_CFG_PLUGIN_PRIORITY,
                    )
                except ValueError:
                    # We know it has a value, just not a valid one: We'll fix it in post.
                    priority = plugin_cfg.get(
                        _PLUGIN_CFG_PRIORITY,
                        _PLUGIN_CFG_PLUGIN_PRIORITY,
                    )
                if not isinstance(priority, int) or priority <= PLUGIN_NO_PRIORITY:
                    _log_warn(
                        "Ignoring invalid plugin priority value in %s: %s",
                        plugin_cfg.plugin_conf_file,
                        str(priority),
                    )
                    priority = PLUGIN_NO_PRIORITY

        self.priority = priority

    def _log_error(self, *args):
        """
        Log at error level.
        """
        self.logger.error(*args)

    def _log_warn(self, *args):
        """
        Log at warning level.
        """
        self.logger.warning(*args)

    def _log_info(self, *args):
        """
        Log at info level.
        """
        self.logger.info(*args)

    def _log_debug(self, *args):
        """
        Log at debug level.
        """
        self.logger.debug(*args)

    def info(self):
        """
        Return plugin name and version.
        """
        return {"name": self.name, "version": self.version}

    def start_transaction(self):
        """
        Begin a snapshot set creation transaction in this plugin.
        """
        self.size_map = {}

    def end_transaction(self):
        """
        End a snapshot set creation transaction in this plugin.
        """
        self.size_map = None

    @abstractmethod
    def discover_snapshots(self):
        """
        Discover snapshots managed by this plugin class.

        Returns a list of objects that are a subclass of ``Snapshot``.
        :returns: A list of snapshots discovered by this plugin class.
        """

    @abstractmethod
    def can_snapshot(self, source):
        """
        Test whether this plugin can snapshot the specified mount point or
        block device.

        :param source: The block device or mount point path to test.
        :returns: ``True`` if this plugin can snapshot the file system mounted
                  at ``mount_point``, or ``False`` otherwise.
        """

    @abstractmethod
    def check_create_snapshot(
        self, origin, snapset_name, timestamp, mount_point, size_policy
    ):
        """
        Perform pre-creation checks before creating a snapshot.

        :param origin: The origin volume for the snapshot.
        :param snapset_name: The name of the snapshot set to be created.
        :param timestamp: The snapshot set timestamp.
        :param mount_point: The mount point path for this snapshot.
        :raises: ``SnapmNoSpaceError`` if there is insufficient free space to
                 create the snapshot.
        """

    @abstractmethod
    def create_snapshot(
        self, origin, snapset_name, timestamp, mount_point, size_policy
    ):
        """
        Create a snapshot of ``origin`` in the snapset named ``snapset_name``.

        :param origin: The origin volume for the snapshot.
        :param snapset_name: The name of the snapshot set to be created.
        :param timestamp: The snapshot set timestamp.
        :param mount_point: The mount point path for this snapshot.
        :raises: ``SnapmNoSpaceError`` if there is insufficient free space to
                 create the snapshot.
        """

    @abstractmethod
    def rename_snapshot(self, old_name, origin, snapset_name, timestamp, mount_point):
        """
        Rename the snapshot named ``old_name`` according to the provided
        snapshot field values.

        :param old_name: The original name of the snapshot to be renamed.
        :param origin: The origin volume for the snapshot.
        :param snapset_name: The new name of the snapshot set.
        :param timestamp: The snapshot set timestamp.
        :param mount_point: The mount point of the snapshot.
        """

    @abstractmethod
    def check_resize_snapshot(self, name, origin, mount_point, size_policy):
        """
        Check whether this snapshot can be resized to the requested
        ``size_policy``. This method returns if the resize can be satisfied and
        raises an exception if not.

        :returns: None
        :raises: ``SnapmNoSpaceError`` if insufficient space is available to
                 satisfy the requested size policy or ``SnapmPluginError`` if
                 another reason prevents the snapshot from being resized.
        """

    @abstractmethod
    def resize_snapshot(self, name, origin, mount_point, size_policy):
        """
        Attempt to resize the snapshot ``name`` to the requested
        ``size_policy``. This method returns if the resize can be satisfied and
        raises an exception if not.

        :returns: None
        :raises: ``SnapmNoSpaceError`` if insufficient space is available to
                 satisfy the requested size policy or ``SnapmPluginError`` if
                 another reason prevents the snapshot from being resized.
        """

    @abstractmethod
    def check_revert_snapshot(self, name, origin):
        """
        Check whether this snapshot can be reverted or not. This method returns
        if the current snapshot can be reverted and raises an exception if not.

        :returns: None
        :raises: ``NotImplementedError`` if this plugin does not support the
                 revert operation, ``SnapmBusyError`` if the snapshot is already
                 in the process of being reverted to another snapshot state or
                 ``SnapmPluginError`` if another reason prevents the snapshot
                 from being merged.
        """

    @abstractmethod
    def revert_snapshot(self, name):
        """
        Request to revert a snapshot and revert the content of the origin
        volume to its state at the time of the snapshot.

        This may be deferred until the next device activation or mount
        operation for the respective volume.

        :param name: The name of the snapshot to revert.
        """

    @abstractmethod
    def delete_snapshot(self, name):
        """
        Delete the snapshot named ``name``

        :param name: The name of the snapshot to be removed.
        """

    @abstractmethod
    def activate_snapshot(self, name):
        """
        Activate the snapshot named ``name``

        :param name: The name of the snapshot to be activated.
        """

    @abstractmethod
    def deactivate_snapshot(self, name):
        """
        Deactivate the snapshot named ``name``

        :param name: The name of the snapshot to be deactivated.
        """

    @abstractmethod
    def set_autoactivate(self, name, auto=False):
        """
        Set the autoactivation state of the snapshot named ``name``.

        :param name: The name of the snapshot to be modified.
        :param auto: ``True`` to enable autoactivation or ``False`` otherwise.
        """

    @staticmethod
    def _check_limit(limit, tracking, key):
        """
        Check whether adding a new snapshot would exceed a configured limit.

        :param limit: The configured maximum (0 means unlimited).
        :param tracking: A dict mapping keys to current snapshot counts.
        :param key: The key to look up in ``tracking``.
        :returns: ``True`` if adding a new snapshot would exceed the limit,
                 or ``False`` otherwise.
        :rtype: ``bool``
        """
        if not limit:
            return False
        if key not in tracking:
            return False
        if tracking[key] + 1 > limit:
            return True
        return False

    @abstractmethod
    def origin_from_mount_point(self, mount_point):
        """
        Return a string representing the origin from a given mount point path.

        :param mount_point: The mount point path.
        """


def _escape_bad_chars(path):
    """
    Encode illegal characters in mount point path.

    :param path: The path to escape.
    :returns: The escaped path.
    """
    escaped = ""
    for char in path:
        if char != path_sep and char not in SNAPM_VALID_NAME_CHARS:
            escaped = escaped + "." + char.encode("utf8").hex()
        else:
            if char == ".":
                escaped = escaped + ".."
            else:
                escaped = escaped + char
    return escaped


def _unescape_bad_chars(path):
    """
    Decode illegal characters in mount point path.

    :param path: The path to unescape.
    :returns: The unescaped path.
    """

    def decode_byte(byte):
        return bytearray.fromhex(byte).decode("utf8")

    unescaped = ""
    i = 0
    while i < len(path):
        if path[i] == ".":
            if path[i + 1] == ".":
                unescaped = unescaped + "."
                i += 1
            else:
                unescaped = unescaped + decode_byte(path[i + 1 : i + 3])
                i += 2
        else:
            unescaped = unescaped + path[i]
        i += 1
    return unescaped


def encode_mount_point(mount_point):
    """
    Encode mount point paths.

    Encode a mount point for use in a snapshot name by replacing the path
    separator with '-'.
    """
    if mount_point == "/":
        return _MOUNT_SEPARATOR
    mount_point = _escape_bad_chars(mount_point)
    parts = [
        part.replace(_MOUNT_SEPARATOR, _ESCAPED_MOUNT_SEPARATOR)
        for part in mount_point.split(path_sep)
    ]
    return _MOUNT_SEPARATOR.join(parts)


def _split_mount_separators(mount_str):
    """
    Split string by mount separator handling escaped separators.

    Split the mount string ``mount_str`` at ``_MOUNT_SEPARATOR`` boundaries,
    into path components, ignoring ``_ESCAPED_MOUNT_SEPARATOR``.
    """
    result = []
    current = ""
    i = 0
    while i < len(mount_str):
        if mount_str[i] == _MOUNT_SEPARATOR:
            if i < len(mount_str) - 1 and mount_str[i + 1] == _MOUNT_SEPARATOR:
                current += _ESCAPED_MOUNT_SEPARATOR
                i += 1
            else:
                result.append(current)
                current = ""
        else:
            current += mount_str[i]
        i += 1

    result.append(current)  # Append the remaining segment
    return result


def decode_mount_point(mount_str):
    """
    Parse the mount point component of a snapshot name.
    """
    mount_str = _unescape_bad_chars(mount_str)
    mount_parts = _split_mount_separators(mount_str)
    unescaped_parts = [
        part.replace(_ESCAPED_MOUNT_SEPARATOR, _MOUNT_SEPARATOR) for part in mount_parts
    ]
    return path_sep.join(unescaped_parts)


def parse_snapshot_name(full_name, origin):
    """
    Attempt to parse a snapshot set snapshot name.

    Returns a tuple of (snapset_name, timestamp, mount_point) if ``full_name``
    is a valid snapset snapshot name, or ``None`` otherwise.
    """
    if not full_name.startswith(origin):
        return None
    base = full_name.removeprefix(origin + "-")
    if "_" not in base:
        return None
    fields = base.split("_", maxsplit=3)
    if len(fields) != 4:
        return None
    if fields[0] != "snapset":
        return None
    snapset_name = fields[1]
    timestamp = int(fields[2])
    mount_point = decode_mount_point(fields[3])
    # (snapset_name, timestamp, mount)
    return (snapset_name, timestamp, mount_point)


def _parse_proc_mounts_line(mount_line):
    """
    Parse ``/proc/mounts`` line.

    Parse a line of ``/proc/mounts`` data and return a tuple
    containing the device and mount point.
    """
    (device, mount_point, _) = mount_line.split(maxsplit=2)
    return (device, mount_point)


def device_from_mount_point(mount_point):
    """
    Convert a mount point path to a corresponding device path.

    Return the device corresponding to the file system mount point
    ``mount_point`` according to /proc/self/mounts.
    """
    # Ignore any trailing '/' in mount point path
    if mount_point != path_sep:
        mount_point = mount_point.rstrip(path_sep)
    with open(_PROC_MOUNTS, "r", encoding="utf8") as mounts:
        for line in mounts.readlines():
            (device, mount_path) = _parse_proc_mounts_line(line)
            if mount_path == mount_point:
                return device
    raise KeyError(f"Mount point {mount_point} not found")


def mount_point_space_used(mount_point):
    """
    Retrieve space usage for a mount point path.

    Return the space used on the file system mounted at ``mount_point``
    as a value in bytes.
    """
    if not mount_point or not ismount(mount_point):
        return -1
    stat = os.statvfs(mount_point)
    return (stat.f_blocks - stat.f_bfree) * stat.f_frsize


def format_snapshot_name(origin, snapset_name, timestamp, mount_point):
    """
    Format structured snapshot name.

    Format a snapshot name according to the snapshot manager naming format.
    """
    return SNAPSHOT_NAME_FORMAT % (origin, snapset_name, timestamp, mount_point)


__all__ = [
    "DEV_PREFIX",
    "DEV_MAPPER_PREFIX",
    "DMSETUP_CMD",
    "DMSETUP_SPLITNAME",
    "DMSETUP_INFO",
    "DMSETUP_LVM_SUBSYS",
    "DMSETUP_NO_HEADINGS",
    "DMSETUP_COLUMNS",
    "DMSETUP_FIELDS_VG_LV",
    "DMSETUP_FIELDS_UUID",
    "DMSETUP_REPORT_SEP",
    "PLUGIN_NO_PRIORITY",
    "PluginLimits",
    "Plugin",
    "encode_mount_point",
    "decode_mount_point",
    "parse_snapshot_name",
    "device_from_mount_point",
    "mount_point_space_used",
    "format_snapshot_name",
]
