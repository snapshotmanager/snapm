# Copyright Red Hat
#
# snapm/manager/plugins/stratis.py - Snapshot Manager Stratis plugin
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: GPL-2.0-only
"""
Stratis snapshot manager plugin
"""
from os import stat
from os.path import join as path_join
from subprocess import run, CalledProcessError
from stat import S_ISBLK
from time import time
from uuid import UUID

from dbus.exceptions import DBusException

from dbus_python_client_gen import (
    DPClientInvocationError,
    DPClientSetPropertyContext,
)


from snapm import (
    SnapmCalloutError,
    SnapmBusyError,
    SnapmNoSpaceError,
    SnapmNotFoundError,
    SnapmPluginError,
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
    Plugin,
    parse_snapshot_name,
    device_from_mount_point,
    mount_point_space_used,
    format_snapshot_name,
    encode_mount_point,
)

from .stratislib import (
    StratisdErrors,
    StratisCliStratisdVersionError,
    check_stratisd_version,
    get_object,
    TOP_OBJECT,
    Id,
    IdType,
    MOFilesystem,
    MOPool,
    ObjectManager,
    Pool,
    Filesystem,
    filesystems,
    pools,
)

STRATIS_UUID_PREFIX = "stratis-1-"

STRATIS_DECODE_DM_CMD = "stratis-decode-dm"
STRATIS_DECODE_DM_OUTPUT = "--output"
STRATIS_DECODE_DM_SYMLINK = "symlink"

DBUS_CACHE_VALID = 5

DEV_STRATIS_PREFIX = "/dev/stratis/"

# Minimum allowed Stratis snapshot size (512MiB)
MIN_STRATIS_SNAPSHOT_SIZE = 512 * 1024**2


def is_stratis_device(devpath):
    """
    Test whether ``devpath`` is a Stratis device.

    Return ``True`` if the device at ``devpath`` is a Stratis device or
    ``False`` otherwise.
    """
    dmsetup_cmd_args = [
        DMSETUP_CMD,
        DMSETUP_INFO,
        DMSETUP_COLUMNS,
        DMSETUP_NO_HEADINGS,
        DMSETUP_FIELDS_UUID,
        devpath,
    ]
    try:
        dmsetup_cmd = run(dmsetup_cmd_args, capture_output=True, check=True)
    except CalledProcessError as err:  # pragma: no cover
        if "not found" in err.stderr.decode("utf8"):
            return False
        raise SnapmCalloutError(
            f"Error calling {DMSETUP_CMD}: {err.stderr.decode('utf8')}"
        ) from err
    uuid = dmsetup_cmd.stdout.decode("utf8").strip()
    return uuid.startswith(STRATIS_UUID_PREFIX)


def is_stratisd_running():
    """
    Test whether stratisd is running by attempting to get TOP_OBJECT.

    :returns: ``True`` if the stratisd DBus interface is available or
              ``False`` otherwise.
    """
    try:
        get_object(TOP_OBJECT)
    except DBusException:
        return False
    return True


def stratis_devices_present():
    """
    Test whether any stratis managed devices are present on the system.

    :returns: ``True`` if stratis devices exist or ``False`` otherwise.
    """
    dmsetup_cmd_args = [
        DMSETUP_CMD,
        DMSETUP_INFO,
        DMSETUP_COLUMNS,
        DMSETUP_NO_HEADINGS,
        DMSETUP_FIELDS_UUID,
    ]
    try:
        dmsetup_cmd = run(dmsetup_cmd_args, capture_output=True, check=True)
    except CalledProcessError as err:  # pragma: no cover
        raise SnapmCalloutError(f"Error calling {DMSETUP_CMD}") from err
    for uuid in dmsetup_cmd.stdout.decode("utf8").strip().splitlines():
        if uuid.startswith(STRATIS_UUID_PREFIX):
            return True
    return False


