# Copyright Red Hat
#
# snapm/_snapm.py - Snapshot Manager global definitions
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: GPL-2.0-only
"""
Global definitions for the top-level snapm package.
"""
from uuid import UUID, uuid5
from datetime import datetime
from enum import Enum
import json
import math
import logging
import re
import os

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error

# Snapm debugging mask
SNAPM_DEBUG_MANAGER = 1
SNAPM_DEBUG_COMMAND = 2
SNAPM_DEBUG_REPORT = 4
SNAPM_DEBUG_ALL = SNAPM_DEBUG_MANAGER | SNAPM_DEBUG_COMMAND | SNAPM_DEBUG_REPORT

__DEBUG_MASK = 0


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
SNAPSET_SOURCES = "Sources"
SNAPSET_MOUNT_POINTS = "MountPoints"
SNAPSET_DEVICES = "Devices"
SNAPSET_NR_SNAPSHOTS = "NrSnapshots"
SNAPSET_TIME = "Time"
SNAPSET_TIMESTAMP = "Timestamp"
SNAPSET_UUID = "UUID"
SNAPSET_STATUS = "Status"
SNAPSET_AUTOACTIVATE = "Autoactivate"
SNAPSET_BOOTABLE = "Bootable"
SNAPSET_BOOT_ENTRIES = "BootEntries"
SNAPSET_SNAPSHOT_ENTRY = "SnapshotEntry"
SNAPSET_REVERT_ENTRY = "RevertEntry"
SNAPSET_SNAPSHOTS = "Snapshots"

# Constants for Snapshot property names
SNAPSHOT_NAME = "Name"
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


class SnapmLogger(logging.Logger):
    """
    Snapm logging wrapper class: wrap the Logger.debug() method
    to allow filtering of submodule debug messages by log mask.
    """

    mask_bits = 0

    def set_debug_mask(self, mask_bits):
        """
        Set the debug mask for this ``SnapmLogger``.

        This should normally be set to the ``SNAPM_DEBUG_*`` value
        corresponding to the ``snapm`` sub-module that this instance
        of ``SnapmLogger`` belongs to.

        :param mask_bits: The bits to set in this logger's mask.
        :rtype: None
        """
        if mask_bits < 0 or mask_bits > SNAPM_DEBUG_ALL:
            raise ValueError(
                f"Invalid SnapmLogger mask bits: 0x{(mask_bits & ~SNAPM_DEBUG_ALL):x}"
            )

        self.mask_bits = mask_bits

    def debug_masked(self, msg, *args, **kwargs):
        """
        Log a debug message if it passes the current debug mask.

        Log the specified message if it passes the current logger
        debug mask.

        :param msg: the message to be logged
        :rtype: None
        """
        if self.mask_bits & get_debug_mask():
            self.debug(msg, *args, **kwargs)


logging.setLoggerClass(SnapmLogger)


def get_debug_mask():
    """
    Return the current debug mask for the ``snapm`` package.

    :returns: The current debug mask value
    :rtype: int
    """
    return __DEBUG_MASK


def set_debug_mask(mask):
    """
    Set the debug mask for the ``snapm`` package.

    :param mask: the logical OR of the ``SNAPM_DEBUG_*``
                 values to log.
    :rtype: None
    """
    # pylint: disable=global-statement
    global __DEBUG_MASK
    if mask < 0 or mask > SNAPM_DEBUG_ALL:
        raise ValueError(f"Invalid snapm debug mask: {mask}")
    __DEBUG_MASK = mask


#
# Snapm exception types
#


