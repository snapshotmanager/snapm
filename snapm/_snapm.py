# Copyright Red Hat
#
# snapm/_snapm.py - Snapshot Manager global definitions
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Global definitions for the top-level snapm package.
"""
from typing import Union, Optional
from uuid import UUID, uuid5
from datetime import datetime
from stat import S_ISBLK
from enum import Enum
import collections
import subprocess
import string
import json
import math
import logging
import re
import os

_log = logging.getLogger("snapm")

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error

# Snapm debugging subsystem mask (legacy interface)
SNAPM_DEBUG_MANAGER = 1
SNAPM_DEBUG_COMMAND = 2
SNAPM_DEBUG_REPORT = 4
SNAPM_DEBUG_MOUNTS = 8
SNAPM_DEBUG_ALL = (
    SNAPM_DEBUG_MANAGER | SNAPM_DEBUG_COMMAND | SNAPM_DEBUG_REPORT | SNAPM_DEBUG_MOUNTS
)

# Snapm debugging subsystem names
SNAPM_SUBSYSTEM_MANAGER = "snapm.manager"
SNAPM_SUBSYSTEM_COMMAND = "snapm.command"
SNAPM_SUBSYSTEM_REPORT = "snapm.report"
SNAPM_SUBSYSTEM_MOUNTS = "snapm.mounts"

_DEBUG_MASK_TO_SUBSYSTEM = {
    SNAPM_DEBUG_MANAGER: SNAPM_SUBSYSTEM_MANAGER,
    SNAPM_DEBUG_COMMAND: SNAPM_SUBSYSTEM_COMMAND,
    SNAPM_DEBUG_REPORT: SNAPM_SUBSYSTEM_REPORT,
    SNAPM_DEBUG_MOUNTS: SNAPM_SUBSYSTEM_MOUNTS,
}

_debug_subsystems = set()

#: Top-level state directory: managed by systemd/tmpfiles.d/snapm.conf
SNAPM_RUNTIME_DIR = "/run/snapm"

NAMESPACE_SNAPSHOT_SET = UUID("{952f0e38-24a1-406d-adf6-0e9fb3c707d8}")
NAMESPACE_SNAPSHOT = UUID("{c17d07c7-1482-43b7-9b3c-12d490622d93}")

_SIZE_RE = re.compile(r"^(?P<size>[0-9]+)(?P<units>([KMGTPEZkmgtpez]i{,1})?[Bb]{,1})$")

#: All suffixes are expressed in powers of two.
_SIZE_SUFFIXES = {
    "B": 1,
    "K": 2**10,
    "M": 2**20,
    "G": 2**30,
    "T": 2**40,
    "P": 2**50,
    "E": 2**60,
    "Z": 2**70,
}

SECTOR_SIZE = 512

ETC_FSTAB = "/etc/fstab"

DEFAULT_PERCENT_USED = 200.0
DEFAULT_PERCENT_SIZE = 25.0

# Constants for SnapshotSet property names
SNAPSET_NAME = "SnapsetName"
SNAPSET_BASENAME = "Basename"
SNAPSET_INDEX = "Index"
SNAPSET_SOURCES = "Sources"
SNAPSET_MOUNT_POINTS = "MountPoints"
SNAPSET_DEVICES = "Devices"
SNAPSET_NR_SNAPSHOTS = "NrSnapshots"
SNAPSET_TIME = "Time"
SNAPSET_TIMESTAMP = "Timestamp"
SNAPSET_UUID = "UUID"
SNAPSET_STATUS = "Status"
SNAPSET_AUTOACTIVATE = "Autoactivate"
SNAPSET_MOUNTED = "Mounted"
SNAPSET_ORIGIN_MOUNTED = "OriginMounted"
SNAPSET_MOUNT_ROOT = "MountRoot"
SNAPSET_BOOTABLE = "Bootable"
SNAPSET_BOOT_ENTRIES = "BootEntries"
SNAPSET_SNAPSHOT_ENTRY = "SnapshotEntry"
SNAPSET_REVERT_ENTRY = "RevertEntry"
SNAPSET_SNAPSHOTS = "Snapshots"

# Constants for Snapshot property names
SNAPSHOT_NAME = "Name"
SNAPSHOT_BASENAME = "Basename"
SNAPSHOT_INDEX = "Index"
SNAPSHOT_ORIGIN = "Origin"
SNAPSHOT_SOURCE = "Source"
SNAPSHOT_MOUNT_POINT = "MountPoint"
SNAPSHOT_PROVIDER = "Provider"
SNAPSHOT_UUID = "UUID"
SNAPSHOT_STATUS = "Status"
SNAPSHOT_SIZE = "Size"
SNAPSHOT_FREE = "Free"
SNAPSHOT_SIZE_BYTES = "SizeBytes"
SNAPSHOT_FREE_BYTES = "FreeBytes"
SNAPSHOT_AUTOACTIVATE = "Autoactivate"
SNAPSHOT_DEV_PATH = "DevicePath"

# Constants for Snapshot and SnapshotSet property values
SNAPSET_INDEX_NONE = -1
SNAPSHOT_INDEX_NONE = -1


# Constant for allow-listed name characters
SNAPM_VALID_NAME_CHARS = set(
    string.ascii_lowercase + string.ascii_uppercase + string.digits + "+_.-"
)


class SubsystemFilter(logging.Filter):
    """
    Filters DEBUG records based on a set of enabled subsystem names.
    Non-DEBUG records or DEBUG records without a 'subsystem' attribute
    are always passed through.
    """

    def __init__(self, name=""):
        super().__init__(name)
        self.enabled_subsystems = set(_debug_subsystems)

    def filter(self, record):
        # Always pass non-DEBUG messages.
        if record.levelno != logging.DEBUG:
            return True

        # Always pass DEBUG messages that aren't for a specific subsystem.
        if not hasattr(record, "subsystem"):
            return True

        # For subsystem-specific DEBUG messages, check if the subsystem is enabled.
        return record.subsystem in self.enabled_subsystems

    def set_debug_subsystems(self, subsystems):
        """Sets the collection of subsystems to allow."""
        self.enabled_subsystems = set(subsystems)


def get_debug_mask():
    """
    Return the current debug mask for the ``snapm`` package.

    :returns: The current debug mask value
    :rtype: int
    """
    enabled_subsystems = set(_debug_subsystems)
    snapm_log = logging.getLogger("snapm")

    for handler in snapm_log.handlers:
        for f in handler.filters:
            if isinstance(f, SubsystemFilter):
                enabled_subsystems.update(f.enabled_subsystems)

    mask_map = {v: k for k, v in _DEBUG_MASK_TO_SUBSYSTEM.items()}
    mask = 0
    for subsystem_name in enabled_subsystems:
        mask |= mask_map.get(subsystem_name, 0)
    return mask


def set_debug_mask(mask):
    """
    Set the debug mask for the ``snapm`` package.

    :param mask: the logical OR of the ``SNAPM_DEBUG_*``
                 values to log.
    :rtype: None
    """
    # pylint: disable=global-statement
    global _debug_subsystems

    if mask < 0 or mask > SNAPM_DEBUG_ALL:
        raise ValueError(f"Invalid snapm debug mask: {mask}")

    enabled_subsystems = []
    for flag, subsystem_name in _DEBUG_MASK_TO_SUBSYSTEM.items():
        if mask & flag:
            enabled_subsystems.append(subsystem_name)

    snapm_log = logging.getLogger("snapm")
    for handler in snapm_log.handlers:
        for f in handler.filters:
            if isinstance(f, SubsystemFilter):
                f.set_debug_subsystems(enabled_subsystems)

    _debug_subsystems = set(enabled_subsystems)


#
# Snapm exception types
#


class SnapmError(Exception):
    """
    Base class for snapshot manager errors.
    """


class SnapmSystemError(SnapmError):
    """
    An error when calling the operating system.
    """


class SnapmCalloutError(SnapmError):
    """
    An error calling out to an external program.
    """


class SnapmNoSpaceError(SnapmError):
    """
    Insufficient space is available for the requested operation.
    """


class SnapmNoProviderError(SnapmError):
    """
    No snapshot provider plugin was found.
    """


class SnapmSizePolicyError(SnapmError):
    """
    An invalid size policy was specified.
    """


class SnapmExistsError(SnapmError):
    """
    The named snapshot set already exists.
    """


class SnapmBusyError(SnapmError):
    """
    A resource needed by the current command is already in use: for e.g.
    a snapshot merge is in progress for a previous snapshot set.
    """


class SnapmPathError(SnapmError):
    """
    An invalid path was supplied, for example attempting to snapshot
    something that is not a mount point or block device.
    """


class SnapmNotFoundError(SnapmError):
    """
    The requested object does not exist.
    """


class SnapmInvalidIdentifierError(SnapmError):
    """
    An invalid identifier was given.
    """


class SnapmParseError(SnapmError):
    """
    An error parsing user input.
    """


class SnapmPluginError(SnapmError):
    """
    An error performing an action via a plugin.
    """


class SnapmStateError(SnapmError):
    """
    The status of an object does not allow an operation to proceed.
    """


class SnapmRecursionError(SnapmError):
    """
    A snapshot set source corresponds to a snapshot of another snapshot
    set.
    """


class SnapmArgumentError(SnapmError):
    """
    An invalid argument was passed to a snapshot manager API call.
    """


class SnapmTimerError(SnapmError):
    """
    An error manipulating systemd timers.
    """


class SnapmLimitError(SnapmError):
    """
    A configured resource limit would be exceeded.
    """


class SnapmMountError(SnapmError):
    """
    An error performing a mount operation.
    """

    def __init__(self, what: str, where: str, status: int, stderr: str):
        """
        Initialise a new `SnapmMountError` exception.

        :param what: The source for the failed mount operation.
        :param where: The intended mount point of the operation.
        :param status: The exit status of the mount(8) program.
        :param stderr: The error message from mount(8).
        """
        self.what, self.where, self.status, self.stderr = what, where, status, stderr
        msg = f"Failed to mount {what} to {where} (status={status}): {stderr}"
        super().__init__(msg)


class SnapmUmountError(SnapmError):
    """
    An error performing an unmount operation.
    """

    def __init__(self, where: str, status: int, stderr: str):
        """
        Initialise a new `SnapmUmountError` exception.

        :param where: The mount point for the failed umount operation.
        :param status: The exit status of the umount(8) program.
        :param stderr: The error message from umount(8).
        """
        self.where, self.status, self.stderr = where, status, stderr
        msg = f"Failed to unmount {where} (status={status}): {stderr}"
        super().__init__(msg)


#
# Selection criteria class
#


# pylint: disable=too-many-instance-attributes
class Selection:
    """
    Selection criteria for snapshot sets and snapshots.
    """

    # Snapshot set fields
    name = None
    uuid = None
    basename = None
    index = None
    timestamp = None
    nr_snapshots = None
    mount_points = None

    # Snapshot fields
    origin = None
    mount_point = None
    snapshot_name = None
    snapshot_uuid = None

    # Schedule fields
    sched_name: Union[None, str] = None
    sched_sources: Union[None, str] = None
    sched_default_size_policy: Union[None, str] = None
    sched_autoindex: Union[None, bool] = None
    sched_gc_type: Union[None, str] = None
    sched_gc_params: Union[None, str] = None
    sched_enabled: Union[None, bool] = None
    sched_running: Union[None, bool] = None
    sched_calendarspec: Union[None, str] = None

    snapshot_set_attrs = [
        "name",
        "uuid",
        "basename",
        "index",
        "timestamp",
        "nr_snapshots",
        "mount_points",
    ]

    snapshot_attrs = [
        "origin",
        "mount_point",
        "snapshot_name",
        "snapshot_uuid",
    ]

    schedule_attrs = [
        "sched_name",
        "sched_sources",
        "sched_default_size_policy",
        "sched_autoindex",
        "sched_gc_type",
        "sched_gc_params",
        "sched_enabled",
        "sched_running",
        "sched_calendarspec",
    ]

    all_attrs = snapshot_set_attrs + snapshot_attrs + schedule_attrs

    def __str__(self):
        """
        Format this ``Selection`` object as a human readable string.

        :returns: A human readable string representation of this
                  Selection object
        :rtype: string
        """
        all_attrs = self.all_attrs
        attrs = [attr for attr in all_attrs if self.__attr_has_value(attr)]
        strval = ""
        tail = ", "
        for attr in sorted(set(attrs)):
            strval += f"{attr}='{getattr(self, attr)}'{tail}"
        return strval.rstrip(tail)

    def __repr__(self):
        """
        Format this ``Selection`` object as a machine readable string.

        The returned string may be passed to the Selection
        initialiser to duplicate the original Selection.

        :returns: A machine readable string representation of this
                  Selection object
        :rtype: string
        """
        return "Selection(" + str(self) + ")"

    # pylint: disable=too-many-arguments,too-many-locals
    def __init__(
        self,
        name=None,
        uuid=None,
        basename=None,
        index=None,
        timestamp=None,
        nr_snapshots=None,
        mount_points=None,
        origin=None,
        mount_point=None,
        snapshot_name=None,
        snapshot_uuid=None,
        sched_name: Union[None, str] = None,
        sched_sources: Union[None, str] = None,
        sched_default_size_policy: Union[None, str] = None,
        sched_autoindex: Union[None, bool] = None,
        sched_gc_type: Union[None, str] = None,
        sched_gc_params: Union[None, str] = None,
        sched_enabled: Union[None, bool] = None,
        sched_running: Union[None, bool] = None,
        sched_calendarspec: Union[None, str] = None,
    ):
        self.name = name
        self.uuid = uuid
        self.basename = basename
        self.index = index
        self.timestamp = timestamp
        self.nr_snapshots = nr_snapshots
        self.mount_points = mount_points
        self.origin = origin
        self.mount_point = mount_point
        self.snapshot_name = snapshot_name
        self.snapshot_uuid = snapshot_uuid
        self.sched_name = sched_name
        self.sched_sources = sched_sources
        self.sched_default_size_policy = sched_default_size_policy
        self.sched_autoindex = sched_autoindex
        self.sched_gc_type = sched_gc_type
        self.sched_gc_params = sched_gc_params
        self.sched_enabled = sched_enabled
        self.sched_running = sched_running
        self.sched_calendarspec = sched_calendarspec

    @classmethod
    def from_cmd_args(cls, cmd_args):
        """
        Initialise Selection from command line arguments.

        Construct a new ``Selection`` object from the command line
        arguments in ``cmd_args``. Each set selection attribute from
        ``cmd_args`` is copied into the Selection. The resulting
        object may be passed to either the ``SnapshotSet`` or
        ``Snapshot`` search functions.
        (``find_snapshot_sets``, ``find_snapshots``, as well as
        the ``snapm.command`` calls that accept a selection argument.

        :param cmd_args: The command line selection arguments.
        :returns: A new Selection instance
        :rtype: Selection
        """
        name = None
        uuid = None
        snapshot_name = None
        snapshot_uuid = None
        sched_name = None

        if getattr(cmd_args, "name", None):
            name = cmd_args.name
        elif getattr(cmd_args, "uuid", None):
            uuid = cmd_args.uuid
        elif getattr(cmd_args, "identifier", None):
            try:
                uuid = UUID(cmd_args.identifier)
            except (TypeError, ValueError):
                name = cmd_args.identifier

        if hasattr(cmd_args, "snapshot_name"):
            snapshot_name = cmd_args.snapshot_name
        if hasattr(cmd_args, "snapshot_uuid"):
            snapshot_uuid = cmd_args.snapshot_uuid

        if hasattr(cmd_args, "schedule_name"):
            sched_name = cmd_args.schedule_name

        select = Selection(
            name=name,
            uuid=uuid,
            snapshot_name=snapshot_name,
            snapshot_uuid=snapshot_uuid,
            sched_name=sched_name,
        )

        _log_debug("Initialised %s from arguments", repr(select))
        return select

    def __attr_has_value(self, attr):
        """
        Test whether an attribute is defined.

        Return ``True`` if the specified attribute name is currently
        defined, or ``False`` otherwise.

        :param attr: The name of the attribute to test
        :returns: ``True`` if ``attr`` is set or ``False`` otherwise
        :rtype: bool
        """
        return hasattr(self, attr) and getattr(self, attr) is not None

    def check_valid_selection(self, snapshot_set=False, snapshot=False, schedule=False):
        """
        Check a Selection for valid criteria.

        Check this ``Selection`` object to ensure it contains only
        criteria that are valid for the specified object type(s).

        Returns ``None`` if the object passes the check, or raise
        ``ValueError`` if invalid criteria exist.

        :param snapshot_set: ``Selection`` may include SnapshotSet data
        :param snapshot: ``Selection`` may include Snapshot data
        :returns: ``None`` on success
        :rtype: ``NoneType``
        :raises: ``ValueError`` if excluded criteria are present
        """
        valid_attrs = []
        invalid_attrs = []

        if snapshot_set:
            valid_attrs += self.snapshot_set_attrs
        if snapshot:
            valid_attrs += self.snapshot_attrs
        if schedule:
            valid_attrs += self.schedule_attrs

        for attr in self.all_attrs:
            if self.__attr_has_value(attr) and attr not in valid_attrs:
                invalid_attrs.append(attr)

        if invalid_attrs:
            invalid = ", ".join(invalid_attrs)
            raise ValueError(f"Invalid criteria for selection type: {invalid}")

    def is_null(self):
        """
        Test this Selection object for null selection criteria.

        Return ``True`` if this ``Selection`` object matches all
        objects, or ``False`` otherwise.

        :returns: ``True`` if this Selection is null
        :rtype: bool
        """
        all_attrs = self.all_attrs
        attrs = [attr for attr in all_attrs if self.__attr_has_value(attr)]
        return not any(attrs)

    def is_single(self):
        """
        Test this Selection object for single item selection criteria.

        Returns ``True`` if this ``Selection`` object matches a single
        object or ``False`` otherwise.

        A ``Selection`` object matches a single object if either the
        ``name`` or ``uuid`` fields is specified.

        :returns: ``True`` if this selection is single or ``False``
                  otherwise.
        :rtype: bool
        """
        return self.name is not None or self.uuid is not None


def size_fmt(value):
    """
    Format a size in bytes as a human readable string.

    :param value: The integer value to format.
    :returns: A human readable string reflecting value.
    """
    suffixes = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB"]
    if value == 0:
        return "0B"
    magnitude = int(math.floor(math.log(value, 1024)))
    val = value / math.pow(1024, magnitude)
    if magnitude > 7:
        return f"{val:.1f}YiB"
    return f"{val:3.1f}{suffixes[magnitude]}"


def parse_size_with_units(value):
    """
    Parse a size string with optional unit suffix and return a value in bytes,

    :param size: The size string to parse.
    :returns: an integer size in bytes.
    :raises: ``ValueError`` if the string could not be parsed as a valid size
             value.
    """
    match = _SIZE_RE.search(value)
    if match is None:
        raise SnapmSizePolicyError(f"Malformed size expression: '{value}'")
    (size, unit) = (match.group("size"), match.group("units").upper())
    size_bytes = int(size) * _SIZE_SUFFIXES[unit[0]]
    return size_bytes


def bool_to_yes_no(value):
    """
    Convert boolean-like to yes/no string.

    Return the string "yes" if ``value`` evaluates to ``True``, or "no"
    otherwise.

    :param value: A boolean value to interpret.
    :returns: A string value that is "yes" if ``value`` is ``True``, or "no"
              otherwise.
    """
    return "yes" if bool(value) is True else "no"


def is_size_policy(policy):
    """
    Test whether a string is a valid size policy

    :param policy: A possible policy string to test.
    :returns: ``True`` if ``policy`` is a valid size policy string or
              ``False`` otherwise.
    """
    try:
        _ = SizePolicy("/", "/", 0, 0, 0, policy)
        return True
    except SnapmSizePolicyError:
        return False


class SizePolicyType(Enum):
    """
    Enum class representing the possible snapshot size policies.
    """

    FIXED = "FIXED"
    PERCENT_FREE = "FREE"
    PERCENT_USED = "USED"
    PERCENT_SIZE = "SIZE"


class SizePolicy:
    """
    Class representing a configured instance of a size policy.
    """

    def _parse_size_policy(self, policy):
        """
        Parse a string representation of a size policy.
        """
        if policy is None:
            if self._mount:
                self._percent = DEFAULT_PERCENT_USED
                return SizePolicyType.PERCENT_USED
            self._percent = DEFAULT_PERCENT_SIZE
            return SizePolicyType.PERCENT_SIZE
        if "%" not in policy:
            self._size = parse_size_with_units(policy)
            return SizePolicyType.FIXED
        (percent, policy_type) = policy.rsplit("%", maxsplit=1)
        self._percent = float(percent)
        cap_percent = (SizePolicyType.PERCENT_SIZE, SizePolicyType.PERCENT_FREE)
        for ptype in SizePolicyType:
            if ptype.value == policy_type:
                if ptype == SizePolicyType.PERCENT_USED and not self._mount:
                    raise SnapmSizePolicyError(
                        f"Cannot apply %USED size policy to unmounted block device {self._source}"
                    )
                if ptype in cap_percent and self._percent > 100.0:
                    raise SnapmSizePolicyError(
                        f"Size {self._mount}:{self._percent}%{ptype.value} "
                        "cannot be greater than 100%"
                    )
                return ptype
        raise SnapmSizePolicyError(f"Could not parse size policy: {policy}")

    def __init__(self, source, mount, free_space, fs_used, dev_size, policy):
        """
        Initialise a new `SizePolicy` object using the supplied parameters.

        :param source: The source for this SizePolicy: a mount point or block
                       device path.
        :param mount: The mount point path if mounted.
        :param free_space: The free space available to provision the snapshot
                           in bytes.
        :param fs_used: The current file system occupancy in bytes.
        :param dev_size: The origin device size in bytes.
        :param policy: A size policy string.
        :returns: A `SizePolicy` object configured for the specified size.
        """
        self._source = source
        self._mount = mount
        self._free_space = free_space
        self._fs_used = fs_used
        self._dev_size = dev_size
        self._percent = 0
        self._size = 0
        self.type = self._parse_size_policy(policy)

    @property
    def size(self):
        """
        Return the backing store size for a snapshot based on the policy
        configuration.
        """

        def percent_of_sectors(percent, value):
            size = int(math.ceil((percent * value) / 100))
            return SECTOR_SIZE * math.ceil(size / SECTOR_SIZE)

        if self.type == SizePolicyType.FIXED:
            return self._size
        if self.type == SizePolicyType.PERCENT_FREE:
            return percent_of_sectors(self._percent, self._free_space)
        if self.type == SizePolicyType.PERCENT_USED:
            return percent_of_sectors(self._percent, self._fs_used)
        if self.type == SizePolicyType.PERCENT_SIZE:
            return percent_of_sectors(self._percent, self._dev_size)
        raise SnapmParseError(
            f"Invalid size policy type: {self.type}"
        )  # pragma: no cover


class SnapStatus(Enum):
    """
    Enum class representing snapshot status: Active, Inactive, Invalid or
    Reverting.
    """

    ACTIVE = 1
    INACTIVE = 2
    INVALID = 3
    REVERTING = 4

    def __str__(self):
        """
        Return a string representation of this ``SnapStatus`` object.

        :returns: "Active", "Inactive", "Invalid", or "Reverting".
        """
        if self == SnapStatus.ACTIVE:
            return "Active"
        if self == SnapStatus.INACTIVE:
            return "Inactive"
        if self == SnapStatus.REVERTING:
            return "Reverting"
        return "Invalid"


def _unescape_mounts(escaped: str) -> str:
    """
    Unescape octal escapes in values read from /proc/*mounts

    :param escaped: The string to unescape.
    :type escaped: str
    :returns: The unescaped string with octal values replaced by literal
              character values.
    :rtype: str
    """
    return (
        escaped.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _split_name(name):
    return name.rsplit(".", maxsplit=1)


def _has_index(name):
    if "." in name:
        return (_split_name(name)[1]).isdigit()
    return False


def _parse_basename(name):
    if "." in name:
        return _split_name(name)[0]
    return name  # pragma: no cover


def _parse_index(name):
    if "." in name:
        index_str = _split_name(name)[1]
        if not index_str.isdigit():
            return name
        return int(index_str)
    return name


# pylint: disable=too-many-public-methods
class SnapshotSet:
    """
    Representation of a set of snapshots taken at the same point
    in time and managed as a group.
    """

    def _link_snapshots(self):
        self._by_mount_point = {}
        self._by_origin = {}
        for snapshot in self._snapshots:
            snapshot.snapshot_set = self
            if snapshot.mount_point:
                self._by_mount_point[snapshot.mount_point] = snapshot
            self._by_origin[snapshot.origin] = snapshot

    def __init__(self, name, timestamp, snapshots):
        """
        Initialise a new ``SnapshotSet`` object.

        :param name: The name of this ``SnapshotSet``.
        :param timestamp: The creation timestamp of this ``SnapshotSet``.
        :param snapshots: A list of ``Snapshot`` objects to be included in this set.
        """
        self._name = name
        self._uuid = uuid5(NAMESPACE_SNAPSHOT_SET, name + str(timestamp))
        self._timestamp = timestamp
        self._snapshots = snapshots
        self._link_snapshots()
        self.boot_entry = None
        self.revert_entry = None
        self.mount_root = ""

        if _has_index(name):
            self._basename = _parse_basename(name)
            self._index = _parse_index(name)
        else:
            self._basename = name
            self._index = SNAPSET_INDEX_NONE

    def __str__(self):
        """
        Return a string representation of this ``SnapshotSet``.

        :returns: A multi-line string describing this snapshot set.
        """
        snapset_str = (
            f"{SNAPSET_NAME}:      {self.name}\n"
            f"{SNAPSET_SOURCES}:          {', '.join(self.sources)}\n"
        )

        if len(self.mount_points) > 0 and len(self.devices) > 0:
            snapset_str += (
                f"  {SNAPSET_MOUNT_POINTS}:    {', '.join(self.mount_points)}\n"
                f"  {SNAPSET_DEVICES}:        {', '.join(self.devices)}\n"
            )

        snapset_str += (
            f"{SNAPSET_NR_SNAPSHOTS}:      {self.nr_snapshots}\n"
            f"{SNAPSET_TIME}:             {datetime.fromtimestamp(self.timestamp)}\n"
            f"{SNAPSET_UUID}:             {self.uuid}\n"
            f"{SNAPSET_STATUS}:           {str(self.status)}\n"
            f"{SNAPSET_AUTOACTIVATE}:     {bool_to_yes_no(self.autoactivate)}\n"
            f"{SNAPSET_ORIGIN_MOUNTED}:    {bool_to_yes_no(self.origin_mounted)}\n"
            f"{SNAPSET_MOUNTED}:          {bool_to_yes_no(self.snapshot_mounted)}\n"
        )

        if self.snapshot_mounted:
            snapset_str += f"  {SNAPSET_MOUNT_ROOT}:      {self.mount_root}\n"

        snapset_str += f"{SNAPSET_BOOTABLE}:         {bool_to_yes_no(self.boot_entry)}"
        if self.boot_entry or self.revert_entry:
            snapset_str += f"\n{SNAPSET_BOOT_ENTRIES}:"
        if self.boot_entry:
            snapset_str += (
                f"\n  {SNAPSET_SNAPSHOT_ENTRY}:  {self.boot_entry.disp_boot_id}"
            )
        if self.revert_entry:
            snapset_str += (
                f"\n  {SNAPSET_REVERT_ENTRY}:    {self.revert_entry.disp_boot_id}"
            )
        return snapset_str

    def to_dict(self, members=False):
        """
        Return a representation of this ``SnapshotSet`` as a dictionary.
        """
        pmap = {}
        pmap[SNAPSET_NAME] = self.name
        pmap[SNAPSET_SOURCES] = self.sources
        pmap[SNAPSET_MOUNT_POINTS] = self.mount_points
        pmap[SNAPSET_DEVICES] = self.devices
        pmap[SNAPSET_NR_SNAPSHOTS] = self.nr_snapshots
        pmap[SNAPSET_TIMESTAMP] = self.timestamp
        pmap[SNAPSET_TIME] = self.time
        pmap[SNAPSET_UUID] = str(self.uuid)
        pmap[SNAPSET_STATUS] = str(self.status)
        pmap[SNAPSET_AUTOACTIVATE] = self.autoactivate
        pmap[SNAPSET_ORIGIN_MOUNTED] = self.origin_mounted
        pmap[SNAPSET_MOUNTED] = self.snapshot_mounted
        if pmap[SNAPSET_MOUNTED]:
            pmap[SNAPSET_MOUNT_ROOT] = self.mount_root
        pmap[SNAPSET_BOOTABLE] = self.boot_entry is not None

        if self.boot_entry or self.revert_entry:
            pmap[SNAPSET_BOOT_ENTRIES] = {}
        if self.boot_entry:
            pmap[SNAPSET_BOOT_ENTRIES][
                SNAPSET_SNAPSHOT_ENTRY
            ] = self.boot_entry.disp_boot_id
        if self.revert_entry:
            pmap[SNAPSET_BOOT_ENTRIES][
                SNAPSET_REVERT_ENTRY
            ] = self.revert_entry.disp_boot_id

        if members:
            pmap[SNAPSET_SNAPSHOTS] = []
            for snapshot in self.snapshots:
                pmap[SNAPSET_SNAPSHOTS].append(snapshot.to_dict())

        return pmap

    #: Backwards compatibility
    as_dict = to_dict

    def json(self, members=False, pretty=False):
        """
        Return a string representation of this ``SnapshotSet`` in JSON notation.
        """
        return json.dumps(self.to_dict(members=members), indent=4 if pretty else None)

    @property
    def name(self):
        """
        The name of this snapshot set.
        """
        return self._name

    @property
    def basename(self):
        """
        The basename of this snapshot set, minus any index.
        """
        return self._basename

    @property
    def index(self):
        """
        The index of this snapshot set, if set, or the special value
        ``snapm.SNAPSET_INDEX_NONE`` otherwise.
        """
        return self._index

    @property
    def uuid(self):
        """
        The UUID of this snapshot set.
        """
        return self._uuid

    @property
    def timestamp(self):
        """
        The numerical timestamp of this snapshot set.
        """
        return self._timestamp

    @property
    def datetime(self):
        """
        The timestamp of this snapshot set as a ``datetime.datetime`` object.
        """
        return datetime.fromtimestamp(self.timestamp)

    @property
    def time(self):
        """
        The human readable timestamp of this snapshot set.
        """
        return str(self.datetime)

    @property
    def snapshots(self):
        """
        The list of snapshots in this snapshot set.
        """
        return self._snapshots

    @property
    def nr_snapshots(self):
        """
        The number of snapshots in this snapshot set.
        """
        return len(self._snapshots)

    @property
    def sources(self):
        """
        The list of source mount points and block devices in this snapshot set.
        """
        return [s.source for s in self.snapshots]

    @property
    def mount_points(self):
        """
        The list of mount points in this snapshot set.
        """
        return [s.mount_point for s in self.snapshots if s.mount_point]

    @property
    def devices(self):
        """
        The list of block devices in this snapshot set.
        """
        return [s.source for s in self.snapshots if s.source not in self.mount_points]

    @property
    def status(self):
        """
        The overall status of this snapshot set. Returns ``SnapStatus.ACTIVE``
        if all members of the set are valid and active, ``SnapStatus.INACTIVE``
        if any members are inactive, or ``SnapStatus.INVALID`` if any member
        of the set is invalid.
        """
        if any(s.status == SnapStatus.INVALID for s in self.snapshots):
            return SnapStatus.INVALID
        if any(s.status == SnapStatus.REVERTING for s in self.snapshots):
            return SnapStatus.REVERTING
        if any(s.status == SnapStatus.INACTIVE for s in self.snapshots):
            return SnapStatus.INACTIVE
        return SnapStatus.ACTIVE

    @property
    def autoactivate(self):
        """
        The overall autoactivation status of this snapshot set. Returns ``True``
        if all snapshots within the set have autoactivate enabled or ``False``
        otherwise.
        """
        return all(s.autoactivate for s in self.snapshots)

    @autoactivate.setter
    def autoactivate(self, value):
        """
        Set the autoactivation status for all snapshots in this snapshot set.
        """
        for snapshot in self.snapshots:
            try:
                snapshot.autoactivate = value
            except SnapmError as err:  # pragma: no cover
                _log_error(
                    "Failed to set autoactivation for snapshot set member '%s': %s",
                    snapshot.name,
                    err,
                )

    @property
    def origin_mounted(self):
        """
        Test whether the origin volumes for this ``SnapshotSet`` are currently
        mounted and in use.

        :returns: ``True`` if any of the snapshots belonging to this
                  ``SnapshotSet`` are currently mounted, or ``False``
                  otherwise.
        """
        return any(s.origin_mounted for s in self.snapshots)

    @property
    def snapshot_mounted(self):
        """
        Test whether the snapshot volumes for this ``SnapshotSet`` are currently
        mounted and in use.

        :returns: ``True`` if any of the snapshots belonging to this
                  ``SnapshotSet`` are currently mounted, or ``False``
                  otherwise.
        """
        return any(s.snapshot_mounted for s in self.snapshots)

    @property
    def mounted(self):
        """
        Test whether the either the origin or snapshot volumes for this
        ``SnapshotSet`` are currently mounted and in use.

        :returns: ``True`` if any of the snapshots belonging to this
                  ``SnapshotSet`` are currently mounted, or ``False``
                  otherwise.
        """
        return self.snapshot_mounted or self.origin_mounted

    def snapshot_by_mount_point(self, mount_point):
        """
        Return the snapshot corresponding to ``mount_point``.

        :param mount_point: The mount point path to search for.
        :returns: A ``Snapshot`` object for the given mount point.
        :raises: ``SnapmNotFoundError`` if the specified mount point
                 is not present in this ``SnapshotSet``.
        """
        if mount_point in self._by_mount_point:
            return self._by_mount_point[mount_point]
        raise SnapmNotFoundError(
            f"Mount point {mount_point} not found in snapset {self.name}"
        )

    def snapshot_by_source(self, source):
        """
        Return the snapshot corresponding to ``source``.

        :param source: The block device or mount point path to search for.
        :returns: A ``Snapshot`` object for the given source path.
        :raises: ``SnapmNotFoundError`` if the specified source path
                 is not present in this ``SnapshotSet``.
        """
        if source in self._by_mount_point:
            return self._by_mount_point[source]
        if source in self._by_origin:
            return self._by_origin[source]
        raise SnapmNotFoundError(
            f"Source path {source} not found in snapset {self.name}"
        )

    def rename(self, new_name):
        """
        Rename this ``SnapshotSet`` to ``new_name``.

        :raises: ``SnapmError`` if a call to rename any member of the snapshot
                 set fails.
        """
        _log_info(
            "Attempting rename for snapshot set '%s' to '%s'", self.name, new_name
        )
        old_name = self.name
        snapshots = self.snapshots
        new_snapshots = []

        for snapshot in snapshots.copy():
            snapshots.remove(snapshot)
            try:
                _log_debug("Renaming snapshot set member '%s'", snapshot.name)
                new_snapshot = snapshot.rename(new_name)
                new_snapshots.append(new_snapshot)
            except SnapmError as err:
                _log_error("Failed to rename snapshot %s: %s", snapshot.name, err)
                rollback_snapshots = []
                for new_snapshot in new_snapshots:
                    try:
                        rollback_snapshot = snapshot.rename(old_name)
                        rollback_snapshots.append(rollback_snapshot)
                    except SnapmError as err2:
                        _log_error(
                            "Failed to rollback snapshot rename on %s: %s",
                            snapshot.name,
                            err2,
                        )
                self._snapshots = snapshots + rollback_snapshots
                raise SnapmPluginError(
                    f"Could not rename all snapshots for set {old_name}"
                ) from err

        self._name = new_name
        self._uuid = uuid5(NAMESPACE_SNAPSHOT_SET, self.name + str(self.timestamp))
        self._snapshots = new_snapshots
        self._link_snapshots()

    def delete(self):
        """
        Delete the on-disk representation of this ``SnapshotSet``.

        :raises: ``SnapmBusyError`` if the snapshot set is in use.
                 ``SnapmPluginError`` if an error occurs deleting snapshot
                 set members.
        """
        _log_info("Attempting delete for snapshot set '%s'", self.name)
        if any(snapshot.snapshot_mounted for snapshot in self.snapshots):
            raise SnapmBusyError(
                f"Snapshots from snapshot set {self.name} are mounted: cannot delete"
            )
        if self.status == SnapStatus.REVERTING:
            _log_error("Cannot operate on reverting snapshot set '%s'", self.name)
            raise SnapmBusyError(f"Failed to delete snapshot set {self.name}")
        for snapshot in self.snapshots.copy():
            _log_debug("Deleting snapshot set member '%s'", snapshot.name)
            try:
                snapshot.delete()
                self.snapshots.remove(snapshot)
                if snapshot.mount_point:
                    self._by_mount_point.pop(snapshot.mount_point)
                self._by_origin.pop(snapshot.origin)
            except SnapmError as err:
                _log_error(
                    "Failed to delete snapshot set member '%s': %s",
                    snapshot.name,
                    err,
                )
                raise SnapmPluginError(
                    f"Could not delete all snapshots for set {self.name}"
                ) from err

    def resize(self, sources, size_policies):
        """
        Attempt to resize the ``SnapshotSet`` according to the list of
        ``sources`` and corresponding ``size_policy`` strings.

        :raises: ``SnapmNoSpaceError`` if there is insufficient space available
                 for the requested operation.
        """
        _log_info("Attempting resize for snapshot set '%s'", self.name)
        for source in sources:
            try:
                _ = self.snapshot_by_source(source)
            except SnapmNotFoundError as err:
                _log_error(
                    "Cannot resize %s: source path not a member of snapset %s",
                    source,
                    self.name,
                )
                raise err

        providers = set(snapshot.provider for snapshot in self.snapshots)
        for provider in providers:
            provider.start_transaction()

        for source in sources:
            snapshot = self.snapshot_by_source(source)
            size_policy = size_policies[source]
            try:
                snapshot.check_resize(size_policy)
            except SnapmNoSpaceError as err:
                _log_error("Cannot resize %s snapshot: %s", snapshot.name, err)
                raise SnapmNoSpaceError(
                    f"Insufficient free space to resize snapshot set {self.name}"
                ) from err

        for provider in providers:
            _log_debug("%s transaction size map: %s", provider.name, provider.size_map)

        for source in sources:
            snapshot = self.snapshot_by_source(source)
            size_policy = size_policies[source]
            _log_debug("Resizing snapshot set member '%s'", snapshot.name)
            try:
                snapshot.resize(size_policy)
            except SnapmNoSpaceError as err:  # pragma: no cover
                _log_error("Cannot resize %s snapshot: %s", snapshot.name, err)
                raise SnapmNoSpaceError(
                    f"Insufficient free space to resize snapshot set {self.name}"
                ) from err

        for provider in providers:
            provider.end_transaction()

    def revert(self):
        """
        Initiate a revert operation on this ``SnapshotSet``.

        :raises: ``SnapmPluginError`` if a plugin fails to perform the
                 requested operation.
        """
        _log_info("Attempting revert for snapshot set '%s'", self.name)
        revert_entry = self.revert_entry
        mounted = self.mounted
        name = self.name

        # Perform revert operation on all snapshots
        for snapshot in self.snapshots:
            try:
                _log_debug("Reverting snapshot set member '%s'", snapshot.name)
                snapshot.revert()
            except SnapmError as err:  # pragma: no cover
                _log_error(
                    "Failed to revert snapshot set member '%s': %s",
                    snapshot.name,
                    err,
                )
                raise SnapmPluginError(
                    f"Could not revert all snapshots for set {self.name}"
                ) from err
        if mounted:
            _log_warn(
                "Snapshot set %s is in use: reboot required to complete revert",
                name,
            )
            if revert_entry:
                _log_warn(
                    "Boot into '%s' to continue",
                    revert_entry.title,
                )

    def activate(self):
        """
        Attempt to activate all members of this ``SnapshotSet``.

        :raises: ``SnapmPluginError`` if a plugin fails to perform the
                 requested operation.
        """
        _log_info("Attempting to activate snapshot set '%s'", self.name)
        for snapshot in self.snapshots:
            try:
                _log_debug("Activating snapshot set member '%s'", snapshot.name)
                snapshot.activate()
            except SnapmError as err:  # pragma: no cover
                _log_error(
                    "Failed to activate snapshot set member '%s': %s",
                    snapshot.name,
                    err,
                )
                raise SnapmPluginError(
                    f"Could not activate all snapshots for set {self.name}"
                ) from err

    def deactivate(self):
        """
        Attempt to deactivate all members of this ``SnapshotSet``.

        :raises: ``SnapmPluginError`` if a plugin fails to perform the
                 requested operation.
        """
        _log_info("Attempting to deactivate snapshot set '%s'", self.name)
        for snapshot in self.snapshots:
            try:
                _log_debug("Deactivating snapshot set member '%s'", snapshot.name)
                snapshot.deactivate()
            except SnapmError as err:  # pragma: no cover
                _log_error(
                    "Failed to deactivate snapshot set member '%s': %s",
                    snapshot.name,
                    err,
                )
                raise SnapmPluginError(
                    f"Could not deactivate all snapshots for set {self.name}"
                ) from err


# pylint: disable=too-many-instance-attributes,too-many-public-methods
class Snapshot:
    """
    Base class for individual snapshots. Each snapshot plugin should
    subclass ``Snapshot`` to provide a specific implementation.
    """

    # pylint: disable=too-many-arguments
    def __init__(self, name, snapset_name, origin, timestamp, mount_point, provider):
        """
        Initialise a new ``Snapshot`` object.

        :param name: The name of the snapshot.
        :param snapset_name: The name of the ``SnapshotSet`` this snapshot is a part of.
        :param origin: The origin volume of the snapshot.
        :param timestamp: The creation timestamp of the snapshot set.
        :param mount_point: The mount point path this snapshot refers to.
        :param provider: The plugin providing this snapshot.
        """
        self._name = name
        self._uuid = uuid5(NAMESPACE_SNAPSHOT, name)
        self._snapset_name = snapset_name
        self._origin = origin
        self._timestamp = timestamp
        self._mount_point = mount_point
        self._snapshot_set = None

        if _has_index(snapset_name):
            self._basename = _parse_basename(snapset_name)
            self._index = _parse_index(snapset_name)
        else:
            self._basename = snapset_name
            self._index = SNAPSHOT_INDEX_NONE

        self.provider = provider

    def __str__(self):
        """
        Return a string representation of this ``Snapshot`` object.

        :returns: A multi-line string describing this snapshot.
        """
        return (
            f"{SNAPSHOT_NAME}:           {self.name}\n"
            f"{SNAPSET_NAME}:    {self.snapset_name}\n"
            f"{SNAPSHOT_ORIGIN}:         {self.origin}\n"
            f"{SNAPSET_TIME}:           {datetime.fromtimestamp(self.timestamp)}\n"
            f"{SNAPSHOT_SOURCE}:         {self.source}\n"
            f"{SNAPSHOT_MOUNT_POINT}:     {self.mount_point}\n"
            f"{SNAPSHOT_PROVIDER}:       {self.provider.name}\n"
            f"{SNAPSHOT_UUID}:           {self.uuid}\n"
            f"{SNAPSHOT_STATUS}:         {str(self.status)}\n"
            f"{SNAPSHOT_SIZE}:           {size_fmt(self.size)}\n"
            f"{SNAPSHOT_FREE}:           {size_fmt(self.free)}\n"
            f"{SNAPSHOT_AUTOACTIVATE}:   {'yes' if self.autoactivate else 'no'}\n"
            f"{SNAPSHOT_DEV_PATH}:     {self.devpath}"
        )

    def to_dict(self):
        """
        Return a representation of this ``Snapshot`` as a dictionary.
        """
        pmap = {}
        pmap[SNAPSHOT_NAME] = self.name
        pmap[SNAPSET_NAME] = self.snapset_name
        pmap[SNAPSHOT_ORIGIN] = self.origin
        pmap[SNAPSET_TIMESTAMP] = self.timestamp
        pmap[SNAPSET_TIME] = self.time
        pmap[SNAPSHOT_SOURCE] = self.source
        pmap[SNAPSHOT_MOUNT_POINT] = self.mount_point
        pmap[SNAPSHOT_PROVIDER] = self.provider.name
        pmap[SNAPSHOT_UUID] = str(self.uuid)
        pmap[SNAPSHOT_STATUS] = str(self.status)
        pmap[SNAPSHOT_SIZE] = size_fmt(self.size)
        pmap[SNAPSHOT_FREE] = size_fmt(self.free)
        pmap[SNAPSHOT_SIZE_BYTES] = self.size
        pmap[SNAPSHOT_FREE_BYTES] = self.free
        pmap[SNAPSHOT_AUTOACTIVATE] = self.autoactivate
        pmap[SNAPSHOT_DEV_PATH] = self.devpath
        return pmap

    # Backwards compatibility
    as_dict = to_dict

    def json(self, pretty=False):
        """
        Return a string representation of this ``Snapshot`` in JSON notation.
        """
        return json.dumps(self.to_dict(), indent=4 if pretty else None)

    @property
    def name(self):
        """
        The name of this snapshot.
        """
        return self._name

    @property
    def uuid(self):
        """
        The UUID of this snapshot.
        """
        return self._uuid

    @property
    def snapset_name(self):
        """
        The name of the snapshot set this snapshot belongs to.
        """
        return self._snapset_name

    @property
    def snapset_basename(self):
        """
        The basename of the snapshot set this snapshot belongs to,
        minus any index.
        """
        return self._basename

    @property
    def index(self):
        """
        The index of this snapshot, if set, or the special value
        ``snapm.SNAPSHOT_INDEX_NONE`` otherwise.
        """
        return self._index

    @property
    def origin(self):
        """
        The origin of this snapshot.
        """
        raise NotImplementedError  # pragma: no cover

    @property
    def origin_options(self):
        """
        File system options needed to specify the origin of this snapshot.
        """
        raise NotImplementedError  # pragma: no cover

    @property
    def timestamp(self):
        """
        The numerical timestamp of this snapshot.
        """
        return self._timestamp

    @property
    def time(self):
        """
        The human readable timestamp of this snapshot.
        """
        return str(datetime.fromtimestamp(self.timestamp))

    @property
    def mount_point(self):
        """
        The mount point of this snapshot.
        """
        return self._mount_point

    @property
    def source(self):
        """
        The block device or mount point from which this ``Snapshot``
        was created.
        """
        return self.mount_point if self.mount_point else self.origin

    @property
    def snapshot_set(self):
        """
        The ``SnapshotSet`` this snapshot belongs to.
        """
        return self._snapshot_set

    @snapshot_set.setter
    def snapshot_set(self, value):
        """
        Set the ``SnapshotSet`` this snapshot belongs to.
        """
        self._snapshot_set = value

    @property
    def devpath(self):
        """
        The device path for this snapshot.
        """
        raise NotImplementedError  # pragma: no cover

    @property
    def status(self):
        """
        The status of this snapshot. Returns a ``SnapStatus`` enum
        value representing the current state of the snapshot.
        """
        raise NotImplementedError  # pragma: no cover

    @property
    def size(self):
        """
        The size of this snapshot in bytes. For snapshots with fixed-size
        backstores this reflects the size of the backstore. For snapshots
        that dynamically allocate space to the snapshot it reflects the
        device or volume size.
        """
        raise NotImplementedError  # pragma: no cover

    @property
    def free(self):
        """
        The space available to this snapshot in bytes. For snapshots with
        fixed-size backstores this reflects the space remaining in the
        current backstore. For snapshots that dynamically allocate space
        to the snapshot it indicates the pooled space available.
        """
        raise NotImplementedError  # pragma: no cover

    @property
    def autoactivate(self):
        """
        The autoactivation status of this snapshot. Returns ``True`` if the
        snapshot is automatically activated or ``False`` otherwise.
        """
        raise NotImplementedError  # pragma: no cover

    @autoactivate.setter
    def autoactivate(self, value):
        """
        Set the autoactivation state of this snapshot.

        :param value: ``True`` to enable autoactivation or ``False`` otherwise.
        """
        self.provider.set_autoactivate(self.name, auto=value)
        self.invalidate_cache()

    @property
    def origin_mounted(self):
        """
        Test whether the origin volume for this ``Snapshot`` is currently
        mounted and in use.

        :returns: ``True`` if this snapshot's origin is currently mounted
                  or ``False`` otherwise.
        """
        if not self.mount_point:
            return False
        with open("/proc/self/mounts", "r", encoding="utf8") as mounts:
            for line in mounts:
                fields = line.split()
                if self.mount_point == _unescape_mounts(fields[1]):
                    devpath = _unescape_mounts(fields[0])
                    if os.path.exists(self.origin) and os.path.exists(devpath):
                        return os.path.samefile(self.origin, devpath)
        return False

    @property
    def snapshot_mounted(self):
        """
        Test whether the snapshot volume for this ``Snapshot`` is currently
        mounted and in use.

        :returns: ``True`` if this snapshot is currently mounted or ``False``
                  otherwise.
        """
        if self.status != SnapStatus.ACTIVE:
            return False
        if not self.mount_point:
            return False
        with open("/proc/self/mounts", "r", encoding="utf8") as mounts:
            for line in mounts:
                fields = line.split()
                devpath = _unescape_mounts(fields[0])
                if os.path.exists(devpath) and os.path.exists(self.devpath):
                    if os.path.samefile(self.devpath, devpath):
                        return True
        return False

    @property
    def mounted(self):
        """
        Test whether either the snapshot or origin volume for this ``Snapshot``
        is currently mounted and in use.

        :returns: ``True`` if this snapshot or its origin is currently mounted
                  or ``False`` otherwise.
        """
        return self.snapshot_mounted or self.origin_mounted

    def delete(self):
        """
        Delete this snapshot.
        """
        self.provider.delete_snapshot(self.name)

    def rename(self, new_name):
        """
        Rename a snapshot within a snapshot set.

        :param new_name: The new name for the snapshot.
        """
        return self.provider.rename_snapshot(
            self.name, self.origin, new_name, self.timestamp, self.mount_point
        )

    def check_resize(self, size_policy):
        """
        Check whether this snapshot can be resized or not by calling the
        provider plugin with the updated ``size_policy``.
        """
        self.invalidate_cache()
        self.provider.check_resize_snapshot(
            self.name, self.origin, self.mount_point, size_policy
        )

    def resize(self, size_policy):
        """
        Attempt to resize this snapshot according to the updated
        ``size_policy``.
        """
        self.invalidate_cache()
        self.provider.resize_snapshot(
            self.name, self.origin, self.mount_point, size_policy
        )

    def check_revert(self):
        """
        Check whether this snapshot can be reverted or not by calling the
        provider plugin to verify the state of the snapshot.
        """
        self.provider.check_revert_snapshot(self.name, self.origin)

    def revert(self):
        """
        Request to revert a snapshot and revert the content of the origin
        volume to its state at the time of the snapshot.

        This may be deferred until the next device activation or mount
        operation for the respective volume.
        """
        self.invalidate_cache()
        self.provider.revert_snapshot(self.name)

    def invalidate_cache(self):
        """
        Invalidate any cached data describing this snapshot.
        """
        raise NotImplementedError  # pragma: no cover

    def activate(self):
        """
        Activate this snapshot.
        """
        self.provider.activate_snapshot(self.name)
        self.invalidate_cache()

    def deactivate(self):
        """
        Deactivate this snapshot.
        """
        self.provider.deactivate_snapshot(self.name)
        self.invalidate_cache()


# pylint: disable=too-many-return-statements
def select_snapshot_set(select, snapshot_set):
    """
    Test SnapshotSet against Selection criteria.

    Test the supplied ``SnapshotSet`` against the selection criteria
    in ``select`` and return ``True`` if it passes, or ``False``
    otherwise.

    :param select: The selection criteria
    :param snapshot_set: The SnapshotSet to test
    :rtype: bool
    :returns: True if SnapshotSet passes selection or ``False``
              otherwise.
    """
    if select.name and select.name != snapshot_set.name:
        return False
    if select.uuid and select.uuid != snapshot_set.uuid:
        return False
    if select.basename and select.basename != snapshot_set.basename:
        return False
    if select.index is not None and select.index != snapshot_set.index:
        return False
    if select.timestamp and select.timestamp != snapshot_set.timestamp:
        return False
    if (
        select.nr_snapshots is not None
        and select.nr_snapshots != snapshot_set.nr_snapshots
    ):
        return False
    if select.mount_points:
        s_mount_points = sorted(select.mount_points)
        mount_points = sorted(snapshot_set.mount_points)
        if s_mount_points != mount_points:
            return False

    return True


def select_snapshot(select, snapshot):
    """
    Test SnapshotSet against Selection criteria.

    Test the supplied ``Snapshot`` against the selection criteria
    in ``select`` and return ``True`` if it passes, or ``False``
    otherwise.

    :param select: The selection criteria
    :param snapshot: The Snapshot to test
    :rtype: bool
    :returns: True if Snapshot passes selection or ``False``
              otherwise.
    """
    if not select_snapshot_set(select, snapshot.snapshot_set):
        return False
    if select.snapshot_uuid and select.snapshot_uuid != snapshot.uuid:
        return False
    if select.snapshot_name and select.snapshot_name != snapshot.name:
        return False

    return True


class FsTabReader:
    """
    A class to read and query data from an fstab file.

    This class reads an fstab-like file and provides methods to iterate
    over its entries and look up specific entries based on their properties.

    """

    # Define a named tuple to give structure to each fstab entry.
    FsTabEntry = collections.namedtuple(
        "FsTabEntry", ["what", "where", "fstype", "options", "freq", "passno"]
    )

    def __init__(self, path=ETC_FSTAB):
        """
        Initializes the FsTabReader object by reading and parsing the file.

        :param path: The path to the fstab file. Defaults to '/etc/fstab'.
        :type path: str
        :raises SnapmNotFoundError: If the specified fstab file does not exist.
        :raises SnapmSystemError: If there is an error reading the fstab file.

        """
        self.path = path
        self.entries = []
        self._read_fstab()

    def _read_fstab(self):
        """
        Private method to read and parse the fstab file.
        It populates the self.entries list.
        """
        try:
            with open(self.path, "r", encoding="utf8") as f:
                for line in f:
                    # 1. Remove end-of-line comments and trim; ignore empties.
                    line = line.split("#", 1)[0].strip()
                    if not line:
                        continue

                    # 2. Split the line into parts.
                    parts = line.split()

                    # 3. An fstab entry must have 6 fields.
                    if len(parts) != 6:
                        _log_warn("Skipping malformed %s entry: %s", self.path, line)
                        continue

                    # 4. Create a named tuple and append it to our list.
                    what, where, fstype, options, freq, passno = parts
                    try:
                        entry = self.FsTabEntry(
                            _unescape_mounts(what),
                            _unescape_mounts(where),
                            fstype,
                            _unescape_mounts(options),
                            int(freq),
                            int(passno),
                        )
                    except ValueError:
                        # Keep as strings if non-numeric (defensive)
                        entry = self.FsTabEntry(*parts)
                    self.entries.append(entry)

        except FileNotFoundError as exc:
            _log_error("Error: The file '%s' was not found.", self.path)
            raise SnapmNotFoundError(f"FsTab file not found: {self.path}") from exc
        except IOError as e:
            _log_error("Error: Could not read the file '%s': %s", self.path, e)
            raise SnapmSystemError(f"Error reading fstab file: {self.path}") from e

    def __iter__(self):
        """
        Allows iteration over the fstab entries.

        :yields:
            A 6-tuple for each entry in the fstab file:
            (what, where, fstype, options, freq, passno)
        """
        yield from self.entries

    def lookup(self, key, value):
        """
        Finds and generates all entries matching a specific key-value pair.

        :param key: The field to search by. Must be one of 'what', 'where',
                    'fstype', 'options', 'freq', or 'passno'.
        :type key: str
        :param value: The value to match for the given key.
        :type value: str|int
        :yields: A 6-tuple for each matching fstab entry.

        :raises KeyError: If the provided key is not a valid fstab field name.
        """
        if key not in self.FsTabEntry._fields:
            raise KeyError(
                f"Invalid lookup key: '{key}'. "
                f"Valid keys are: {self.FsTabEntry._fields}"
            )

        for entry in self.entries:
            # getattr() allows us to get an attribute by its string name.
            if getattr(entry, key) == value:
                yield entry

    def __repr__(self):
        """
        Return a machine readable string representation of this FsTabReader.
        """
        return f"FsTabReader(path='{self.path}')"


def get_device_path(by_type: str, identifier: str) -> Optional[str]:
    """
    Translates a filesystem UUID or label to its corresponding device path
    using the blkid command.

    :param by_type: The type of identifier to search for.
    :param identifier: The UUID or label of the filesystem.

    :returns: The device path if found, otherwise None.
    :rtype: Optional[str]

    :raises ValueError: If 'identifier' is empty or 'by_type' is not 'uuid' or 'label'.
    :raises SnapmNotFoundError: If the 'blkid' command is not found on the system.
    :raises SnapmSystemError: If the 'blkid' command exits with a non-zero status
                              due to reasons other than the identifier not being found
                              (e.g., permission issues).
    :raises SnapmCalloutError: For any other unexpected errors during command execution
                               or parsing.
    """
    if by_type not in ["uuid", "label"]:
        raise ValueError("Invalid 'by_type'. Must be 'uuid' or 'label'.")
    if not identifier:
        raise ValueError("Identifier cannot be an empty string.")

    env = dict(os.environ, LC_ALL="C", LANG="C")
    try:
        command = ["blkid"]

        if by_type == "uuid":
            command.extend(["--uuid", identifier])
        else:  # by_type == "label"
            command.extend(["--label", identifier])

        # Execute the command
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            env=env,
        )

        # The output of 'blkid --uuid <UUID>' or 'blkid --label <LABEL>' is simply the device path
        # if found. If not found, it returns a non-zero exit code, which
        # 'check=True' converts into a CalledProcessError.
        device_path = result.stdout.strip()

        if device_path:
            return device_path

        # This case should ideally not be reached if check=True is working as expected
        # for "not found" scenarios, but it's a safeguard.
        return None

    except FileNotFoundError as exc:
        _log_error(
            "Error: 'blkid' command not found. Please ensure it is installed and in your PATH."
        )
        raise SnapmNotFoundError("blkid command not found.") from exc
    except subprocess.CalledProcessError as e:
        # blkid returns 2 if the specified UUID/label is not found.
        if e.returncode == 2:
            # This is the expected behavior when the identifier is not found.
            _log_debug(
                "Identifier '%s' (%s) not found by blkid.",
                identifier,
                by_type,
            )
            return None

        # Other errors (e.g., permission denied) should still be raised.
        _log_error(
            "Error executing blkid command (return code %d): %s", e.returncode, e
        )
        _log_error("Stdout: %s", e.stdout.strip())
        _log_error("Stderr: %s", e.stderr.strip())
        raise SnapmSystemError(f"Error executing blkid command: {e}") from e
    except Exception as e:
        _log_error("An unexpected error occurred while executing blkid: %s", str(e))
        raise SnapmCalloutError(
            f"An unexpected error occurred while executing blkid: {e}"
        ) from e


def get_device_fstype(devpath: str) -> str:
    """
    Determine the file system type for the device at `devpath`.

    :param devpath: The path to the device.
    :returns: The file system type, 'xfs', 'ext4', 'swap', etc.
    :rtype: str

    :raises SnapmNotFoundError: If the device is not found.
    :raises SnapmSystemError: If the 'blkid' command exits with a non-zero status
                              due to reasons other than the identifier not being found
                              (e.g., permission issues).
    :raises SnapmCalloutError: For any other unexpected errors during command execution
                               or parsing.
    """
    if not devpath:
        raise ValueError("Device path cannot be an empty string.")
    if not os.path.exists(devpath):
        raise SnapmNotFoundError(f"Unknown device path: {devpath}")
    if not S_ISBLK(os.stat(devpath).st_mode):
        raise SnapmPathError(f"Path {devpath} is not a block device.")

    env = dict(os.environ, LC_ALL="C", LANG="C")
    try:
        command = ["blkid", "--match-tag=TYPE", "--output=value", devpath]

        # Execute the command
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            env=env,
        )

        # The output of 'blkid --match-tag=TYPE --output=value /path/to/dev' is simply
        # the value of the requested tag, e.g.:
        # # blkid --match-tag=TYPE --output=value /dev/fedora/root
        # xfs
        # if found. If not found, it returns a non-zero exit code, which
        # 'check=True' converts into a CalledProcessError.
        return result.stdout.strip()

    except FileNotFoundError as exc:
        _log_error(
            "Error: 'blkid' command not found. Please ensure it is installed and in your PATH."
        )
        raise SnapmNotFoundError("blkid command not found.") from exc
    except subprocess.CalledProcessError as e:
        # blkid returns 2 if the specified device path does not exist, is not a
        # block device, or if the specified tag is undefined.
        if e.returncode == 2:
            _log_debug("blkid: no TYPE for %s", devpath)
            return ""

        # Other errors (e.g., permission denied) should still be raised.
        _log_error(
            "Error executing blkid command (return code %d): %s", e.returncode, e
        )
        _log_error("Stdout: %s", e.stdout.strip())
        _log_error("Stderr: %s", e.stderr.strip())
        raise SnapmSystemError(f"Error executing blkid command: {e}") from e
    except Exception as e:
        _log_error("An unexpected error occurred while executing blkid: %s", str(e))
        raise SnapmCalloutError(
            f"An unexpected error occurred while executing blkid: {e}"
        ) from e


def find_snapset_root(snapset, fstab: FsTabReader, origin: bool = False):
    """
    Find the device that backs the root filesystem for snapshot set ``snapset``.

    If the snapset does not include the root volume look the device up via the
    fstab.

    :param snapset: The ``SnapshotSet`` to check.
    :type snapset: SnapshotSet
    :param fstab: An ``FsTabReader`` instance to use.
    :type fstab: FsTabReader
    :param origin: Always return the origin device, even if a snapshot exists
                   for the root mount point.
    :type origin: bool
    :returns: The device path for the root filesystem.
    :rtype: str
    :raises SnapmNotFoundError: If no root device can be found in the snapshot set
                                or fstab.
    """
    for snapshot in snapset.snapshots:
        if snapshot.mount_point == "/":
            if origin:
                return snapshot.origin
            return snapshot.devpath
    dev_path = None

    for entry in fstab.lookup("where", "/"):
        if entry.what.startswith("UUID="):
            dev_path = get_device_path("uuid", entry.what.split("=", maxsplit=1)[1])
            break
        if entry.what.startswith("LABEL="):
            dev_path = get_device_path("label", entry.what.split("=", maxsplit=1)[1])
            break
        if entry.what.startswith("PARTUUID="):
            ident = entry.what.split("=", maxsplit=1)[1]
            cand = f"/dev/disk/by-partuuid/{ident}"
            dev_path = cand if os.path.exists(cand) else None
            break
        if entry.what.startswith("PARTLABEL="):
            ident = entry.what.split("=", maxsplit=1)[1]
            cand = f"/dev/disk/by-partlabel/{ident}"
            dev_path = cand if os.path.exists(cand) else None
            break
        if entry.what.startswith("/"):
            dev_path = entry.what
            break

    if dev_path:
        return dev_path

    raise SnapmNotFoundError(f"Could not find root device for snapset {snapset.name}")


def build_snapset_mount_list(snapset: SnapshotSet, fstab: FsTabReader):
    """
    Build a list of command line mount unit definitions for the snapshot set
    ``snapset``. Mount points that are not part of the snapset are substituted
    from /etc/fstab.

    :param snapset: The snapshot set to build a mount list for.
    :type snapset: SnapshotSet
    :param fstab: An ``FsTabReader`` instance to use.
    :type fstab: FsTabReader
    :returns: A list of 4-tuples, each containing (device_path, mount_point,
              filesystem_type, mount_options) for auxiliary mounts. The root
              filesystem ("/") and swap entries are excluded from the list.
    :rtype: List[Tuple[str, str, str, str]]
    """
    mounts = []
    snapset_mounts = snapset.mount_points

    for entry in fstab:
        what, where, fstype, options, _, _ = entry
        if where == "/" or fstype == "swap":
            continue
        if where in snapset_mounts:
            snapshot = snapset.snapshot_by_mount_point(where)
            mounts.append((snapshot.devpath, where, fstype, options))
        else:
            mounts.append((what, where, fstype, options))
    return mounts


__all__ = [
    "ETC_FSTAB",
    "SNAPSET_NAME",
    "SNAPSET_BASENAME",
    "SNAPSET_INDEX",
    "SNAPSET_SOURCES",
    "SNAPSET_MOUNT_POINTS",
    "SNAPSET_DEVICES",
    "SNAPSET_NR_SNAPSHOTS",
    "SNAPSET_TIME",
    "SNAPSET_TIMESTAMP",
    "SNAPSET_UUID",
    "SNAPSET_STATUS",
    "SNAPSET_AUTOACTIVATE",
    "SNAPSET_MOUNTED",
    "SNAPSET_ORIGIN_MOUNTED",
    "SNAPSET_MOUNT_ROOT",
    "SNAPSET_BOOTABLE",
    "SNAPSET_BOOT_ENTRIES",
    "SNAPSET_SNAPSHOT_ENTRY",
    "SNAPSET_REVERT_ENTRY",
    "SNAPSET_SNAPSHOTS",
    "SNAPSHOT_NAME",
    "SNAPSHOT_BASENAME",
    "SNAPSHOT_INDEX",
    "SNAPSHOT_ORIGIN",
    "SNAPSHOT_SOURCE",
    "SNAPSHOT_MOUNT_POINT",
    "SNAPSHOT_PROVIDER",
    "SNAPSHOT_UUID",
    "SNAPSHOT_STATUS",
    "SNAPSHOT_SIZE",
    "SNAPSHOT_FREE",
    "SNAPSHOT_SIZE_BYTES",
    "SNAPSHOT_FREE_BYTES",
    "SNAPSHOT_AUTOACTIVATE",
    "SNAPSHOT_DEV_PATH",
    "SNAPSET_INDEX_NONE",
    "SNAPSHOT_INDEX_NONE",
    "SNAPM_VALID_NAME_CHARS",
    "SNAPM_DEBUG_MANAGER",
    "SNAPM_DEBUG_COMMAND",
    "SNAPM_DEBUG_REPORT",
    "SNAPM_DEBUG_MOUNTS",
    "SNAPM_DEBUG_ALL",
    # Path to runtime directory
    "SNAPM_RUNTIME_DIR",
    # Debug logging - subsystem name interface
    "SubsystemFilter",
    "SNAPM_SUBSYSTEM_MANAGER",
    "SNAPM_SUBSYSTEM_COMMAND",
    "SNAPM_SUBSYSTEM_REPORT",
    "SNAPM_SUBSYSTEM_MOUNTS",
    # Debug logging - legacy interface
    "set_debug_mask",
    "get_debug_mask",
    "SnapmError",
    "SnapmSystemError",
    "SnapmCalloutError",
    "SnapmNoSpaceError",
    "SnapmNoProviderError",
    "SnapmSizePolicyError",
    "SnapmExistsError",
    "SnapmBusyError",
    "SnapmPathError",
    "SnapmNotFoundError",
    "SnapmInvalidIdentifierError",
    "SnapmParseError",
    "SnapmPluginError",
    "SnapmStateError",
    "SnapmRecursionError",
    "SnapmArgumentError",
    "SnapmTimerError",
    "SnapmLimitError",
    "SnapmMountError",
    "SnapmUmountError",
    "Selection",
    "size_fmt",
    "is_size_policy",
    "parse_size_with_units",
    "bool_to_yes_no",
    "SizePolicyType",
    "SizePolicy",
    "SnapStatus",
    "SnapshotSet",
    "Snapshot",
    "select_snapshot_set",
    "select_snapshot",
    "FsTabReader",
    "get_device_path",
    "get_device_fstype",
    "find_snapset_root",
    "build_snapset_mount_list",
]