def pool_fs_from_device_path(devpath):
    """
    Return a ``(pool_name, fs_name)`` tuple for the Stratis device at
    ``devpath``.
    """
    stratis_decode_dm_cmd_args = [
        STRATIS_DECODE_DM_CMD,
        STRATIS_DECODE_DM_OUTPUT,
        STRATIS_DECODE_DM_SYMLINK,
        devpath,
    ]
    try:
        stratis_decode_dm_cmd = run(
            stratis_decode_dm_cmd_args, capture_output=True, check=True
        )
    except CalledProcessError as err:  # pragma: no cover
        raise SnapmCalloutError(f"Error calling {STRATIS_DECODE_DM_CMD}") from err
    symlink = stratis_decode_dm_cmd.stdout.decode("utf8").strip()
    return symlink.removeprefix(DEV_STRATIS_PREFIX).split("/")


def pool_fs_from_origin(origin):
    """
    Return a ``(pool_name, fs_name)`` tuple for the Stratis device with
    origin path ``origin``.
    """
    return origin.removeprefix(DEV_STRATIS_PREFIX).split("/")


class StratisSnapshot(Snapshot):
    """
    Class for Stratis snapshot objects.
    """

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        name,
        snapset_name,
        origin,
        timestamp,
        mount_point,
        provider,
        pool_name,
        fs_name,
    ):
        super().__init__(name, snapset_name, origin, timestamp, mount_point, provider)
        self.pool_name = pool_name
        self.fs_name = fs_name
        self._pool = None
        self._filesystem = None
        self._cache_ts = 0
        self._get_dbus_cache()

    def __str__(self):
        return "".join(
            [
                super().__str__(),
                f"\nPool:           {self.pool_name}",
                f"\nFilesystem:     {self.fs_name}",
            ]
        )

    @property
    def origin(self):
        return path_join(DEV_PREFIX, "stratis", self.pool_name, self._origin)

    @property
    def origin_options(self):
        """
        File system options needed to specify the origin of this snapshot.

        No file system options are requires for Stratis block snapshots: always
        return the empty string.
        """
        return ""

    @property
    def devpath(self):
        return path_join(DEV_PREFIX, "stratis", self.pool_name, self.fs_name)

    @property
    def status(self):
        (_, filesystem) = self._get_dbus_cache()
        if filesystem.MergeScheduled() is True:
            return SnapStatus.REVERTING
        return SnapStatus.ACTIVE

    @property
    def size(self):
        (_, filesystem) = self._get_dbus_cache()
        return int(filesystem.Size())

    @property
    def free(self):
        (pool, _) = self._get_dbus_cache()
        size = int(pool.TotalPhysicalSize())
        used = int(pool.TotalPhysicalUsed()[1]) if pool.TotalPhysicalUsed()[0] else 0
        return size - used

    # Pylint does not understand the decorator notation.
    # pylint: disable=invalid-overridden-method
    @Snapshot.autoactivate.getter
    def autoactivate(self):
        # Stratis filesystems always autoactivate with the pool
        return True

    def invalidate_cache(self):
        self._pool = None
        self._filesystem = None
        self._cache_ts = 0

    def _get_dbus_cache(self):
        now = time()
        if (
            not (self._pool and self._filesystem)
            or (self._cache_ts + DBUS_CACHE_VALID) < now
        ):
            try:
                proxy = get_object(TOP_OBJECT)
                managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})
            except DBusException as err:
                raise SnapmPluginError(
                    f"Failed to communicate with stratisd: {err}"
                ) from err

            (pool, filesystem) = _get_pool_filesystem(
                managed_objects, self.pool_name, self.fs_name
            )
            self._pool = pool
            self._filesystem = filesystem
            self._cache_ts = time()
        return (self._pool, self._filesystem)


def filter_stratis_snapshot(filesystem):
    """
    Filter Stratis snapshots.

    Return ``True`` if the filesystem epresented by ``filesystem`` is a stratis
    snapshot or ``False`` otherwise. The ``filesystem`` argument must be a
    DBus managed object corresponding to a Stratis filesystem.
    """
    return filesystem.Origin()[0]


def _snapshot_min_size(policy_size):
    """
    Return the minimum snapshot size given the space used by the snapshot
    mount point.

    :param policy_size: The size suggested by the in-use size policy.
    :returns: The greater of ``policy_size`` and ``MIN_STRATIS_SNAPSHOT_SIZE``.
    """
    return max(MIN_STRATIS_SNAPSHOT_SIZE, policy_size)


