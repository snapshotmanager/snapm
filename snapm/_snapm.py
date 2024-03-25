# Copyright (C) 2023 Red Hat, Inc., Bryn M. Reeves <bmr@redhat.com>
#
# snapm/_snapm.py - Snapshot Manager global definitions
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
"""
Global definitions for the top-level snapm package.
"""
from uuid import UUID, uuid5
from datetime import datetime
from enum import Enum
import logging

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error

# Snapm debugging mask
SNAPM_DEBUG_MANAGER = 1
SNAPM_DEBUG_COMMAND = 2
SNAPM_DEBUG_PLUGINS = 4
SNAPM_DEBUG_REPORT = 8
SNAPM_DEBUG_ALL = (
    SNAPM_DEBUG_MANAGER | SNAPM_DEBUG_COMMAND | SNAPM_DEBUG_PLUGINS | SNAPM_DEBUG_REPORT
)

__DEBUG_MASK = 0


NAMESPACE_SNAPSHOT_SET = UUID("{952f0e38-24a1-406d-adf6-0e9fb3c707d8}")
NAMESPACE_SNAPSHOT = UUID("{c17d07c7-1482-43b7-9b3c-12d490622d93}")

ETC_FSTAB = "/etc/fstab"


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


class SnapmExistsError(SnapmError):
    """
    The named snapshot set already exists.
    """


class SnapmPathError(SnapmError):
    """
    An invalid path was supplied, for example attempting to snapshot
    something that is not a mount point.
    """


class SnapmNotFoundError(SnapmError):
    """
    The requested object does not exist.
    """


class SnapmInvalidIdentifierError(SnapmError):
    """
    An invalid identifier was given.
    """


#
# Selection criteria class
#


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
                UUID(cmd_args.identifier)
                uuid = cmd_args.identifier
            except (TypeError, ValueError):
                name = cmd_args.identifier
        else:
            if cmd_args.name:
                name = cmd_args.name
            elif cmd_args.uuid:
                try:
                    UUID(cmd_args.uuid)
                    uuid = cmd_args.uuid
                except (TypeError, ValueError) as err:
                    raise SnapmInvalidIdentifierError(
                        f"Invalid UUID: '{cmd_args.uuid}'"
                    ) from err

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


class SnapStatus(Enum):
    """
    Enum class representing snapshot status: Active, Inactive or Invalid.
    """

    ACTIVE = 1
    INACTIVE = 2
    INVALID = 3

    def __str__(self):
        """
        Return a string representation of this ``SnapStatus`` object.

        :returns: "Active", "Inactive", or "Invalid".
        """
        if self == SnapStatus.ACTIVE:
            return "Active"
        if self == SnapStatus.INACTIVE:
            return "Inactive"
        return "Invalid"


class SnapshotSet:
    """
    Representation of a set of snapshots taken at the same point
    in time and managed as a group.
    """

    _name = None
    _uuid = None
    _timestamp = None
    _snapshots = []

    def __init__(self, name, timestamp, snapshots):
        """
        Initialise a new ``SnapshotSet`` object.

        :param name: The name of this ``SnapshotSet``.
        :param timestamp: The creation timestamp of this ``SnapshotSet``.
        :param snapshots: A list of ``Snapshot`` objects to be included in this set.
        """
        self._name = name
        self._timestamp = timestamp
        self._uuid = uuid5(NAMESPACE_SNAPSHOT_SET, name + str(timestamp))
        self._snapshots = snapshots
        self._by_mount_point = {}
        self.boot_entry = None
        self.rollback_entry = None
        for snapshot in self._snapshots:
            self._by_mount_point[snapshot.mount_point] = snapshot

    def __str__(self):
        """
        Return a string representation of this ``SnapshotSet``.

        :returns: A multi-line string describing this snapshot set.
        """
        snapset_str = (
            f"SnapsetName:    {self.name}\n"
            f"MountPoints:    {', '.join([s.mount_point for s in self.snapshots])}\n"
            f"NrSnapshots:    {self.nr_snapshots}\n"
            f"Time:           {datetime.fromtimestamp(self.timestamp)}\n"
            f"UUID:           {self.uuid}\n"
            f"Status:         {str(self.status)}"
        )
        if self.boot_entry:
            snapset_str += f"\nBoot entry:     {self.boot_entry.disp_boot_id}"
        if self.rollback_entry:
            snapset_str += f"\nRollback entry: {self.rollback_entry.disp_boot_id}"
        return snapset_str

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
    def mount_points(self):
        """
        The list of mount points in this snapshot set.
        """
        return [s.mount_point for s in self.snapshots]

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


