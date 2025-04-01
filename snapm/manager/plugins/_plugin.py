# Copyright Red Hat
#
# snapm/manager/plugins/__init__.py - Snapshot Manager plugins
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: GPL-2.0-only
"""
Snapshot manager plugin helpers.
"""
import os
from os.path import sep as path_sep, ismount

from snapm import SNAPM_VALID_NAME_CHARS

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
    "encode_mount_point",
    "decode_mount_point",
    "parse_snapshot_name",
    "device_from_mount_point",
    "mount_point_space_used",
    "format_snapshot_name",
]