def _get_pool_filesystem(managed_objects, pool_name, fs_name):
    """
    Return pool and filesystem managed objects.

    :param managed_objects: The managed objects to search.
    :param pool_name: The name of the pool to return.
    :param fs_name: The name of the filesystem to return, or ``None``
                    to query only the pool.
    """
    props = {"Name": pool_name}
    pool_object_path = next(
        pools(props=props).require_unique_match(True).search(managed_objects)
    )[0]

    if fs_name is not None:
        fs_id = Id(IdType.NAME, fs_name)
        fs_props = {"Pool": pool_object_path} | fs_id.managed_objects_key()

        filesystem = [
            MOFilesystem(info)
            for objpath, info in filesystems(props=fs_props)
            .require_unique_match(True)
            .search(managed_objects)
        ][0]
    else:
        filesystem = None

    pool = [
        MOPool(info)
        for objpath, info in pools(props=props)
        .require_unique_match(True)
        .search(managed_objects)
    ][0]
    return (pool, filesystem)


def _origin_uuid_to_fs_name(managed_objects, pool_object_path, origin_uuid):
    """
    Return the filesystem corresponding to `origin_uuid`.
    :param managed_objects: DBus managed objects for the service
    :param pool_object_path: The pool DBus object path
    :param origin_uuid: The origin UUID to find.
    """
    fs_id = Id(IdType.UUID, UUID(origin_uuid))
    fs_props = {"Pool": pool_object_path} | fs_id.managed_objects_key()

    filesystem = [
        MOFilesystem(info)
        for objpath, info in filesystems(props=fs_props)
        .require_unique_match(True)
        .search(managed_objects)
    ][0]

    return str(filesystem.Name())


def _fs_name_to_uuid(managed_objects, pool_object_path, fs_name):
    """
    Return the filesystem UUID corresponding to `origin_name`.
    :param managed_objects: DBus managed objects for the service
    :param pool_object_path: The pool DBus object path
    :param origin_name: The origin filesystem name to find
    """
    fs_props = {"Pool": pool_object_path, "Name": fs_name}

    filesystem = [
        MOFilesystem(info)
        for objpath, info in filesystems(props=fs_props)
        .require_unique_match(True)
        .search(managed_objects)
    ][0]

    return str(filesystem.Uuid())


def _find_in_progress_merge(managed_objects, pool_object_path, origin_uuid):
    """
    Return a list containing any in-progres merge for the specified
    `pool_object_path` and `origin_uuid`, or the empty list if no merge is
    in progress.
    :param managed_objects: DBus managed objects for the service
    :param pool_object_path: The pool DBus object path
    :param origin_uuid: The origin filesystem uuid to find
    """
    fs_props = {
        "Pool": pool_object_path,
        "Origin": (True, origin_uuid),
        "MergeScheduled": True,
    }

    return [
        MOFilesystem(info)
        for objpath, info in filesystems(props=fs_props).search(managed_objects)
    ]


def _pool_free_space_bytes(managed_objects, pool_name):
    """
    Return the free space available as bytes for the Stratis pool named
    ``pool_name``.
    """
    (pool, _) = _get_pool_filesystem(managed_objects, pool_name, None)
    size = int(pool.TotalPhysicalSize())
    used = int(pool.TotalPhysicalUsed()[1]) if pool.TotalPhysicalUsed()[0] else 0
    return size - used


def _fs_size_bytes(managed_objects, pool_name, fs_name):
    """
    Return the size of the specified filesystem in bytes.
    """
    (_, filesystem) = _get_pool_filesystem(managed_objects, pool_name, fs_name)
    return int(filesystem.Size())