class SnapmError(Exception):
    """
    Base class for snapshot manager errors.
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
    A resouce needed by the current command is already in use: for e.g.
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
    timestamp = None
    nr_snapshots = None
    mount_points = None

    # Snapshot fields
    origin = None
    mount_point = None
    snapshot_name = None
    snapshot_uuid = None

    snapshot_set_attrs = [
        "name",
        "uuid",
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

    all_attrs = snapshot_set_attrs + snapshot_attrs

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
        for attr in set(attrs):
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

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        name=None,
        uuid=None,
        timestamp=None,
        nr_snapshots=None,
        mount_points=None,
        origin=None,
        mount_point=None,
        snapshot_name=None,
        snapshot_uuid=None,
    ):
        self.name = name
        self.uuid = uuid
        self.timestamp = timestamp
        self.nr_snapshots = nr_snapshots
        self.mount_points = mount_points
        self.origin = origin
        self.mount_point = mount_point
        self.snapshot_name = snapshot_name
        self.snapshot_uuid = snapshot_uuid

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
        if cmd_args.identifier:
            try:
                uuid = UUID(cmd_args.identifier)
            except (TypeError, ValueError):
                name = cmd_args.identifier
        else:
            if cmd_args.name:
                name = cmd_args.name
            elif cmd_args.uuid:
                uuid = cmd_args.uuid

        if hasattr(cmd_args, "snapshot_name"):
            snapshot_name = cmd_args.snapshot_name
        if hasattr(cmd_args, "snapshot_uuid"):
            snapshot_uuid = cmd_args.snapshot_uuid

        select = Selection(
            name=name,
            uuid=uuid,
            snapshot_name=snapshot_name,
            snapshot_uuid=snapshot_uuid,
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

    def check_valid_selection(self, snapshot_set=False, snapshot=False):
        """
        Check a Selection for valid criteria.

        Check this ``Selection`` object to ensure it contains only
        criteria that are valid for the specified object type(s).

        Returns ``None`` if the object passes the check, or raise
        ``ValueError`` if invalid criteria exist.

        :param snapshot_set ``Selection`` may include SnapshotSet data
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
        Test this Selection object for single item selction criteria.

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
    :raises: ``ValueError`` if the string could not  be parsed as a valid size
             vale.
    """
    match = _SIZE_RE.search(value)
    if match is None:
        raise SnapmSizePolicyError(f"Malformed size expression: {value}")
    (size, unit) = (match.group("size"), match.group("units").upper())
    size_bytes = int(size) * _SIZE_SUFFIXES[unit[0]]
    return size_bytes


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
        raise SnapmParseError(f"Invalid size policy type: {self.type}")


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
            f"{SNAPSET_AUTOACTIVATE}:     {'yes' if self.autoactivate else 'no'}\n"
            f"{SNAPSET_BOOTABLE}:         {'yes' if self.boot_entry is not None else 'no'}"
        )

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

    def as_dict(self, members=False):
        """
        Return a representation of this snapshot as a dictionary.
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
                pmap[SNAPSET_SNAPSHOTS].append(snapshot.as_dict())

        return pmap

    def json(self, members=False, pretty=False):
        """
        Return a string representation of this ``SnapshotSet`` in JSON notation.
        """
        return json.dumps(self.as_dict(members=members), indent=4 if pretty else None)

    @property
    def name(self):
        """
        The name of this snapshot set.
        """
        return self._name

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
    def time(self):
        """
        The human readable timestamp of this snapshot set.
        """
        return str(datetime.fromtimestamp(self.timestamp))

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
        The list of souce mount points and block devices in this snapshot set.
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
            except SnapmError as err:
                _log_error(
                    "Failed to set autoactivation for snapshot set member %s: %s",
                    snapshot.name,
                    err,
                )

    @property
    def origin_mounted(self):
        """
        Test whether the origin volumes for this ``SnapshotSet`` are currently
        mounted and in use.

        :returns: ``True`` if any of the snaphots belonging to this
                  ``SnapshotSet`` are currently mounted, or ``False``
                  otherwise.
        """
        return any(s.origin_mounted for s in self.snapshots)

    @property
    def snapshot_mounted(self):
        """
        Test whether the snapshot volumes for this ``SnapshotSet`` are currently
        mounted and in use.

        :returns: ``True`` if any of the snaphots belonging to this
                  ``SnapshotSet`` are currently mounted, or ``False``
                  otherwise.
        """
        return any(s.snapshot_mounted for s in self.snapshots)

    @property
    def mounted(self):
        """
        Test whether the either the origin or snapshot volumes for this
        ``SnapshotSet`` are currently mounted and in use.

        :returns: ``True`` if any of the snaphots belonging to this
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
        old_name = self.name
        snapshots = self.snapshots
        new_snapshots = []

        for snapshot in snapshots.copy():
            snapshots.remove(snapshot)
            try:
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
        if any(snapshot.snapshot_mounted for snapshot in self.snapshots):
            raise SnapmBusyError(
                f"Snapshots from snapshot set {self.name} are mounted: cannot delete"
            )
        if self.status == SnapStatus.REVERTING:
            _log_error("Cannot operate on reverting snapshot set '%s'", self.name)
            raise SnapmBusyError(f"Failed to delete snapshot set {self.name}")
        for snapshot in self.snapshots.copy():
            try:
                snapshot.delete()
                self.snapshots.remove(snapshot)
                if snapshot.mount_point:
                    self._by_mount_point.pop(snapshot.mount_point)
                self._by_origin.pop(snapshot.origin)
            except SnapmError as err:
                _log_error(
                    "Failed to delete snapshot set member %s: %s",
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

        for source in sources:
            snapshot = self.snapshot_by_source(source)
            size_policy = size_policies[source]
            try:
                snapshot.resize(size_policy)
            except SnapmNoSpaceError as err:
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
        revert_entry = self.revert_entry
        mounted = self.mounted
        name = self.name

        # Perform revert operation on all snapshots
        for snapshot in self.snapshots:
            try:
                snapshot.revert()
            except SnapmError as err:
                _log_error(
                    "Failed to revert snapshot set member %s: %s",
                    snapshot.name,
                    err,
                )
                raise SnapmPluginError(
                    f"Could not revert all snapshots for set {self.name}"
                ) from err
        if mounted:
            _log_warn(
                "Snaphot set %s is in use: reboot required to complete revert",
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
        for snapshot in self.snapshots:
            try:
                snapshot.activate()
            except SnapmError as err:
                _log_error(
                    "Failed to activate snapshot set member %s: %s",
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
        for snapshot in self.snapshots:
            try:
                snapshot.deactivate()
            except SnapmError as err:
                _log_error(
                    "Failed to deactivate snapshot set member %s: %s",
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

    def as_dict(self):
        """
        Return a representation of this snapshot as a dictionary.
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

    def json(self, pretty=False):
        """
        Return a string representation of this ``Snapshot`` in JSON notation.
        """
        return json.dumps(self.as_dict(), indent=4 if pretty else None)

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
    def origin(self):
        """
        The origin of this snapshot.
        """
        raise NotImplementedError

    @property
    def origin_options(self):
        """
        File system options needed to specify the origin of this snapshot.
        """
        raise NotImplementedError

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
        raise NotImplementedError

    @property
    def status(self):
        """
        The status of this snapshot. Returns a ``SnapStatus`` enum
        value representing the current state of the snapshot.
        """
        raise NotImplementedError

    @property
    def size(self):
        """
        The size of this snapshot in bytes. For snapshots with fixed-size
        backstores this reflects the size of the backstore. For snapshots
        that dynamically allocate space to the snapshot it reflects the
        device or volume size.
        """
        raise NotImplementedError

    @property
    def free(self):
        """
        The space available to this snapshot in bytes. For snapshots with
        fixed-size backstores this reflects the space remaining in the
        current backstore. For snapshots that dynamically allocate space
        to the snapshot it indicates the pooled space available.
        """
        raise NotImplementedError

    @property
    def autoactivate(self):
        """
        The autoactivation status of this snapshot. Returns ``True`` if the
        snapshot is automatically activated or ``False`` otherwise.
        """
        raise NotImplementedError

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

        :returns: ``True`` if this snaphot's prigin is currently mounted
                  or ``False`` otherwise.
        """
        with open("/proc/mounts", "r", encoding="utf8") as mounts:
            for line in mounts:
                fields = line.split(" ")
                if self.mount_point == fields[1]:
                    return os.path.samefile(self.origin, fields[0])
        return False

    @property
    def snapshot_mounted(self):
        """
        Test whether the snapshot volume for this ``Snapshot`` is currently
        mounted and in use.

        :returns: ``True`` if this snaphot is currently mounted or ``False``
                  otherwise.
        """
        if self.status != SnapStatus.ACTIVE:
            return False
        with open("/proc/mounts", "r", encoding="utf8") as mounts:
            for line in mounts:
                fields = line.split(" ")
                devpath = fields[0]
                if os.path.exists(devpath):
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
        raise NotImplementedError

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


__all__ = [
    "ETC_FSTAB",
    "SNAPSET_NAME",
    "SNAPSET_SOURCES",
    "SNAPSET_MOUNT_POINTS",
    "SNAPSET_DEVICES",
    "SNAPSET_NR_SNAPSHOTS",
    "SNAPSET_TIME",
    "SNAPSET_TIMESTAMP",
    "SNAPSET_UUID",
    "SNAPSET_STATUS",
    "SNAPSET_AUTOACTIVATE",
    "SNAPSET_BOOTABLE",
    "SNAPSET_BOOT_ENTRIES",
    "SNAPSET_SNAPSHOT_ENTRY",
    "SNAPSET_REVERT_ENTRY",
    "SNAPSET_SNAPSHOTS",
    "SNAPSHOT_NAME",
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
    "SNAPM_DEBUG_MANAGER",
    "SNAPM_DEBUG_COMMAND",
    "SNAPM_DEBUG_REPORT",
    "SNAPM_DEBUG_ALL",
    "SnapmLogger",
    "set_debug_mask",
    "get_debug_mask",
    "SnapmError",
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
    "Selection",
    "size_fmt",
    "is_size_policy",
    "parse_size_with_units",
    "SizePolicyType",
    "SizePolicy",
    "SnapStatus",
    "SnapshotSet",
    "Snapshot",
]