# pylint: disable=too-many-instance-attributes
class Snapshot:
    """
    Base class for individual snapshots. Each snapshot plugin should
    subclass ``Snapshot`` to provide a specific implementation.
    """

    _name = None
    _uuid = None
    _snapset_name = None
    _origin = None
    _timestamp = None
    _mount_point = None
    _provider = None
    _snapshot_set = None

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
        self._provider = provider

    def __str__(self):
        """
        Return a string representation of this ``Snapshot`` object.

        :returns: A multi-line string describing this snapshot.
        """
        return (
            f"Name:           {self.name}\n"
            f"SnapsetName:    {self.snapset_name}\n"
            f"Origin:         {self.origin}\n"
            f"Time:           {datetime.fromtimestamp(self.timestamp)}\n"
            f"MountPoint:     {self.mount_point}\n"
            f"Provider:       {self.provider}\n"
            f"UUID:           {self.uuid}\n"
            f"Status:         {str(self.status)}\n"
            f"Autoactivate:   {'yes' if self.autoactivate else 'no'}\n"
            f"DevicePath:     {self.devpath}"
        )

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
    def provider(self):
        """
        The name of the plugin managing this snapshot.
        """
        return self._provider.name

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
    def autoactivate(self):
        """
        The autoactivation status of this snapshot. Returns ``True`` if the
        snapshot is automatically activated or ``False`` otherwise.
        """
        raise NotImplementedError

    def delete(self):
        """
        Delete this snapshot.
        """
        self._provider.delete_snapshot(self.name)

    def rename(self, new_name):
        """
        Rename a snapshot within a snapshot set.

        :param new_name: The new name for the snapshot.
        """
        return self._provider.rename_snapshot(
            self.name, self.origin, new_name, self.timestamp, self.mount_point
        )

    def rollback(self):
        """
        Request to roll back a snapshot and revert the content of the origin
        volume to its state at the time of the snapshot.

        This may be deferred until the next device activation or mount
        operation for the respective volume.
        """
        self._provider.rollback_snapshot(self.name)

    def invalidate_cache(self):
        """
        Invalidate any cached data describing this snapshot.
        """
        raise NotImplementedError

    def activate(self):
        """
        Activate this snapshot.
        """
        self._provider.activate_snapshot(self.name)
        self.invalidate_cache()

    def deactivate(self):
        """
        Deactivate this snapshot.
        """
        self._provider.deactivate_snapshot(self.name)
        self.invalidate_cache()

    def set_autoactivate(self, auto=False):
        """
        Set the autoactivation state of this snapshot.

        :param auto: ``True`` to enable autoactivation or ``False`` otherwise.
        """
        self._provider.set_autoactivate(self.name, auto=auto)
        self.invalidate_cache()


__all__ = [
    "ETC_FSTAB",
    "SNAPM_DEBUG_MANAGER",
    "SNAPM_DEBUG_COMMAND",
    "SNAPM_DEBUG_PLUGINS",
    "SNAPM_DEBUG_REPORT",
    "SNAPM_DEBUG_ALL",
    "SnapmLogger",
    "set_debug_mask",
    "get_debug_mask",
    "SnapmError",
    "SnapmCalloutError",
    "SnapmNoSpaceError",
    "SnapmNoProviderError",
    "SnapmExistsError",
    "SnapmPathError",
    "SnapmNotFoundError",
    "SnapmInvalidIdentifierError",
    "Selection",
    "SnapStatus",
    "SnapshotSet",
    "Snapshot",
]