class Stratis(Plugin):
    """
    Class for Stratis snapshot plugin.
    """

    name = "stratis"
    version = "0.1.0"
    snapshot_class = StratisSnapshot

    def __init__(self, logger):
        """
        Initialise the Stratis plugin.

        :param logger: The logger to pass to the Plugin class.
        """
        super().__init__(logger)
        try:
            check_stratisd_version()
        except DBusException as err:
            if stratis_devices_present():
                self._log_warn(
                    "Stratis devices present but stratisd is not running: %s", err
                )
                self._log_warn(
                    "Run 'systemctl enable --now stratisd' to manage snapshots for Stratis volumes"
                )
            raise SnapmNotFoundError(
                f"Stratisd DBus service not available: {err}"
            ) from err
        except StratisCliStratisdVersionError as err:
            raise SnapmPluginError(f"Stratisd version check failed: {err}") from err

    # pylint: disable=too-many-locals
    def discover_snapshots(self):
        """
        Discover snapshots managed by this plugin class.

        Returns a list of objects that are a subclass of ``Snapshot``.
        """
        snapshots = []

        try:
            proxy = get_object(TOP_OBJECT)
            managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})
        except DBusException as err:
            raise SnapmPluginError(
                f"Failed to communicate with stratisd: {err}"
            ) from err

        path_to_name = dict(
            (path, MOPool(info).Name())
            for path, info in pools().search(managed_objects)
        )

        filesystems_with_props = [
            MOFilesystem(info)
            for objpath, info in filesystems(props=None)
            .require_unique_match(False)
            .search(managed_objects)
        ]

        for filesystem in filesystems_with_props:
            if not filter_stratis_snapshot(filesystem):
                continue

            pool_name = path_to_name[filesystem.Pool()]
            filesystem_name = str(filesystem.Name())

            origin = _origin_uuid_to_fs_name(
                managed_objects, filesystem.Pool(), str(filesystem.Origin()[1])
            )

            try:
                fields = parse_snapshot_name(filesystem_name, origin)
            except ValueError:
                continue
            if fields is not None:
                (snapset, timestamp, mount_point) = fields
                full_name = f"{pool_name}/{filesystem_name}"
                self._log_debug("Found %s snapshot: %s", self.name, full_name)
                snapshots.append(
                    StratisSnapshot(
                        full_name,
                        snapset,
                        origin,
                        timestamp,
                        mount_point,
                        self,
                        pool_name,
                        filesystem_name,
                    )
                )

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

        if not is_stratis_device(device):
            return False
        if not is_stratisd_running():
            self._log_error("Stratis source specified but stratisd is not running")
            return False
        return True

    # pylint: disable=too-many-arguments
    def _check_free_space(self, managed_objects, origin, mount_point, size_policy):
        """
        Check for available space in pool ``pool_name`` for the specified
        mount point.

        :param pool_name: The name of the pool to check.
        :param fs_name: The name of the filesystem to check.
        :param mount_point: The mount point path to check.
        :param size_policy: The size policy to be applied.
        :returns: The minimum size required for the snapshot.
        :raises: ``SnapmNoSpaceError`` if the minimum snapshot size exceeds the
                 available space.
        """
        pool_name, fs_name = pool_fs_from_origin(origin)
        fs_used = mount_point_space_used(mount_point)
        pool_free = _pool_free_space_bytes(managed_objects, pool_name)
        fs_size = _fs_size_bytes(managed_objects, pool_name, fs_name)
        policy = SizePolicy(
            origin, mount_point, pool_free, fs_used, fs_size, size_policy
        )
        snapshot_min_size = _snapshot_min_size(policy.size)
        if pool_free < (sum(self.size_map[pool_name].values()) + snapshot_min_size):
            raise SnapmNoSpaceError(
                f"Stratis pool {pool_name} has insufficient free space to snapshot {mount_point}"
            )
        return snapshot_min_size

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
        pool_name, fs_name = pool_fs_from_origin(origin)
        try:
            proxy = get_object(TOP_OBJECT)
            managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})
        except DBusException as err:
            raise SnapmPluginError(
                f"Failed to communicate with stratisd: {err}"
            ) from err

        if pool_name not in self.size_map:
            self.size_map[pool_name] = {}
            self.size_map[pool_name][fs_name] = self._check_free_space(
                managed_objects, origin, mount_point, size_policy
            )

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
        pool_name, fs_name = pool_fs_from_origin(origin)

        snapshot_name = format_snapshot_name(
            fs_name, snapset_name, timestamp, encode_mount_point(mount_point)
        )

        try:
            proxy = get_object(TOP_OBJECT)
            managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})
        except DBusException as err:
            raise SnapmPluginError(
                f"Failed to communicate with stratisd: {err}"
            ) from err

        self._check_free_space(managed_objects, origin, mount_point, size_policy)

        self._log_debug(
            "Creating Stratis snapshot for %s/%s mounted at %s",
            pool_name,
            fs_name,
            mount_point,
        )
        (pool_object_path, _) = next(
            pools(props={"Name": pool_name})
            .require_unique_match(True)
            .search(managed_objects)
        )
        (origin_fs_object_path, _) = next(
            filesystems(props={"Name": fs_name, "Pool": pool_object_path})
            .require_unique_match(True)
            .search(managed_objects)
        )

        ((changed, _), return_code, message) = Pool.Methods.SnapshotFilesystem(
            get_object(pool_object_path),
            {"origin": origin_fs_object_path, "snapshot_name": snapshot_name},
        )

        if return_code != StratisdErrors.OK:  # pragma: no cover
            raise SnapmPluginError(message)

        if not changed:  # pragma: no cover
            raise SnapmPluginError(
                f"Stratis daemon reported no change creating snapshot {snapshot_name}"
            )

        return StratisSnapshot(
            f"{pool_name}/{snapshot_name}",
            snapset_name,
            fs_name,
            timestamp,
            mount_point,
            self,
            pool_name,
            snapshot_name,
        )

    def origin_from_mount_point(self, mount_point):
        """
        Return a string representing the origin from a given mount point path.
        """
        device = device_from_mount_point(mount_point)
        if not is_stratis_device(device):
            return None
        (pool_name, fs_name) = pool_fs_from_device_path(device)
        return path_join(DEV_STRATIS_PREFIX, pool_name, fs_name)

    def delete_snapshot(self, name):
        """
        Delete the snapshot named ``name``

        :param name: The name of the snapshot to be removed.
        """
        (pool_name, fs_name) = name.split("/")
        fs_name = [fs_name]

        try:
            proxy = get_object(TOP_OBJECT)
            managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})
        except DBusException as err:
            raise SnapmPluginError(
                f"Failed to communicate with stratisd: {err}"
            ) from err

        (pool_object_path, _) = next(
            pools(props={"Name": pool_name})
            .require_unique_match(True)
            .search(managed_objects)
        )

        requested_names = frozenset(fs_name)

        pool_filesystems = {
            MOFilesystem(info).Name(): op
            for (op, info) in filesystems(props={"Pool": pool_object_path}).search(
                managed_objects
            )
        }
        already_removed = requested_names.difference(frozenset(pool_filesystems.keys()))

        if already_removed != frozenset():  # pragma: no cover
            raise SnapmPluginError(
                f"Stratisd destroy reported snapshot already removed: {already_removed}"
            )

        fs_object_paths = [
            op for (name, op) in pool_filesystems.items() if name in requested_names
        ]

        (
            (destroyed, list_destroyed),
            return_code,
            message,
        ) = Pool.Methods.DestroyFilesystems(
            get_object(pool_object_path), {"filesystems": fs_object_paths}
        )

        if return_code != StratisdErrors.OK:  # pragma: no cover
            raise SnapmPluginError(message)

        if not destroyed or len(list_destroyed) < len(
            fs_object_paths
        ):  # pragma: no cover
            raise SnapmPluginError(
                (
                    f"Expected to destroy the specified filesystems in pool "
                    f"{pool_name} but stratisd reports that it did not"
                    f"actually destroy some or all of the filesystems "
                    f"requested"
                )
            )

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
        (pool_name, fs_name) = old_name.split("/")
        _, origin = pool_fs_from_origin(origin)
        new_name = format_snapshot_name(
            origin, snapset_name, timestamp, encode_mount_point(mount_point)
        )

        self._log_debug(
            "Renaming snapshot from %s to %s",
            old_name,
            new_name,
        )

        try:
            proxy = get_object(TOP_OBJECT)
            managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})
        except DBusException as err:
            raise SnapmPluginError(
                f"Failed to communicate with stratisd: {err}"
            ) from err

        (pool_object_path, _) = next(
            pools(props={"Name": pool_name})
            .require_unique_match(True)
            .search(managed_objects)
        )
        (fs_object_path, _) = next(
            filesystems(props={"Name": fs_name, "Pool": pool_object_path})
            .require_unique_match(True)
            .search(managed_objects)
        )
        ((changed, _), return_code, message) = Filesystem.Methods.SetName(
            get_object(fs_object_path), {"name": new_name}
        )

        if return_code != StratisdErrors.OK:  # pragma: no cover
            raise SnapmPluginError(
                f"Rename failed: stratisd returned error {return_code}, {message})"
            )

        if not changed:
            raise SnapmPluginError(f"Rename reported no change: {new_name}")

        return StratisSnapshot(
            f"{pool_name}/{new_name}",
            snapset_name,
            fs_name,
            timestamp,
            mount_point,
            self,
            pool_name,
            new_name,
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
        pool_name, fs_name = pool_fs_from_origin(origin)
        try:
            proxy = get_object(TOP_OBJECT)
            managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})
        except DBusException as err:
            raise SnapmPluginError(
                f"Failed to communicate with stratisd: {err}"
            ) from err

        if pool_name not in self.size_map:
            self.size_map[pool_name] = {}
            self.size_map[pool_name][fs_name] = self._check_free_space(
                managed_objects, origin, mount_point, size_policy
            )

    def resize_snapshot(self, name, origin, mount_point, size_policy):
        """
        Perform any necessary resize operation on this snapshot. Since Stratis
        snapshots use space allocated dynamically from the thin pool this is a
        no-op for Stratis devices.
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
        pool_name, origin = pool_fs_from_origin(origin)

        try:
            proxy = get_object(TOP_OBJECT)
            managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})
        except DBusException as err:
            raise SnapmPluginError(
                f"Failed to communicate with stratisd: {err}"
            ) from err

        (pool_object_path, _) = next(
            pools(props={"Name": pool_name})
            .require_unique_match(True)
            .search(managed_objects)
        )

        origin_uuid = _fs_name_to_uuid(managed_objects, pool_object_path, origin)
        in_progress = _find_in_progress_merge(
            managed_objects, pool_object_path, origin_uuid
        )

        if len(in_progress):
            raise SnapmBusyError(
                f"Snapshot revert is in progress for {name} origin volume {pool_name}/{origin}"
            )

    def revert_snapshot(self, name):
        """
        Revert the state of the content of the origin to the content at the
        time the snapshot was taken.

        For Stratis snapshots of in-use filesystems this will take place at
        the next activation (typically a reboot into the revert boot entry
        for the snapshot set).
        """
        (pool_name, fs_name) = name.split("/")

        try:
            proxy = get_object(TOP_OBJECT)
            managed_objects = ObjectManager.Methods.GetManagedObjects(proxy, {})
        except DBusException as err:
            raise SnapmPluginError(
                f"Failed to communicate with stratisd: {err}"
            ) from err

        (pool_object_path, _) = next(
            pools(props={"Name": pool_name})
            .require_unique_match(True)
            .search(managed_objects)
        )
        (fs_object_path, info) = next(
            filesystems(props={"Name": fs_name, "Pool": pool_object_path})
            .require_unique_match(True)
            .search(managed_objects)
        )
        filesystem = MOFilesystem(info)

        try:
            Filesystem.Properties.MergeScheduled.Set(get_object(fs_object_path), True)
        except DPClientInvocationError as err:
            if isinstance(err.context, DPClientSetPropertyContext):
                origin_uuid = filesystem.Origin()[1]
                if len(
                    _find_in_progress_merge(
                        managed_objects, pool_object_path, origin_uuid
                    )
                ):
                    origin = _origin_uuid_to_fs_name(
                        managed_objects, pool_object_path, origin_uuid
                    )
                    raise SnapmBusyError(
                        f"Snapshot revert is in progress for {fs_name} origin volume "
                        f"{pool_name}/{origin}"
                    ) from err
            raise SnapmPluginError(
                f"Unexpected D-Bus error setting property for {pool_name}/{fs_name}"
            ) from err

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
