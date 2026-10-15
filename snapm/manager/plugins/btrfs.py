# Copyright Mark Flitter flits@flits.co.uk
#
# snapm/manager/plugins/btrfs.py - Snapshot Manager Btrfs plugin
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Btrfs snapshot manager plugin

Btrfs snapshots are subvolumes that share extents with the subvolume they
were created from: there is no per-snapshot block device and no separate
backing store to size or resize.

The plugin follows the subvolume layout used by Fedora installations: a
single btrfs file system containing flat, top level subvolumes (``root``,
``home``, ...) that are mounted via ``subvol=`` mount options. Snapshots
are created as read-write subvolumes alongside their origin at the top
level of the file system so that they can be mounted, or booted, using the
same ``subvol=`` mechanism.
"""
from collections import namedtuple
from contextlib import contextmanager
from os import environ, makedirs, rename, rmdir, stat, statvfs
from os.path import (
    basename,
    dirname,
    exists as path_exists,
    join as path_join,
    realpath,
    samefile,
)
from shutil import which
from stat import S_ISBLK
from subprocess import run, CalledProcessError
from tempfile import mkdtemp
from time import time

from snapm import (
    SNAPM_RUNTIME_DIR,
    SnapmCalloutError,
    SnapmBusyError,
    SnapmInvalidIdentifierError,
    SnapmNoSpaceError,
    SnapmNotFoundError,
    SnapmPluginError,
    SnapmLimitError,
    SnapmRecursionError,
    SizePolicy,
    SnapStatus,
    Snapshot,
    get_device_fstype,
    size_fmt,
)
from snapm.manager.plugins import (
    PLUGIN_NO_PRIORITY,
    Plugin,
    parse_snapshot_name,
    mount_point_space_used,
    format_snapshot_name,
    encode_mount_point,
)

#: Path to the kernel mount table
_PROC_MOUNTS = "/proc/self/mounts"

#: File system type reported for btrfs volumes
BTRFS_FSTYPE = "btrfs"

# btrfs command and sub-commands
BTRFS_CMD = "btrfs"
BTRFS_SUBVOLUME = "subvolume"
BTRFS_DELETE = "delete"
BTRFS_LIST = "list"
BTRFS_SHOW = "show"
BTRFS_SNAPSHOT = "snapshot"
BTRFS_VERSION = "--version"

# btrfs subvolume list options
BTRFS_LIST_UUID = "-u"
BTRFS_LIST_PARENT_UUID = "-q"

# mount and umount commands and options
MOUNT_CMD = "mount"
UMOUNT_CMD = "umount"
MOUNT_TYPE = "-t"
MOUNT_OPTIONS = "-o"

# btrfs subvolume list report fields
SUBVOL_ID = "ID"
SUBVOL_GEN = "gen"
SUBVOL_CGEN = "cgen"
SUBVOL_TOP_LEVEL = "top_level"
SUBVOL_OTIME = "otime"
SUBVOL_UUID = "uuid"
SUBVOL_PARENT_UUID = "parent_uuid"
SUBVOL_PATH = "path"

# btrfs subvolume show report fields
SUBVOL_SHOW_ID = "Subvolume ID"
SUBVOL_SHOW_UUID = "UUID"
SUBVOL_SHOW_PARENT_UUID = "Parent UUID"
SUBVOL_SHOW_FLAGS = "Flags"

#: Value used for unset btrfs report fields
BTRFS_NULL_FIELD = "-"

#: Mount option used to select a subvolume by path
BTRFS_SUBVOL_OPTION = "subvol"

#: Mount option used to select a subvolume by numeric identifier
BTRFS_SUBVOLID_OPTION = "subvolid"

#: Subvolume ID of the btrfs file system top level
BTRFS_TOPLEVEL_SUBVOLID = 5

#: Path of the btrfs file system top level
BTRFS_TOPLEVEL_SUBVOL = "/"

#: Maximum length of a btrfs subvolume name in bytes
BTRFS_MAX_NAME_LEN = 255

#: Separator between the device and subvolume components of btrfs snapshot
#: and origin identifiers.
BTRFS_NAME_SEP = ":"

#: Suffix applied to the origin subvolume of a revert operation. The
#: original content of the origin is preserved under this name until it is
#: removed by the administrator.
BTRFS_REVERT_SUFFIX = ".snapm-revert."

#: Directory used for temporary btrfs top level mounts
BTRFS_MOUNT_DIR = path_join(SNAPM_RUNTIME_DIR, BTRFS_FSTYPE)

#: Prefix for temporary btrfs top level mount points
BTRFS_MOUNT_PREFIX = "top."

#: Maximum time to cache btrfs file system data in seconds
BTRFS_CACHE_VALID = 5

#: Minimum btrfs snapshot size. Btrfs snapshots share their extents with
#: their origin subvolume and consume no space when created.
MIN_BTRFS_SNAPSHOT_SIZE = 0

#: Minimum btrfs-progs version supporting the reporting options used here
MINIMUM_BTRFS_VERSION = (5, 4, 0)

#: Commands required by the plugin
_BTRFS_CMDS = [
    BTRFS_CMD,
    MOUNT_CMD,
    UMOUNT_CMD,
]

#: Btrfs static priority value
BTRFS_STATIC_PRIORITY = 30

#: A btrfs mount table entry: the device backing the file system, the mount
#: point, the mounted subvolume path relative to the file system top level
#: and the mounted subvolume identifier.
BtrfsMount = namedtuple("BtrfsMount", ["device", "mount_point", "subvol", "subvolid"])

#: Cached btrfs file system data: the time at which the data was gathered, a
#: dictionary mapping subvolume paths to subvolume report fields, and the
#: size and free space of the file system in bytes.
BtrfsFsInfo = namedtuple("BtrfsFsInfo", ["timestamp", "subvolumes", "size", "free"])


def _decode_stderr(err):
    """
    Decode and strip the stderr member of a ``CalledProcessError``.

    :param err: A ``CalledProcessError`` like exception.
    :returns: A stripped string representation of the exception's stderr
              member.
    """
    return err.stderr.decode("utf8").strip()


def _callout_env():
    """
    Return a sanitized environment for plugin command callouts.

    :returns: A copy of the current environment with ``LC_ALL`` set to "C".
    """
    return dict(environ, LC_ALL="C")


def _btrfs(*args):
    """
    Call out to the ``btrfs`` command and return its output.

    :param args: The command line arguments to pass to ``btrfs``.
    :returns: The command standard output decoded as a string.
    :raises: ``SnapmCalloutError`` if the command returns an error.
    """
    btrfs_cmd_args = [BTRFS_CMD] + list(args)
    try:
        btrfs_cmd = run(
            btrfs_cmd_args, capture_output=True, check=True, env=_callout_env()
        )
    except CalledProcessError as err:
        raise SnapmCalloutError(
            f"Error calling {BTRFS_CMD}: {_decode_stderr(err)}"
        ) from err
    return btrfs_cmd.stdout.decode("utf8")


def _mount(device, path, options):
    """
    Mount the btrfs file system on ``device`` at ``path``.

    :param device: The device to mount.
    :param path: The mount point path to mount the device at.
    :param options: The mount options to apply.
    :raises: ``SnapmCalloutError`` if the mount attempt fails.
    """
    mount_cmd_args = [
        MOUNT_CMD,
        MOUNT_TYPE,
        BTRFS_FSTYPE,
        MOUNT_OPTIONS,
        options,
        device,
        path,
    ]
    try:
        run(mount_cmd_args, capture_output=True, check=True, env=_callout_env())
    except CalledProcessError as err:
        raise SnapmCalloutError(
            f"Error calling {MOUNT_CMD}: {_decode_stderr(err)}"
        ) from err


def _umount(path):
    """
    Unmount the file system mounted at ``path``.

    :param path: The mount point path to unmount.
    :raises: ``SnapmCalloutError`` if the umount attempt fails.
    """
    umount_cmd_args = [UMOUNT_CMD, path]
    try:
        run(umount_cmd_args, capture_output=True, check=True, env=_callout_env())
    except CalledProcessError as err:
        raise SnapmCalloutError(
            f"Error calling {UMOUNT_CMD}: {_decode_stderr(err)}"
        ) from err


def _unescape_mount(value):
    """
    Unescape octal escapes in values read from the kernel mount table.

    :param value: The string to unescape.
    :returns: The unescaped string with octal values replaced by literal
              character values.
    """
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _parse_mount_options(options):
    """
    Parse a comma separated mount option string into a dictionary.

    Options with no value are mapped to ``None``.

    :param options: The mount option string to parse.
    :returns: A dictionary mapping option names to option values.
    """
    parsed = {}
    for option in options.split(","):
        (key, _, value) = option.partition("=")
        parsed[key] = value if value else None
    return parsed


def btrfs_mounts():
    """
    Return the btrfs entries present in the kernel mount table.

    :returns: A list of ``BtrfsMount`` tuples describing each mounted btrfs
              subvolume.
    """
    mounts = []
    with open(_PROC_MOUNTS, "r", encoding="utf8") as proc_mounts:
        for line in proc_mounts:
            fields = line.split()
            if len(fields) < 4 or fields[2] != BTRFS_FSTYPE:
                continue
            options = _parse_mount_options(fields[3])
            subvol = options.get(BTRFS_SUBVOL_OPTION) or BTRFS_TOPLEVEL_SUBVOL
            subvolid = options.get(BTRFS_SUBVOLID_OPTION)
            mounts.append(
                BtrfsMount(
                    _unescape_mount(fields[0]),
                    _unescape_mount(fields[1]),
                    _unescape_mount(subvol.lstrip("/")),
                    int(subvolid) if subvolid else BTRFS_TOPLEVEL_SUBVOLID,
                )
            )
    return mounts


def _same_device(device, other):
    """
    Test whether two device paths refer to the same device.

    :param device: The first device path to compare.
    :param other: The second device path to compare.
    :returns: ``True`` if both paths refer to the same device or ``False``
              otherwise.
    """
    if not path_exists(device) or not path_exists(other):
        return False
    return samefile(device, other)


def mount_for_path(mount_point):
    """
    Return the btrfs mount table entry for the mount point ``mount_point``.

    Where more than one file system is mounted at ``mount_point`` the last,
    and therefore effective, mount is returned.

    :param mount_point: The mount point path to look up.
    :returns: A ``BtrfsMount`` tuple describing the mount, or ``None`` if
              ``mount_point`` is not a btrfs mount point.
    """
    found = None
    for mount in btrfs_mounts():
        if mount.mount_point == mount_point:
            found = mount
    return found


def _find_top_level_mount(device):
    """
    Return an existing mount of the top level of the btrfs file system on
    ``device``.

    :param device: The device backing the file system.
    :returns: A ``BtrfsMount`` tuple describing the top level mount, or
              ``None`` if the file system top level is not mounted.
    """
    for mount in btrfs_mounts():
        if not _same_device(mount.device, device):
            continue
        if mount.subvolid == BTRFS_TOPLEVEL_SUBVOLID:
            return mount
    return None


def is_btrfs_device(devpath):
    """
    Test whether ``devpath`` is a Btrfs device.

    Return ``True`` if the device at ``devpath`` is a Btrfs device or
    ``False`` otherwise.
    """
    if not path_exists(devpath):
        return False
    if not S_ISBLK(stat(devpath).st_mode):
        return False
    return get_device_fstype(devpath) == BTRFS_FSTYPE


def btrfs_devices_present():
    """
    Test whether any btrfs managed devices are present on the system.

    Only mounted btrfs file systems are considered: the plugin operates on
    subvolumes and requires the file system to be mounted in order to do so.

    :returns: ``True`` if btrfs devices exist or ``False`` otherwise.
    """
    return bool(btrfs_mounts())


def btrfs_devices():
    """
    Return the set of devices backing the mounted btrfs file systems on this
    system.

    :returns: A list of canonical device paths, one per btrfs file system.
    """
    devices = []
    for mount in btrfs_mounts():
        device = realpath(mount.device)
        if device not in devices:
            devices.append(device)
    return devices


def format_btrfs_name(device, subvol):
    """
    Format a btrfs snapshot or origin identifier.

    :param device: The device backing the btrfs file system.
    :param subvol: The subvolume path relative to the file system top level.
    :returns: An identifier of the form ``device:subvolume``.
    """
    return f"{device}{BTRFS_NAME_SEP}{subvol}"


def _split_btrfs_name(name):
    """
    Split a btrfs snapshot or origin identifier into its components.

    :param name: The identifier to split.
    :returns: A ``(device, subvol)`` tuple.
    :raises: ``SnapmInvalidIdentifierError`` if ``name`` is not a valid btrfs
             identifier.
    """
    (device, sep, subvol) = name.partition(BTRFS_NAME_SEP)
    if not sep or not device or not subvol:
        raise SnapmInvalidIdentifierError(f"Malformed btrfs identifier: {name}")
    return (device, subvol)


def device_subvol_from_origin(origin):
    """
    Return a ``(device, subvol)`` tuple for the btrfs origin ``origin``.

    :param origin: The origin identifier to parse.
    :returns: A ``(device, subvol)`` tuple.
    """
    return _split_btrfs_name(origin)


def device_subvol_from_name(name):
    """
    Return a ``(device, subvol)`` tuple for the btrfs snapshot ``name``.

    :param name: The snapshot name to parse.
    :returns: A ``(device, subvol)`` tuple.
    """
    return _split_btrfs_name(name)


def _check_subvol_name(subvol):
    """
    Check whether a proposed subvolume name exceeds the maximum allowed name
    length for a btrfs subvolume.

    :param subvol: The subvolume name to check.
    :raises: ``SnapmInvalidIdentifierError`` if the proposed name exceeds
             limits.
    """
    if len(basename(subvol).encode("utf8")) > BTRFS_MAX_NAME_LEN:
        raise SnapmInvalidIdentifierError(
            f"Subvolume name {subvol} exceeds maximum btrfs name length"
        )


def _parse_subvol_list_line(line):
    """
    Parse one line of ``btrfs subvolume list`` output.

    Report lines have the form::

        ID 258 gen 9 top level 5 parent_uuid <uuid> uuid <uuid> path root

    :param line: The report line to parse.
    :returns: A dictionary mapping report field names to values, or ``None``
              if ``line`` could not be parsed.
    """
    fields = {}
    tokens = line.split()
    index = 0
    while index < len(tokens) - 1:
        key = tokens[index]
        if key == "top" and tokens[index + 1] == "level":
            if index > len(tokens) - 3:
                return None
            fields[SUBVOL_TOP_LEVEL] = int(tokens[index + 2])
            index += 3
        elif key == SUBVOL_OTIME:
            # otime is reported as a space separated date and time pair
            fields[SUBVOL_OTIME] = " ".join(tokens[index + 1 : index + 3])
            index += 3
        elif key == SUBVOL_PATH:
            # path is always the final field and may contain spaces
            fields[SUBVOL_PATH] = " ".join(tokens[index + 1 :])
            index = len(tokens)
        elif key in (SUBVOL_ID, SUBVOL_GEN, SUBVOL_CGEN):
            fields[key] = int(tokens[index + 1])
            index += 2
        else:
            fields[key] = tokens[index + 1]
            index += 2
    if SUBVOL_PATH not in fields or SUBVOL_ID not in fields:
        return None
    return fields


def list_subvolumes(path):
    """
    Return the subvolumes present in the btrfs file system containing
    ``path``.

    Subvolume paths are reported relative to the top level of the file
    system when ``path`` is a top level mount.

    :param path: A path within the btrfs file system to list.
    :returns: A dictionary mapping subvolume paths to dictionaries of
              subvolume report fields.
    :raises: ``SnapmCalloutError`` if the ``btrfs`` command fails.
    """
    subvolumes = {}
    report = _btrfs(
        BTRFS_SUBVOLUME,
        BTRFS_LIST,
        BTRFS_LIST_UUID,
        BTRFS_LIST_PARENT_UUID,
        path,
    )
    for line in report.splitlines():
        if not line.strip():
            continue
        try:
            subvolume = _parse_subvol_list_line(line)
        except ValueError:
            continue
        if subvolume is None:
            continue
        subvolumes[subvolume[SUBVOL_PATH]] = subvolume
    return subvolumes


def show_subvolume(path):
    """
    Return the properties of the subvolume at ``path``.

    :param path: The path of the subvolume to query.
    :returns: A dictionary mapping property names to values.
    :raises: ``SnapmCalloutError`` if the ``btrfs`` command fails.
    """
    properties = {}
    report = _btrfs(BTRFS_SUBVOLUME, BTRFS_SHOW, path)
    for line in report.splitlines():
        if ":" not in line:
            continue
        (key, _, value) = line.partition(":")
        properties[key.strip()] = value.strip()
    return properties


def subvolume_id(path):
    """
    Return the subvolume identifier of the subvolume at ``path``.

    :param path: The path of the subvolume to query.
    :returns: The numeric subvolume identifier.
    :raises: ``SnapmPluginError`` if the subvolume identifier cannot be
             determined.
    """
    properties = show_subvolume(path)
    try:
        return int(properties[SUBVOL_SHOW_ID])
    except (KeyError, ValueError) as err:
        raise SnapmPluginError(f"Could not determine subvolume ID for {path}") from err


def filter_btrfs_snapshot(subvolume):
    """
    Filter Btrfs snapshots.

    Return ``True`` if the subvolume represented by ``subvolume`` is a btrfs
    snapshot or ``False`` otherwise. The ``subvolume`` argument must be a
    dictionary representing the output of the ``btrfs subvolume list``
    reporting command.

    :param subvolume: The subvolume report fields to test.
    """
    parent_uuid = subvolume.get(SUBVOL_PARENT_UUID, BTRFS_NULL_FIELD)
    return parent_uuid != BTRFS_NULL_FIELD


def _strip_revert_suffix(subvol):
    """
    Return the origin subvolume path for a subvolume preserved by a revert
    operation.

    Paths that are not the result of a revert operation are returned
    unmodified.

    :param subvol: The subvolume path to strip.
    :returns: The corresponding origin subvolume path.
    """
    (base, sep, _) = subvol.rpartition(BTRFS_REVERT_SUFFIX)
    return base if sep else subvol


def _origin_from_parent_uuid(subvolumes, parent_uuid):
    """
    Return the origin subvolume path corresponding to ``parent_uuid``.

    Since a revert operation renames the origin subvolume to preserve its
    content, the parent of a snapshot taken before a revert is the preserved
    subvolume: the path of the current origin subvolume is returned in this
    case.

    :param subvolumes: A dictionary of subvolumes to search.
    :param parent_uuid: The parent UUID to find.
    :returns: The path of the matching origin subvolume, or ``None`` if the
              origin subvolume no longer exists.
    """
    for path, subvolume in subvolumes.items():
        if subvolume.get(SUBVOL_UUID) == parent_uuid:
            return _strip_revert_suffix(path)
    return None


def _snapshot_min_size(policy_size):
    """
    Return the minimum snapshot size given the space used by the snapshot
    mount point.

    This is somewhat meaningless for btrfs.

    :param policy_size: The size suggested by the in-use size policy.
    :returns: The greater of ``policy_size`` and ``MIN_BTRFS_SNAPSHOT_SIZE``.
    """
    return max(MIN_BTRFS_SNAPSHOT_SIZE, policy_size)


def _find_in_progress_revert(subvolumes, subvol):
    """
    Return a list containing any in-progress revert for the specified
    subvolume.

    A revert is in progress for ``subvol`` while the content the subvolume
    held before the revert is preserved as ``subvol.snapm-revert.TIMESTAMP``.

    :param subvolumes: A dictionary of subvolumes to search.
    :param subvol: The origin subvolume path to test.
    :returns: A list of preserved subvolume paths.
    """
    prefix = f"{subvol}{BTRFS_REVERT_SUFFIX}"
    return [path for path in subvolumes if path.startswith(prefix)]


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
    ):
        """
        Initialise a new ``BtrfsSnapshot`` object.

        :param name: The name of the snapshot.
        :param snapset_name: The name of the snapshot set this snapshot is
                             a part of.
        :param origin: The origin subvolume path relative to the file system
                       top level.
        :param timestamp: The creation timestamp of the snapshot set.
        :param mount_point: The mount point path this snapshot refers to.
        :param provider: The plugin providing this snapshot.
        :param what: The device backing the btrfs file system.
        :param subvol: The snapshot subvolume path relative to the file
                       system top level.
        :param subvolid: The snapshot subvolume identifier.
        """
        super().__init__(name, snapset_name, origin, timestamp, mount_point, provider)
        self.what = what
        self.subvol = subvol
        self.subvolid = subvolid

    def __str__(self):
        return "".join(
            [
                super().__str__(),
                f"\nDevice:         {self.what}",
                f"\nSubvolume:      {self.subvol}",
                f"\nSubvolumeID:    {self.subvolid}",
            ]
        )

    @property
    def origin(self):
        return format_btrfs_name(self.what, self._origin)

    @property
    def origin_options(self):
        """
        File system options needed to specify the origin of this snapshot.

        All subvolumes of a btrfs file system share a single block device:
        the origin subvolume is selected with a ``subvol=`` mount option.
        """
        return f"{BTRFS_SUBVOL_OPTION}=/{self._origin}"

    @property
    def snapshot_options(self):
        """
        File system options needed to specify this snapshot.

        All subvolumes of a btrfs file system share a single block device:
        the snapshot subvolume is selected with a ``subvol=`` mount option.
        """
        return f"{BTRFS_SUBVOL_OPTION}=/{self.subvol}"

    @property
    def devpath(self):
        """
        The device path for this snapshot.

        Btrfs subvolumes have no device of their own: mounting or booting
        this snapshot requires ``snapshot_options`` in addition to the
        device path.
        """
        return self.what

    @property
    def status(self):
        subvolumes = self.provider.fs_info(self.what).subvolumes
        if self.subvol not in subvolumes:
            return SnapStatus.INVALID
        if _find_in_progress_revert(subvolumes, self._origin):
            return SnapStatus.REVERTING
        return SnapStatus.ACTIVE

    @property
    def size(self):
        """
        The size of the btrfs file system containing this snapshot.

        Btrfs snapshots share extents with their origin subvolume and have
        no size of their own.
        """
        return self.provider.fs_info(self.what).size

    @property
    def free(self):
        """
        The space available in the btrfs file system containing this
        snapshot.
        """
        return self.provider.fs_info(self.what).free

    # Pylint does not understand the decorator notation.
    # pylint: disable=invalid-overridden-method
    @Snapshot.autoactivate.getter
    def autoactivate(self):
        # Btrfs subvolumes always activate with the main file system
        return True

    def _subvol_mounted(self, subvol):
        """
        Test whether the subvolume ``subvol`` of this snapshot's file system
        is currently mounted.

        :param subvol: The subvolume path to test.
        :returns: ``True`` if the subvolume is mounted or ``False``
                  otherwise.
        """
        for mount in btrfs_mounts():
            if not _same_device(mount.device, self.what):
                continue
            if mount.subvol == subvol:
                return True
        return False

    @property
    def origin_mounted(self):
        """
        Test whether the origin subvolume for this ``BtrfsSnapshot`` is
        currently mounted and in use.

        Overrides ``Snapshot.origin_mounted``: every subvolume of a btrfs
        file system shares one block device, so the mounted subvolume must
        be compared as well as the device.

        :returns: ``True`` if this snapshot's origin is currently mounted
                  or ``False`` otherwise.
        """
        return self._subvol_mounted(self._origin)

    @property
    def snapshot_mounted(self):
        """
        Test whether this ``BtrfsSnapshot`` is currently mounted and in use.

        Overrides ``Snapshot.snapshot_mounted``: every subvolume of a btrfs
        file system shares one block device, so the mounted subvolume must
        be compared as well as the device.

        :returns: ``True`` if this snapshot is currently mounted or ``False``
                  otherwise.
        """
        if self.status != SnapStatus.ACTIVE:
            return False
        return self._subvol_mounted(self.subvol)

    def invalidate_cache(self):
        self.provider.invalidate_fs_cache(self.what)


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
        :raises: ``SnapmNotFoundError`` if the btrfs commands are not
                 installed or ``SnapmPluginError`` if the installed version
                 of btrfs-progs is not supported.
        """
        super().__init__(logger, plugin_cfg)
        self.origins = {}
        self.filesystems = {}
        self._fs_cache = {}

        self._check_btrfs_present()
        self._check_btrfs_version()

        if self.priority == PLUGIN_NO_PRIORITY:
            self.priority = BTRFS_STATIC_PRIORITY

    def _check_btrfs_present(self):
        """
        Check for the presence of the commands required by the plugin.

        :raises: ``SnapmNotFoundError`` if required dependencies are not
                 found.
        """
        if all(which(cmd) for cmd in _BTRFS_CMDS):
            return
        if btrfs_devices_present():
            self._log_warn(
                "Btrfs file systems present but btrfs-progs is not installed"
            )
            self._log_warn(
                "Install btrfs-progs to manage snapshots for btrfs file systems"
            )
        raise SnapmNotFoundError("Btrfs commands not found")

    def _check_btrfs_version(self):
        """
        Check for the required minimum btrfs-progs version.

        :raises: ``SnapmPluginError`` if the installed version of btrfs-progs
                 is older than ``MINIMUM_BTRFS_VERSION``.
        """

        def _version_string(value):
            return ".".join(str(part) for part in value)

        version = _btrfs(BTRFS_VERSION).splitlines()[0].split()[-1]
        try:
            parts = [int(part) for part in version.lstrip("v").split("-")[0].split(".")]
        except ValueError as err:
            raise SnapmPluginError(
                f"Could not parse btrfs-progs version: {version}"
            ) from err

        # btrfs-progs versions may omit trailing components: pad the parsed
        # value so that comparisons with MINIMUM_BTRFS_VERSION are valid.
        parts += [0] * (len(MINIMUM_BTRFS_VERSION) - len(parts))
        btrfs_version = tuple(parts[: len(MINIMUM_BTRFS_VERSION)])

        if btrfs_version < MINIMUM_BTRFS_VERSION:
            raise SnapmPluginError(
                f"Unsupported btrfs-progs version: {_version_string(btrfs_version)} "
                f"< {_version_string(MINIMUM_BTRFS_VERSION)}"
            )

    @contextmanager
    def _top_level(self, device):
        """
        Context manager yielding a path to the top level of the btrfs file
        system on ``device``.

        An existing top level mount is used if one is present. Otherwise the
        file system is temporarily mounted beneath ``BTRFS_MOUNT_DIR`` for
        the duration of the operation.

        :param device: The device backing the btrfs file system.
        :returns: A path to the file system top level.
        :raises: ``SnapmCalloutError`` if the file system cannot be mounted.
        """
        mount = _find_top_level_mount(device)
        if mount is not None:
            self._log_debug(
                "Using top level mount %s for %s", mount.mount_point, device
            )
            yield mount.mount_point
            return

        makedirs(BTRFS_MOUNT_DIR, mode=0o700, exist_ok=True)
        path = mkdtemp(dir=BTRFS_MOUNT_DIR, prefix=BTRFS_MOUNT_PREFIX)
        self._log_debug("Mounting %s top level at %s", device, path)
        try:
            _mount(device, path, f"{BTRFS_SUBVOLID_OPTION}={BTRFS_TOPLEVEL_SUBVOLID}")
        except SnapmCalloutError:
            rmdir(path)
            raise
        try:
            yield path
        finally:
            try:
                _umount(path)
                rmdir(path)
            except (SnapmCalloutError, OSError) as err:
                self._log_warn("Failed to clean up top level mount %s: %s", path, err)

    def invalidate_fs_cache(self, device=None):
        """
        Invalidate cached file system data.

        :param device: The device to invalidate data for, or ``None`` to
                       invalidate data for all file systems.
        """
        if device is None:
            self._fs_cache.clear()
        else:
            self._fs_cache.pop(device, None)

    def fs_info(self, device):
        """
        Return file system data for the btrfs file system on ``device``.

        Data is cached for ``BTRFS_CACHE_VALID`` seconds and is shared by all
        snapshots of the file system.

        :param device: The device backing the btrfs file system.
        :returns: A ``BtrfsFsInfo`` tuple describing the file system.
        """
        cached = self._fs_cache.get(device)
        if cached is not None and (cached.timestamp + BTRFS_CACHE_VALID) > time():
            return cached

        with self._top_level(device) as top_level:
            subvolumes = list_subvolumes(top_level)
            stats = statvfs(top_level)

        info = BtrfsFsInfo(
            time(),
            subvolumes,
            stats.f_blocks * stats.f_frsize,
            stats.f_bavail * stats.f_frsize,
        )
        self._fs_cache[device] = info
        return info

    def _discover_fs_snapshots(self, device):
        """
        Discover snapshots in the btrfs file system on ``device``.

        :param device: The device backing the btrfs file system.
        :returns: A list of ``BtrfsSnapshot`` objects.
        """
        snapshots = []
        subvolumes = self.fs_info(device).subvolumes

        for subvol, subvolume in subvolumes.items():
            if not filter_btrfs_snapshot(subvolume):
                continue

            origin = _origin_from_parent_uuid(subvolumes, subvolume[SUBVOL_PARENT_UUID])
            if origin is None:
                continue

            try:
                fields = parse_snapshot_name(basename(subvol), basename(origin))
            except ValueError:
                continue
            if fields is None:
                continue

            (snapset, timestamp, mount_point) = fields
            full_name = format_btrfs_name(device, subvol)
            self._log_debug("Found %s snapshot: %s", self.name, full_name)
            snapshots.append(
                BtrfsSnapshot(
                    full_name,
                    snapset,
                    origin,
                    timestamp,
                    mount_point,
                    self,
                    device,
                    subvol,
                    subvolume[SUBVOL_ID],
                )
            )
        return snapshots

    def discover_snapshots(self):
        """
        Discover snapshots managed by this plugin class.

        Returns a list of objects that are a subclass of ``Snapshot``.
        """
        snapshots = []

        for device in btrfs_devices():
            try:
                snapshots.extend(self._discover_fs_snapshots(device))
            except (SnapmCalloutError, SnapmPluginError) as err:
                self._log_warn("Failed to discover snapshots for %s: %s", device, err)

        for snapshot in snapshots:
            self.origins[snapshot.origin] = self.origins.get(snapshot.origin, 0) + 1
            self.filesystems[snapshot.what] = self.filesystems.get(snapshot.what, 0) + 1

        return snapshots

    def can_snapshot(self, source):
        """
        Test whether this plugin can snapshot the specified mount point.

        Btrfs sources must be given as a mount point: the subvolume to be
        snapshotted cannot be determined from a block device path alone.

        :param source: The mount point path to test.
        :returns: ``True`` if this plugin can snapshot the file system mounted
                  at ``mount_point``, or ``False`` otherwise.
        :raises: ``SnapmRecursionError`` if ``source`` is itself a snapshot
                 managed by this plugin.
        """
        if S_ISBLK(stat(source).st_mode):
            if is_btrfs_device(source):
                self._log_warn(
                    "Btrfs sources must be specified as a mount point: %s", source
                )
            return False

        mount = mount_for_path(source)
        if mount is None:
            return False
        if not is_btrfs_device(mount.device):
            return False

        # The origin cannot be a snapshot managed by this plugin: the manager
        # cannot detect this via Snapshot.devpath since all subvolumes of a
        # btrfs file system share a single device.
        device = realpath(mount.device)
        for snapshot in self._discover_fs_snapshots(device):
            if snapshot.subvol == mount.subvol:
                raise SnapmRecursionError(
                    f"Cannot snapshot {source}: subvolume {mount.subvol} is a "
                    f"snapshot belonging to snapshot set '{snapshot.snapset_name}'"
                )
        return True

    def _check_free_space(self, origin, mount_point, size_policy):
        """
        Check for available space in the file system backing ``origin`` for
        the specified mount point.

        Btrfs snapshots share extents with their origin subvolume and consume
        no space when created: the size policy is applied as a check against
        the space available in the file system.

        :param origin: The origin identifier to check.
        :param mount_point: The mount point path to check.
        :param size_policy: The size policy to be applied.
        :returns: The minimum size required for the snapshot.
        :raises: ``SnapmNoSpaceError`` if the minimum snapshot size exceeds the
                 available space.
        """
        (device, _) = device_subvol_from_origin(origin)
        info = self.fs_info(device)
        fs_used = mount_point_space_used(mount_point)
        policy = SizePolicy(
            origin, mount_point, info.free, fs_used, info.size, size_policy
        )
        snapshot_min_size = _snapshot_min_size(policy.size)
        used = sum(self.size_map[device].values())
        if info.free < (used + snapshot_min_size):
            raise SnapmNoSpaceError(
                f"Btrfs file system {device} has insufficient free space to "
                f"snapshot {mount_point} "
                f"({size_fmt(info.free)} < {size_fmt(used + snapshot_min_size)})"
            )
        return snapshot_min_size

    def _check_origin_limits(self, origin: str) -> bool:
        """
        Check ``origin`` against configured plugin limits: return ``True`` if
        adding a new snapshot of this origin would exceed limits, and
        ``False`` otherwise.

        :param origin: The origin subvolume to check.
        :type origin: ``str``
        :returns: ``True`` if adding a new snapshot would exceed limits, or
                 ``False`` otherwise.
        :rtype: ``bool``
        """
        if not self.limits.snapshots_per_origin:
            return False
        if origin not in self.origins:
            return False
        return self.origins[origin] + 1 > self.limits.snapshots_per_origin

    def _check_limits(self, pool: str) -> bool:
        """
        Check ``pool`` against configured plugin limits: return ``True`` if
        adding a new snapshot of this pool would exceed limits, and ``False``
        otherwise.

        The btrfs file system containing the origin subvolume is treated as
        the pool for the purposes of plugin limits.

        :param pool: The device backing the file system to check.
        :type pool: ``str``
        :returns: ``True`` if adding a new snapshot would exceed limits, or
                 ``False`` otherwise.
        :rtype: ``bool``
        """
        if not self.limits.snapshots_per_pool:
            return False
        if pool not in self.filesystems:
            return False
        return self.filesystems[pool] + 1 > self.limits.snapshots_per_pool

    def _snapshot_name(self, origin_subvol, snapset_name, timestamp, mount_point):
        """
        Return the subvolume name for a new snapshot.

        :param origin_subvol: The origin subvolume path.
        :param snapset_name: The name of the snapshot set.
        :param timestamp: The snapshot set timestamp.
        :param mount_point: The mount point path for this snapshot.
        :returns: The name of the snapshot subvolume to create.
        :raises: ``SnapmInvalidIdentifierError`` if the resulting name exceeds
                 btrfs limits.
        """
        snapshot_name = format_snapshot_name(
            basename(origin_subvol),
            snapset_name,
            timestamp,
            encode_mount_point(mount_point),
        )
        _check_subvol_name(snapshot_name)
        return snapshot_name

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
        (device, subvol) = device_subvol_from_origin(origin)
        self._snapshot_name(subvol, snapset_name, timestamp, mount_point)

        if device not in self.size_map:
            self.size_map[device] = {}
        self.size_map[device][subvol] = self._check_free_space(
            origin, mount_point, size_policy
        )

        if self._check_origin_limits(origin):
            raise SnapmLimitError(
                f"Adding snapshot of {mount_point} would exceed MaxSnapshotsPerOrigin "
                f"for {origin} ({self.limits.snapshots_per_origin})"
            )
        if self._check_limits(device):
            raise SnapmLimitError(
                f"Adding snapshot of {mount_point} would exceed MaxSnapshotsPerPool "
                f"for {device} ({self.limits.snapshots_per_pool})"
            )

    # pylint: disable=too-many-arguments
    def create_snapshot(
        self, origin, snapset_name, timestamp, mount_point, size_policy
    ):
        """
        Create a snapshot of ``origin`` in the snapset named ``snapset_name``.

        The snapshot is created as a read-write subvolume at the top level of
        the btrfs file system containing the origin subvolume so that it can
        be mounted, or booted, with a ``subvol=`` mount option.

        :param origin: The origin volume for the snapshot.
        :param snapset_name: The name of the snapshot set to be created.
        :param timestamp: The snapshot set timestamp.
        :param mount_point: The mount point path for this snapshot.
        :raises: ``SnapmNoSpaceError`` if there is insufficient free space to
                 create the snapshot.
        """
        (device, subvol) = device_subvol_from_origin(origin)
        snapshot_name = self._snapshot_name(
            subvol, snapset_name, timestamp, mount_point
        )

        self._check_free_space(origin, mount_point, size_policy)

        self._log_debug(
            "Creating Btrfs snapshot for %s:%s mounted at %s",
            device,
            subvol,
            mount_point,
        )

        with self._top_level(device) as top_level:
            snapshot_path = path_join(top_level, snapshot_name)
            if path_exists(snapshot_path):
                raise SnapmPluginError(f"Subvolume {snapshot_name} already exists")
            _btrfs(
                BTRFS_SUBVOLUME,
                BTRFS_SNAPSHOT,
                path_join(top_level, subvol),
                snapshot_path,
            )
            subvolid = subvolume_id(snapshot_path)

        self.invalidate_fs_cache(device)

        self.origins[origin] = self.origins.get(origin, 0) + 1
        self.filesystems[device] = self.filesystems.get(device, 0) + 1

        return BtrfsSnapshot(
            format_btrfs_name(device, snapshot_name),
            snapset_name,
            subvol,
            timestamp,
            mount_point,
            self,
            device,
            snapshot_name,
            subvolid,
        )

    def origin_from_mount_point(self, mount_point):
        """
        Return a string representing the origin from a given mount point path.

        :param mount_point: The mount point path.
        :returns: An origin identifier of the form ``device:subvolume``, or
                  ``None`` if ``mount_point`` is not a btrfs mount point.
        """
        mount = mount_for_path(mount_point)
        if mount is None or not is_btrfs_device(mount.device):
            return None
        return format_btrfs_name(realpath(mount.device), mount.subvol)

    def delete_snapshot(self, name):
        """
        Delete the snapshot named ``name``

        :param name: The name of the snapshot to be removed.
        """
        (device, subvol) = device_subvol_from_name(name)
        self._log_debug("Deleting %s snapshot %s", self.name, name)
        with self._top_level(device) as top_level:
            _btrfs(BTRFS_SUBVOLUME, BTRFS_DELETE, path_join(top_level, subvol))
        self.invalidate_fs_cache(device)

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
        (device, old_subvol) = device_subvol_from_name(old_name)
        (_, origin_subvol) = device_subvol_from_origin(origin)
        new_subvol = path_join(
            dirname(old_subvol),
            self._snapshot_name(origin_subvol, snapset_name, timestamp, mount_point),
        )

        self._log_debug(
            "Renaming snapshot from %s to %s",
            old_subvol,
            new_subvol,
        )

        with self._top_level(device) as top_level:
            new_path = path_join(top_level, new_subvol)
            if path_exists(new_path):
                raise SnapmPluginError(f"Subvolume {new_subvol} already exists")
            try:
                rename(path_join(top_level, old_subvol), new_path)
            except OSError as err:
                raise SnapmPluginError(
                    f"Could not rename subvolume {old_subvol} to {new_subvol}: {err}"
                ) from err
            subvolid = subvolume_id(new_path)

        self.invalidate_fs_cache(device)

        return BtrfsSnapshot(
            format_btrfs_name(device, new_subvol),
            snapset_name,
            origin_subvol,
            timestamp,
            mount_point,
            self,
            device,
            new_subvol,
            subvolid,
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
        (device, subvol) = device_subvol_from_origin(origin)
        if device not in self.size_map:
            self.size_map[device] = {}
        self.size_map[device][subvol] = self._check_free_space(
            origin, mount_point, size_policy
        )

    def resize_snapshot(self, name, origin, mount_point, size_policy):
        """
        Perform any necessary resize operation on this snapshot. Since Btrfs
        snapshots share extents with their origin subvolume and allocate space
        dynamically from the file system this is a no-op for Btrfs snapshots.
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
        (device, origin_subvol) = device_subvol_from_origin(origin)
        subvolumes = self.fs_info(device).subvolumes

        if origin_subvol not in subvolumes:
            raise SnapmPluginError(
                f"Origin subvolume {origin_subvol} not found in {device}"
            )

        if _find_in_progress_revert(subvolumes, origin_subvol):
            raise SnapmBusyError(
                f"Snapshot revert is in progress for {name} origin subvolume "
                f"{origin_subvol}"
            )

    def revert_snapshot(self, name):
        """
        Revert the state of the content of the origin to the content at the
        time the snapshot was taken.

        Btrfs has no equivalent of an LVM2 snapshot merge: the origin
        subvolume is renamed to preserve its current content and a new
        read-write snapshot of ``name`` is created in its place. Since the
        subvolume is selected at mount time with ``subvol=`` the revert takes
        effect at the next mount of the origin subvolume (typically a reboot
        into the revert boot entry for the snapshot set).

        The preserved subvolume marks the revert as in-progress: it must be
        removed once the revert has been verified.

        :param name: The name of the snapshot to revert.
        """
        (device, subvol) = device_subvol_from_name(name)

        with self._top_level(device) as top_level:
            subvolumes = list_subvolumes(top_level)

            if subvol not in subvolumes:
                raise SnapmNotFoundError(f"Snapshot subvolume {subvol} not found")

            origin_subvol = _origin_from_parent_uuid(
                subvolumes, subvolumes[subvol][SUBVOL_PARENT_UUID]
            )
            if origin_subvol is None:
                raise SnapmPluginError(
                    f"Could not find origin subvolume for snapshot {name}"
                )

            if _find_in_progress_revert(subvolumes, origin_subvol):
                raise SnapmBusyError(
                    f"Snapshot revert is in progress for {name} origin subvolume "
                    f"{origin_subvol}"
                )

            revert_subvol = f"{origin_subvol}{BTRFS_REVERT_SUFFIX}{int(time())}"
            _check_subvol_name(revert_subvol)

            origin_path = path_join(top_level, origin_subvol)
            revert_path = path_join(top_level, revert_subvol)

            self._log_debug(
                "Preserving origin subvolume %s as %s", origin_subvol, revert_subvol
            )
            try:
                rename(origin_path, revert_path)
            except OSError as err:
                raise SnapmPluginError(
                    f"Could not rename subvolume {origin_subvol} to {revert_subvol}: {err}"
                ) from err

            self._log_debug("Reverting %s to %s", origin_subvol, subvol)
            try:
                _btrfs(
                    BTRFS_SUBVOLUME,
                    BTRFS_SNAPSHOT,
                    path_join(top_level, subvol),
                    origin_path,
                )
            except SnapmCalloutError:
                rename(revert_path, origin_path)
                raise

        self.invalidate_fs_cache(device)

        self._log_info(
            "Reverted subvolume %s: previous content is preserved as %s",
            origin_subvol,
            revert_subvol,
        )

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
