# Copyright Mark Flitter flits@flits.co.uk
#
# snapm/manager/plugins/btrfs.py - Snapshot Manager Btrfs plugin
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Btrfs snapshot manager plugin
"""
from os.path import exists as path_exists, join as path_join
from os import stat
from subprocess import run, CalledProcessError
from stat import S_ISBLK
from time import time
from uuid import UUID

from snapm import (
    SnapmCalloutError,
    SnapmBusyError,
    SnapmNoSpaceError,
    SnapmNotFoundError,
    SnapmPluginError,
    SnapmLimitError,
    SizePolicy,
    SnapStatus,
    Snapshot,
)
from snapm.manager.plugins import (
    DEV_PREFIX,
    DMSETUP_CMD,
    DMSETUP_INFO,
    DMSETUP_NO_HEADINGS,
    DMSETUP_COLUMNS,
    DMSETUP_FIELDS_UUID,
    PLUGIN_NO_PRIORITY,
    Plugin,
    parse_snapshot_name,
    device_from_mount_point,
    mount_point_space_used,
    format_snapshot_name,
    encode_mount_point,
)

BTRFS_CACHE_VALID = 5

#: Btrfs static priority value
BTRFS_STATIC_PRIORITY = 30


def is_btrfs_device(devpath):
    """
    Test whether ``devpath`` is a Btrfs device.

    Return ``True`` if the device at ``devpath`` is a Btrfs device or
    ``False`` otherwise.
    """
    if not path_exists(devpath):
        return False

    # FIXME


def btrfs_devices_present():
    """
    Test whether any btrfs managed devices are present on the system.

    :returns: ``True`` if btrfs devices exist or ``False`` otherwise.
    """


class BtrfsSnapshot(Snapshot):
    """
    Class for Btrfs snapshot objects.
    """

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        name: str,
        snapset_name: str,
        origin: str,
        timestamp: int,
        mount_point: str,
        provider: Plugin,
        what: str,
        subvol: str,
        subvolid: int,
        _cache_btrfs,  # Definitely FIXME
    ):
        super().__init__(name, snapset_name, origin, timestamp, mount_point, provider)

    def __str__(self):
        return "".join(
            [
                super().__str__(),
                f"\nDevice:           {self.what}",
                f"\nSubvol:           {self.subvol}",
                f"\nSubvolID:     {self.subvolid}",
                "\nStatus: EXPERIMENTAL",  # Also FIXME
            ]
        )

    @property
    def origin(self):
        return path_join(DEV_PREFIX, "btrfs", self.pool_name, self._origin)

    @property
    def origin_options(self):
        """
        File system options needed to specify the origin of this snapshot.

        No file system options are requires for Btrfs block snapshots: always
        return the empty string.
        """
        return ""

    @property
    def devpath(self):
        # FIXME: use resolve_device_spec once merged (#961)
        return self.what

    @property
    def status(self):
        # FIXME: how are we going to keep track of in-progress merges?
        return SnapStatus.ACTIVE

    @property
    def size(self):
        return 1000000  # FIXME stat

    @property
    def free(self):
        return 900000  # FIXME also stat

    # Pylint does not understand the decorator notation.
    # pylint: disable=invalid-overridden-method
    @Snapshot.autoactivate.getter
    def autoactivate(self):
        # Btrfs subvolumes always activate with the main file system
        return True

    def invalidate_cache(self):
        pass


def filter_btrfs_snapshot(what, subvol):
    """
    Filter Btrfs snapshots.

    Return ``True`` if the filesystem epresented by ``what`` and ``subvol`` is
    a btrfs snapshot or ``False`` otherwise.

    :param what: The device containing the file system.
    :param subvol: The subvolume path to consider.
    """
    return False


def _snapshot_min_size(policy_size):
    """
    Return the minimum snapshot size given the space used by the snapshot
    mount point.

    This is somewhat meaningless for btrfs.

    :param policy_size: The size suggested by the in-use size policy.
    :returns: The greater of ``policy_size`` and ``MIN_BTRFS_SNAPSHOT_SIZE``.
    """
    return max(MIN_BTRFS_SNAPSHOT_SIZE, policy_size)


def _find_in_progress_merge(what, subvol):
    """
    Return a list containing any in-progres merge for the specified
    filesystem and subvolume.

    :param
    """
    return []


def _fs_size_bytes(what):
    """
    Return the size of the specified filesystem in bytes.
    """
    return 0  # FIXME stat


class Btrfs(Plugin):
    """
    Class for Btrfs snapshot plugin.
    """

    name = "btrfs"
    version = "0.1.0"
    snapshot_class = BtrfsSnapshot

    def __init__(self, logger, plugin_cfg):
        """
        Initialise the Btrfs plugin.

        :param logger: The logger to pass to the Plugin class.
        """
        super().__init__(logger, plugin_cfg)
        self.pools = {}
        if self.priority == PLUGIN_NO_PRIORITY:
            self.priority = BTRFS_STATIC_PRIORITY

    # pylint: disable=too-many-locals
    def discover_snapshots(self):
        """
        Discover snapshots managed by this plugin class.

        Returns a list of objects that are a subclass of ``Snapshot``.
        """
        snapshots = []

        # Type Some Shit In Here Please

        return snapshots

    def can_snapshot(self, source):
        """
        Test whether this plugin can snapshot the specified mount point.

        :param source: The mount point path to test.
        :returns: ``True`` if this plugin can snapshot the file system mounted
                  at ``mount_point``, or ``False`` otherwise.
        """
        if S_ISBLK(stat(source).st_mode):
            device = source
        else:
            device = device_from_mount_point(source)

        if not is_btrfs_device(device):
            return False
        return True

    # pylint: disable=too-many-arguments
    def _check_free_space(self, what, mount_point, size_policy):
        """
        Check for available space in volume ``what`` for the specified
        mount point.

        :param what: The the file system to check.
        :param mount_point: The mount point path to check.
        :param size_policy: The size policy to be applied.
        :returns: The minimum size required for the snapshot.
        :raises: ``SnapmNoSpaceError`` if the minimum snapshot size exceeds the
                 available space.
        """
        snapshot_min_size = _snapshot_min_size(policy.size)
        # FIXME Check space
        return snapshot_min_size

    def _check_limits(self, pool: str) -> bool:
        """
        Check ``pool`` against configured plugin limits: return ``True`` if
        adding a new snapshot of this pool would exceed limits, and ``False``
        otherwise.

        :param pool: The pool volume to check.
        :type pool: ``str``
        :returns: ``True`` if adding a new snapshot would exceed limits, or
                 ``False`` otherwise.
        :rtype: ``bool``
        """
        return False  # FIXME: do something useful

    # pylint: disable=too-many-arguments
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
        pass  # FIXME

    # pylint: disable=too-many-arguments
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
        what = "somethingwhat"
        subvol = "somethingsubvol"
        # FIXME: btrfs snapshot_name
        # snapshot_name = format_snapshot_name(
        #    fs_name, snapset_name, timestamp, encode_mount_point(mount_point)
        # )

        self._check_free_space(what, mount_point, size_policy)

        self._log_debug(
            "Creating Btrfs snapshot for %s:%s mounted at %s",
            what,
            subvol,
            mount_point,
        )

        return BtrfsSnapshot(
            f"{what}:{subvol}",
            snapset_name,
            what,
            timestamp,
            mount_point,
            self,
            what,
            subvol,
            12345,  # FIXME use a real subvolid
        )

    def origin_from_mount_point(self, mount_point):
        """
        Return a string representing the origin from a given mount point path.
        """
        device = device_from_mount_point(mount_point)
        if not is_btrfs_device(device):
            return None
        return ""  # FIXME find this from 'btrfs subvol list'

    def delete_snapshot(self, name):
        """
        Delete the snapshot named ``name``

        :param name: The name of the snapshot to be removed.
        """
        pass  # fixme 'btrfs subvolume delete'

    # pylint: disable=too-many-arguments
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
        new_name = format_snapshot_name(
            origin, snapset_name, timestamp, encode_mount_point(mount_point)
        )

        self._log_debug(
            "Renaming snapshot from %s to %s",
            old_name,
            new_name,
        )

        return BtrfsSnapshot(
            f"{pool_name}/{new_name}",
            snapset_name,
            fs_name,
            timestamp,
            mount_point,
            self,
            what,
            subvol,
            12345,  # FIXME: presumably this won't change?
        )

    def check_resize_snapshot(self, name, origin, mount_point, size_policy):
        """
        Check whether this snapshot can be resized or not. This method returns
        if the current snapshot can be resized and raises an exception if not.

        :returns: None
        :raises: ``SnapmNoSpaceError`` if there is insufficient space to resize
                 the snapshot according to ``size_policy`` or ``SnapmPluginError``
                 if another error occurs.
        """
        pass  # FIXME

    def resize_snapshot(self, name, origin, mount_point, size_policy):
        """
        Perform any necessary resize operation on this snapshot. Since Btrfs
        snapshots use space allocated dynamically from the thin pool this is a
        no-op for Btrfs devices.
        """
        return

    def check_revert_snapshot(self, name, origin):
        """
        Check whether this snapshot can be reverted or not. This method returns
        if the current snapshot can be reverted and raises an exception if not.

        :returns: None
        :raises: ``NotImplementedError`` if this plugin does not support the
        revert operation, ``SnapmBusyError`` if the snapshot is already in the
        process of being reverted to another snapshot state or
        ``SnapmPluginError`` if another reason prevents the snapshot from being
        merged.
        """
        pass

    def revert_snapshot(self, name):
        """
        Revert the state of the content of the origin to the content at the
        time the snapshot was taken.

        For Btrfs snapshots of in-use filesystems this will take place at
        the next activation (typically a reboot into the revert boot entry
        for the snapshot set).
        """
        pass

    def activate_snapshot(self, name):
        """
        Activate the snapshot named ``name``

        :param name: The name of the snapshot to be activated.
        """
        return

    def deactivate_snapshot(self, name):
        """
        Deactivate the snapshot named ``name``

        :param name: The name of the snapshot to be deactivated.
        """
        return

    def set_autoactivate(self, name, auto=False):
        """
        Set the autoactivation state of the snapshot named ``name``.

        :param name: The name of the snapshot to be modified.
        :param auto: ``True`` to enable autoactivation or ``False`` otherwise.
        """
        return
