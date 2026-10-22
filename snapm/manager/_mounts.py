# Copyright Red Hat
#
# snapm/manager/_mounts.py - Snapshot Manager mount support
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Mount integration for snapshot manager
"""
from subprocess import run, CalledProcessError, TimeoutExpired
from typing import Dict, Iterable, List, Optional, Union
from abc import ABC, abstractmethod
from stat import S_ISDIR, S_ISLNK, S_IWGRP, S_IWOTH
import collections
import logging
import shlex
import os.path
import os

from snapm import (
    SNAPM_SUBSYSTEM_MOUNTS,
    SnapmError,
    SnapmNotFoundError,
    SnapmCalloutError,
    SnapmPathError,
    SnapmArgumentError,
    SnapmMountError,
    SnapmUmountError,
    Selection,
    SnapshotSet,
    MountInfoReader,
    select_snapshot_set,
    device_number,
    FsTabReader,
    unescape_mounts,
    get_device_fstype,
    resolve_device_spec,
    find_snapset_root,
    build_snapset_mount_list,
)

from ._mountstate import (
    MOUNT_STATE_LOCK_TIMEOUT,
    MountStateDir,
    MountStateRecord,
    mount_state_lock,
)

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


def _log_debug_mounts(msg, *args, **kwargs):
    """A wrapper for mounts subsystem debug logs."""
    _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_MOUNTS}, **kwargs)


#: Path to /proc/self/mounts
PROC_MOUNTS = "/proc/self/mounts"

#: List of API file systems to rbind if present and a mount point.
_BIND_MOUNTS: List[str] = [
    "/dev",
    "/proc",
    "/run",
    "/sys",
]

#: Timeout for mount helper programs
_SNAPM_MOUNT_HELPER_TIMEOUT = int(os.getenv("SNAPM_MOUNT_TIMEOUT", "60"))

#: Path to container overlay bind mount
_CONTAINERS_OVERLAY = "/var/lib/containers/storage/overlay"

#: Mount option marking a mount as one snapm made (#1027)
_SNAPM_MANAGED_OPTION = "x-snapm.managed"

#: Taken from kernel source: fs/xfs/libxfs/xfs_log_format.h:
_XFS_UQUOTA_ACCT = 0x0001  # user quota accounting ON
_XFS_UQUOTA_ENFD = 0x0002  # user quota limits enforced
_XFS_GQUOTA_ACCT = 0x0040  # group quota accounting ON
_XFS_GQUOTA_ENFD = 0x0080  # group quota limits enforced
_XFS_PQUOTA_ACCT = 0x0008  # project quota accounting ON
_XFS_PQUOTA_ENFD = 0x0200  # project quota limits enforced

#: Prefix for the output of the "print qflags" command.
_QFLAGS_PREFIX = "qflags = "


def _sanitize_environment():
    """
    Sanitize the environment for callouts.

    :returns: An environment dictionary stripped of locale related keys
              and with "LC_ALL=C".
    :rtype: ``Dict[str, str]``
    """
    _env_filter_keys = (
        "LANG",
        "LC_CTYPE",
        "LC_NUMERIC",
        "LC_TIME",
        "LC_COLLATE",
        "LC_MONETARY",
        "LC_MESSAGES",
        "LC_PAPER",
        "LC_NAME",
        "LC_ADDRESS",
        "LC_TELEPHONE",
        "LC_MEASUREMENT",
        "LC_IDENTIFICATION",
        "LC_ALL",
    )
    return {"LC_ALL": "C"} | {
        k: v for k, v in os.environ.items() if k not in _env_filter_keys
    }


#: Environment for mount callouts
_CALLOUT_ENV = _sanitize_environment()


# pylint: disable=too-many-branches
def _get_xfs_quota_options(devpath: str) -> str:
    """
    Return appropriate XFS quota mount options for the file system contained
    on the block device at `devpath`.

    These must be passed to avoid destroying any existing quota information:

        https://github.com/lvmteam/lvm2/issues/182
        https://github.com/storaged-project/libblockdev/pull/1132

    :param devpath: The path to the block device to examine.
    :returns: A comma-separated string list of mount options.
    :rtype: `str`
    """
    xfs_db_cmd = ["xfs_db", "-r", devpath, "-c", "sb 0", "-c", "print qflags"]
    try:
        result = run(
            xfs_db_cmd,
            env=_CALLOUT_ENV,
            check=True,
            capture_output=True,
            encoding="utf8",
            timeout=_SNAPM_MOUNT_HELPER_TIMEOUT,
        )
    except FileNotFoundError as err:
        raise SnapmCalloutError(
            f"xfs_db not found while examining '{devpath}': {err}"
        ) from err
    except TimeoutExpired as err:
        raise SnapmCalloutError(
            f"Timed out obtaining XFS quota flags for '{devpath}': {err}"
        ) from err
    except CalledProcessError as err:
        raise SnapmCalloutError(
            f"Failed to obtain XFS quota flags value for '{devpath}': {err.stderr}"
        ) from err

    qflags_str = result.stdout.strip()

    def _malformed_qflags():
        return SnapmCalloutError(
            f"Malformed qflags value from xfs_db for '{devpath}': {qflags_str}"
        )

    if not qflags_str.startswith(_QFLAGS_PREFIX):
        raise _malformed_qflags()

    try:
        qflags = int(qflags_str.removeprefix(_QFLAGS_PREFIX), 0)
    except ValueError as err:
        raise _malformed_qflags() from err

    options = []

    if qflags & _XFS_UQUOTA_ACCT:
        if qflags & _XFS_UQUOTA_ENFD:
            options.append("uquota")
        else:
            options.append("uqnoenforce")
    if qflags & _XFS_GQUOTA_ACCT:
        if qflags & _XFS_GQUOTA_ENFD:
            options.append("gquota")
        else:
            options.append("gqnoenforce")
    if qflags & _XFS_PQUOTA_ACCT:
        if qflags & _XFS_PQUOTA_ENFD:
            options.append("pquota")
        else:
            options.append("pqnoenforce")
    return ",".join(options)


def managed_option(snapset: SnapshotSet) -> str:
    """
    Return the mount option marking a mount as made by snapm for the
    snapshot set ``snapset``.

    The option is in the "x-" namespace, so the kernel never sees it:
    mount(8) keeps it in /run/mount/utab and findmnt(8) reports it
    alongside the kernel's own options. It names the snapshot set that is
    mounted, and makes every mount snapm made findable wherever it is::

        # findmnt -m -o TARGET,OPTIONS -O x-snapm.managed

    :param snapset: The snapshot set being mounted.
    :type snapset: ``SnapshotSet``
    :returns: The mount option marking a mount of ``snapset``.
    :rtype: ``str``
    """
    return f"{_SNAPM_MANAGED_OPTION}={snapset.uuid}"


def _merge_options(opts_a, opts_b):
    """
    Merge two comma-separated mount options strings.

    :param opts_a: The first set of options.
    :param opts_b: The second set of options.
    :returns: Merged "opts_a,opts_b"
    :rtype: ``str``
    """
    return ",".join(filter(None, [opts_a, opts_b]))


def _get_xfs_options(devpath: str) -> str:
    """
    Return appropriate options for mounting an XFS file system, including
    specifying "nouuid" to avoid UUID clashes, and appropriate quota flags
    for the current file system state.

    :param devpath: The path to the block device to examine.
    :returns: A comma-separated string list of mount options.
    :rtype: `str`
    """

    # Always use "nouuid" when mounting XFS.
    options = "nouuid"

    return _merge_options(options, _get_xfs_quota_options(devpath))


def _mount(
    what: str,
    where: str,
    fstype: Optional[str] = None,
    options: str = "defaults",
    readonly: bool = False,
    rbind: bool = False,
    rprivate: bool = False,
    unbindable: bool = False,
):
    """
    Call the mount program to mount a file system.

    :param what: The source for the mount operation.
    :param where: The path to the mount point.
    :param fstype: An optional file system type.
    :param options: Options to pass to the mount program.
    :param readonly: Mount the filesystem read-only.
    :param rbind: Perform a recursive bind mount.
    :param rprivate: Make the mount point private recursively.
    :param unbindable: Make the mount point unbindable.
    """
    mount_cmd = ["mount"]

    if readonly:
        if options in ("defaults", ""):
            options = "ro"
        else:
            options = _merge_options(options, "ro")

    what = resolve_device_spec(what, raise_on_error=True)

    if not rbind and not fstype:
        fstype = get_device_fstype(what)

    if fstype and fstype == "xfs":
        options = ",".join(filter(None, [options, _get_xfs_options(what)]))
    if fstype:
        mount_cmd.extend(["--type", fstype])
    if rbind:
        mount_cmd.append("--rbind")

    mount_cmd.extend(["--options", options, what, where])
    _run_mount(mount_cmd, what, where)

    # Change the propagation type with a second call to mount(8). Given on
    # the same command line as the mount, a "--make-*" flag is applied by a
    # second mount(2) that rewrites the userspace mount table entry, taking
    # the "x-" options recorded with the mount out with it (#1027).
    for flag in filter(
        None,
        [
            "--make-rprivate" if rprivate else None,
            "--make-unbindable" if unbindable else None,
        ],
    ):
        _run_mount(["mount", flag, where], what, where)


def _run_mount(mount_cmd: List[str], what: str, where: str):
    """
    Call the mount program and turn its failures into snapm exceptions.

    :param mount_cmd: The mount command line to run.
    :param what: The source of the mount operation, for error reporting.
    :param where: The mount point, for error reporting.
    """
    _log_debug_mounts("Calling %s", " ".join(mount_cmd))
    try:
        run(
            mount_cmd,
            env=_CALLOUT_ENV,
            check=True,
            capture_output=True,
            encoding="utf8",
            timeout=_SNAPM_MOUNT_HELPER_TIMEOUT,
        )
    except TimeoutExpired as err:
        raise SnapmCalloutError(
            f"Timed out calling mount for {what} -> {where}: {err}"
        ) from err
    except CalledProcessError as err:
        raise SnapmMountError(what, where, err.returncode, err.stderr.strip()) from err


def _umount(where: str):
    """
    Call the umount program to unmount a file system.

    :param where: The mount point to be unmounted.
    """
    umount_cmd = ["umount", where]
    _log_debug_mounts("Calling %s", " ".join(umount_cmd))
    try:
        run(
            umount_cmd,
            env=_CALLOUT_ENV,
            check=True,
            capture_output=True,
            encoding="utf8",
            timeout=_SNAPM_MOUNT_HELPER_TIMEOUT,
        )
    except TimeoutExpired as err:
        raise SnapmCalloutError(f"Timed out calling umount for {where}: {err}") from err
    except CalledProcessError as err:
        raise SnapmUmountError(where, err.returncode, err.stderr.strip()) from err


class ProcMountsReader:
    """Reader for /proc/mounts format files."""

    # Define a named tuple to give structure to each /proc/mounts entry.
    MountsEntry = collections.namedtuple(
        "MountsEntry", ["what", "where", "fstype", "options", "freq", "passno"]
    )

    def __init__(self, path=PROC_MOUNTS):
        """Initialize with the path to a mounts file.

        :param path: Path to the mounts file (e.g., '/proc/mounts')
        """
        self.path = path

    @property
    def entries(self):
        """Iterate over all entries in the mounts file.

        :returns: Yields ``MountsEntry`` objects for each well-formed line.
        """
        with open(self.path, "r", encoding="utf8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue

                parts = line.split()
                if len(parts) == 6:
                    # Unescape the device and mount point paths
                    # /proc/mounts uses octal escape sequences for special characters
                    what = unescape_mounts(parts[0])
                    where = unescape_mounts(parts[1])

                    # Create entry with unescaped paths
                    yield self.MountsEntry(what, where, *parts[2:])
                else:
                    _log_warn("Skipping malformed %s line: %s", self.path, line)

    def lookup(self, key, value):
        """
        Find and generate all entries matching a specific key-value pair.

        :param key: The field to search by. Must be one of 'what', 'where',
                    'fstype', 'options', 'freq', or 'passno'.
        :type key: str
        :param value: The value to match for the given key.
        :type value: str|int
        :yields: A ``MountsEntry`` for each matching mounts entry.

        :raises KeyError: If the provided key is not a valid mounts field name.
        """
        if key not in self.MountsEntry._fields:
            raise KeyError(
                f"Invalid lookup key: '{key}'. "
                f"Valid keys are: {self.MountsEntry._fields}"
            )

        for entry in self.entries:
            if getattr(entry, key) == value:
                yield entry

    def submounts(self, root):
        """Iterate over submounts under the given mount point root.

        :param root: The mount point root (e.g., '/run/snapm/mounts/before-upgrade')
        :returns: Yields ``MountsEntry`` objects for submounts under root.
        """
        root_prefix = root.rstrip("/") + "/"
        for entry in self.entries:
            if entry.where.startswith(root_prefix):
                yield entry


class MountBase(ABC):
    """
    An abstract base class representing a file system mount.
    """

    TYPE = "Base"  #: Description of the type of mount object.

    def __init__(self, mount_root: str, name: str):
        """
        Initialise base mount state.

        :param mount_root: The absolute path to the root directory of this
                           mount.
        :type mount_root: ``str``
        :raises SnapmPathError: If mount_root is not a directory.
        """
        if not os.path.isdir(mount_root):
            raise SnapmPathError(f"Mount path {mount_root} is not a directory.")

        self.root: str = mount_root
        self.name: str = name
        self.snapset: Optional[SnapshotSet] = None

    def _check_submounts(
        self,
        mount_points: Iterable[str],
        submounts: List[str],
        name_map: Optional[Dict[str, str]] = None,
    ):
        """
        Verify that the expected submounts (both device file systems and API
        file system mounts) are present under ``self.root``.

        :param mount_points: The list of expected device mount points to check.
        :type mount_points: ``Iterable[str]``
        :param submounts: A list of present submounts for this mount to be
                          checked against the expected device and API file
                          system mount points.
        :type submounts: ``List[str]``
        :param name_map: A map of mount points to device names for logging.
        :type name_map: ``Dict[str, str]``
        """
        # Check device and API file system submounts
        checked = {mp: False for mp in mount_points if mp != "/"}
        bind_checked = {mp: False for mp in _BIND_MOUNTS}

        # Don't try to check the overlay bind mount
        checked.pop(_CONTAINERS_OVERLAY, None)

        for abs_mount_path in submounts:
            rel_mount_path = (
                abs_mount_path.removeprefix(self.root)
                if self.root != "/"
                else abs_mount_path
            )
            path_is_mount = os.path.ismount(abs_mount_path)
            if rel_mount_path in checked:
                checked[rel_mount_path] = path_is_mount
            elif rel_mount_path in bind_checked:
                bind_checked[rel_mount_path] = path_is_mount

        if not all(checked.values()):
            _log_warn("Missing snapshot set submounts for %s:", self.name)
            for mount_point in [mp for mp in checked if not checked[mp]]:
                _log_warn(
                    "  Missing: %s (%s)",
                    mount_point,
                    name_map.get(mount_point, "??") if name_map else "??",
                )

        if not all(bind_checked.values()):
            _log_warn("Missing API file system submounts for %s:", self.name)
            for mount_point in [mp for mp in bind_checked if not bind_checked[mp]]:
                _log_warn("  Missing: %s", mount_point)

    @property
    def mounted(self):
        """
        Determine whether this snapshot set mount is currently mounted.
        """
        return os.path.ismount(self.root)

    def mount(self):
        """
        Mount the configured file system and its submounts at `self.root`.
        """
        if self.mounted:
            _log_info("%s %s already mounted at '%s'", self.TYPE, self.name, self.root)
            return
        self._do_mount()

    @abstractmethod
    def _do_mount(self):
        """
        Hook invoked to mount a tree.

        Subclasses implement mount behaviour such as mounting snapshots and
        bind mounting API and runtime file systems.
        """

    def umount(self):
        """
        Unmount the configured file system and its submounts from `self.root`.
        """
        if not self.mounted:
            _log_warn("%s mount at '%s' is not mounted", self.TYPE, self.root)
            return
        self._do_umount()

    @abstractmethod
    def _do_umount(self):
        """
        Hook invoked to unmount a tree.

        Subclasses implement unmount behaviour such as umounting snapshots and
        umounting API and runtime file systems.
        """

    @abstractmethod
    def _chroot(self):
        """
        Hook invoked to obtain an appropriate chroot command list for this
        mount.

        :returns: A command list including an appropriate chroot invocation
                  if necessary.
        :rtype: ``List[str]``
        """

    def exec(self, command: Union[str, Iterable[str]]) -> int:
        """
        Execute a ``command`` chrooted inside this mount namespace.

        We explicitly trust the caller-provided command since snapm requires
        root privileges for all operations.

        :param command: A command and its arguments, suitable for passing to
                        ``shlex.split()``.
        """
        if not self.mounted:
            raise SnapmPathError(
                f"{self.TYPE} {self.name} is not mounted at {self.root}"
            )
        if isinstance(command, str):
            try:
                cmd_args = shlex.split(command)
            except ValueError as err:
                raise SnapmArgumentError(
                    f"Cannot parse command string: {command}"
                ) from err
        else:
            cmd_args = list(command)

        chroot_cmd = list(self._chroot())

        chroot_cmd.extend(cmd_args)
        _log_debug_mounts(
            "Invoking snapshot set chroot command: %s", " ".join(chroot_cmd)
        )
        try:
            status = run(chroot_cmd, check=False)
        except FileNotFoundError as err:  # pragma: no cover
            _log_error("Failed to execute chroot command: %s", err)
            return 127
        return status.returncode


class Mount(MountBase):
    """
    Representation of a mounted snapshot set, including snapshot set mounts,
    non-snapshot mounts from /etc/fstab, and bind mounts for API file systems.
    """

    TYPE = "Snapshot set"

    def __init__(
        self,
        snapset: SnapshotSet,
        mount_root: str,
        discover: bool = False,
        root_device: Optional[str] = None,
    ):
        """
        Initialize a new Mount instance for the given snapshot set. If ``discover``
        is ``True`` validate that the ``mount_root`` is an existing mount point for
        the snapshot set and check for the presence of expected submounts.

        :param snapset: The snapshot set to mount or represent.
        :type snapset: ``SnapshotSet``
        :param mount_root: The absolute path to the root directory where the snapshot
                           set will be mounted or is already mounted.
        :type mount_root: ``str``
        :param discover: If True, validate that mount_root is an existing mount point
                         and discover its submounts. If False, prepare a new mount
                         object without validation.
        :type discover: ``bool``
        :param root_device: The device to mount at ``mount_root``. One is
                            looked up with ``find_snapset_root()`` when it is
                            ``None``.
        :type root_device: ``Optional[str]``
        :raises SnapmPathError: If mount_root is not a directory, or if discover is True
                                and mount_root is not a mount point.
        """
        super().__init__(mount_root, snapset.name)

        _log_debug("Building snapshot set Mount instance for %s", snapset.name)

        # The snapshot set that this mount belongs to.
        self.snapset: Optional[SnapshotSet] = snapset

        # The device to mount at self.root.
        self.root_device: Optional[str] = root_device

        if discover:
            if not os.path.ismount(mount_root):
                raise SnapmPathError(f"Mount path {mount_root} is not a mount point.")
            pmr = ProcMountsReader()
            submounts = [mnt.where for mnt in pmr.submounts(self.root)]
            submounts.sort(key=lambda mnt: mnt.count("/"), reverse=True)
            _log_debug_mounts("Submounts by depth (%s)", self.snapset.name)
            for submount in submounts:
                _log_debug_mounts(submount)

            name_map = {
                mp: self.snapset.snapshot_by_source(mp).name
                for mp in self.snapset.mount_points
            }
            self._check_submounts(
                self.snapset.mount_points, submounts, name_map=name_map
            )

    def _bind_api_filesystems(self, options: str):
        """
        Recursively bind mount the host's API file systems into this mount.

        :param options: Mount options to apply to each bind mount.
        :type options: ``str``
        """
        for what in _BIND_MOUNTS:
            if not os.path.exists(what) or not os.path.ismount(what):
                continue
            where = os.path.join(self.root, what.lstrip("/"))
            if not os.path.isdir(where):
                _log_warn(
                    "Bind mount point %s does not exist in snapshot set %s, skipping mount",
                    where,
                    self.snapset.name,
                )
                continue
            _mount(what, where, options=options, rbind=True, rprivate=True)

    def _rollback_mount(self, err: Exception):
        """
        Unmount whatever a failed mount operation left behind.

        :param err: The error that failed the mount operation.
        :type err: ``Exception``
        """
        _log_warn(
            "Mount operation failed for %s at %s: %s. Rolling back.",
            self.snapset.name,
            self.root,
            err,
        )
        # Discover all submounts and unmount in depth-first order.
        pmr = ProcMountsReader()
        rollback_mounts = [submount.where for submount in pmr.submounts(self.root)]
        rollback_mounts.append(self.root)  # Include root itself
        rollback_mounts.sort(key=lambda mp: mp.count("/"), reverse=True)

        for mount_path in rollback_mounts:
            if not os.path.ismount(mount_path):
                continue
            try:
                _umount(mount_path)
            except SnapmUmountError as cleanup_err:
                _log_warn("Failed to roll back mount '%s': %s", mount_path, cleanup_err)

    def _do_mount(self):
        """
        Mount the configured snapshot set and its submounts at `self.root`.
        """
        # Tag every mount in the tree so that an administrator can list the
        # mounts snapm made, wherever they are, with
        # "findmnt -O x-snapm.managed" (#1027).
        managed = managed_option(self.snapset)

        try:
            # 0. Initialise FsTabReader
            fstab = FsTabReader()

            # 1. Find and mount the snapshot set root filesystem and make it
            #    unbindable. The caller settles the root device when there is
            #    a provenance record to agree with (#1031).
            root_device = self.root_device or find_snapset_root(self.snapset, fstab)
            try:
                root_opts = next(fstab.lookup("where", "/")).options
            except StopIteration:
                _log_warn("No entry matching mount point '/' found in fstab")
                root_opts = "defaults"
            _mount(
                root_device,
                self.root,
                options=_merge_options(root_opts, managed),
                unbindable=True,
            )

            # 2. Build an auxiliary mount list from the SnapshotSet/FsTabReader.
            mount_list = build_snapset_mount_list(self.snapset, fstab)

            # 3. Mount the discovered auxiliary mount points.
            for mount_spec in mount_list:
                what, where, fstype, options = mount_spec
                where = os.path.join(self.root, where.lstrip("/"))
                if not os.path.isdir(where):
                    _log_warn(
                        "Mount point %s does not exist in snapshot set %s, skipping mount",
                        where,
                        self.snapset.name,
                    )
                    continue
                _mount(
                    what, where, fstype=fstype, options=_merge_options(options, managed)
                )

            # 4. Recursively bind mount API file systems from the host.
            self._bind_api_filesystems(managed)

        except (ValueError, SnapmError, StopIteration) as err:
            self._rollback_mount(err)
            raise

    def _do_umount(self):
        """
        Unmount the configured snapshot set and its submounts from `self.root`.
        """
        # 1. Discover and unmount each submount under self.root
        pmr = ProcMountsReader()
        submounts = [mnt.where for mnt in pmr.submounts(self.root)]
        submounts.sort(key=lambda mnt: mnt.count("/"), reverse=True)

        # Work around #586
        failed_netns_mounts = []

        for submount in submounts:
            _log_debug_mounts(
                "Attempting to unmount %s (%s)", submount, self.snapset.name
            )
            try:
                if submount == os.path.join(self.root, "run") and failed_netns_mounts:
                    for netns_mount in failed_netns_mounts.copy():
                        try:
                            _umount(netns_mount)
                            failed_netns_mounts.remove(netns_mount)
                        except SnapmUmountError as err:
                            _log_warn(
                                "Unable to unmount %s after retry: %s", netns_mount, err
                            )
                _umount(submount)
            except SnapmUmountError as err:
                if "/run/netns/netns-" in submount and "not mounted" in err.stderr:
                    failed_netns_mounts.append(submount)
                else:
                    raise

        # 1b. Warn if any netns submounts failed to unmount
        if failed_netns_mounts:
            _log_warn(
                "Failed to remove all netns submounts, manual cleanup required: %s",
                ", ".join(failed_netns_mounts),
            )  # pragma: no cover

        # 2. Unmount the root mount at self.root
        _log_debug_mounts("Attempting to unmount snapshot set root: %s", self.root)
        _umount(self.root)

    def _chroot(self):
        """
        Return a chrooted command list for this snapshot set mount.

        :returns: A command list including an appropriate chroot invocation
                  if necessary.
        :rtype: ``List[str]``
        """
        return ["chroot", self.root]


class SysMount(MountBase):
    """
    A class representing the system root file system tree mounted at '/'.
    """

    TYPE = "System root"

    def __init__(self):
        """
        Initialize a new SysMount instance for the root file system.

        :raises SnapmPathError: If root is not a directory, or not a mount
                                point. This should never happen for the root
                                file system.
        """
        super().__init__("/", "System Root")
        self.snapset: Optional[SnapshotSet] = None

        _log_debug("Building SysMount instance")

        if not os.path.ismount(self.root):
            raise SnapmPathError(f"Mount path {self.root} is not a mount point.")
        pmr = ProcMountsReader()
        submounts = [mnt.where for mnt in pmr.submounts(self.root)]
        submounts.sort(key=lambda mnt: mnt.count("/"), reverse=True)
        _log_debug_mounts("Submounts by depth (%s)", self.name)
        for submount in submounts:
            _log_debug_mounts(submount)
        name_map = {
            mnt.where: os.path.basename(mnt.what)
            for mnt in pmr.submounts("/")
            if mnt.what.startswith(os.sep)
        }
        mount_points = name_map.keys()
        self._check_submounts(mount_points, submounts, name_map=name_map)

    def _do_mount(self):
        """
        Dummy template mount method hook for the system root file system.
        """
        # Unreachable sanity check: ``MountBase.mount()`` will already reject
        # an attempt to mount a mounted file system.
        raise SnapmPathError("Root file system is already mounted")

    def _do_umount(self):
        """
        Template unmount method hook. Always rejects attempts to unmount
        the root file system.
        """
        raise SnapmPathError("Root file system cannot be unmounted")

    def _chroot(self):
        """
        Return a non-chrooted command list for the root file system mount.

        :returns: A command list including an appropriate chroot invocation
                  if necessary.
        :rtype: ``List[str]``
        """
        return []


class Mounts:
    """
    A high-level interface for mounting, unmounting and enumerating snapshot
    set mounts in the snapm runtime directory (normally /run/snapm/mounts).
    """

    def __init__(self, manager, mounts_dir: str, state_dir: str, lock_dir: str):
        """
        Initialise a new `Mounts` object.

        :param manager: The `Manager` object that this instance belongs to.
        :param mounts_dir: The directory under which mounts are managed.
        :param state_dir: The directory holding the mount provenance
                          records (#1025).
        :param lock_dir: The directory holding the lock that serialises
                         changes to the mount state.
        """
        self._manager = manager
        self._lock_dir = lock_dir
        #: How long to wait for the mount state lock. Overridden by the
        #: tests, which have no reason to wait out the default.
        self._lock_timeout = MOUNT_STATE_LOCK_TIMEOUT
        # Every mount lookup is a string comparison against a path the
        # kernel reported, and the kernel reports resolved paths: canonicalise
        # the base once here so that the paths built from it match (#1029).
        self._root = os.path.realpath(mounts_dir)
        self._state = MountStateDir(state_dir)
        self._mounts = []
        self._sys_mount = SysMount()
        self.discover_mounts()

    def _snapset_mounts(self, snapset: SnapshotSet) -> List[Mount]:
        """
        Return the mounts for ``snapset``, in discovery order.

        :param snapset: The snapshot set to look up.
        :returns: A list of `Mount` objects, empty if the set is not mounted.
        """
        return [mount for mount in self._mounts if mount.snapset.name == snapset.name]

    def _canonical_mount(self, snapset: SnapshotSet) -> Optional[Mount]:
        """
        Return the canonical mount for ``snapset``.

        A snapshot set may be mounted at more than one path at a time: the
        canonical mount is the first one discovered or created, and is the
        mount reported as the set's mount root. Entries describing a path
        that is no longer mounted are dropped by
        ``_forget_stale_mounts()``, which every caller runs first.

        :param snapset: The snapshot set to look up.
        :returns: A `Mount` object, or ``None`` if the set is not mounted.
        """
        candidates = self._snapset_mounts(snapset)
        return candidates[0] if candidates else None

    def _default_mount_path(self, snapset: SnapshotSet) -> str:
        """
        Return the path at which ``snapset`` is mounted beneath the default
        mounts directory.

        :param snapset: The snapshot set to build a mount path for.
        :returns: The absolute mount path for the snapshot set.
        :raises SnapmPathError: If the set name does not name a path directly
                                beneath the mounts directory.
        """
        # The mounts directory is already canonical, and the final component
        # is deliberately left unresolved: it is the path to be created, and
        # a symbolic link planted there is refused by the mount point checks
        # in mount(), not treated as a malformed set name.
        path = os.path.normpath(os.path.join(self._root, snapset.name))

        # The set name has to contribute exactly one path component: '..' and
        # the empty name both resolve back to the base or above it (#1026).
        if os.path.dirname(path) != self._root:
            raise SnapmPathError(
                f"Snapshot set name '{snapset.name}' does not name a mount "
                f"point beneath {self._root}"
            )
        return path

    def _check_mounts_dir(self) -> None:
        """
        Check the mounts directory before creating a mount point in it.

        Mount points created here are verified by name and then mounted by
        name, so the directory holding them has to be one that no other
        user can write to.

        :raises SnapmPathError: If the mounts directory is not a directory
                                owned by this process and writable by its
                                owner alone.
        """
        # stat(), not lstat(): the mounts directory was resolved with
        # realpath() when this object was built, so there is no final symbolic
        # link left here for lstat() to catch (#1029). A symbolic link planted
        # in place of the mounts directory itself is refused by
        # _check_snapm_dir(), before the Manager creates this object.
        try:
            st = os.stat(self._root)
        except OSError as err:
            raise SnapmPathError(
                f"Mounts directory is not accessible: {self._root}"
            ) from err
        if not S_ISDIR(st.st_mode):
            raise SnapmPathError(f"Mounts directory is not a directory: {self._root}")
        if st.st_uid != os.geteuid():
            raise SnapmPathError(
                f"Mounts directory is not owned by uid {os.geteuid()}: {self._root}"
            )
        if st.st_mode & (S_IWGRP | S_IWOTH):
            raise SnapmPathError(
                f"Mounts directory is writable by other users: {self._root}"
            )

    def _owns_mount_point(self, path: str) -> bool:
        """
        Test whether ``path`` is a mount point snapm created, and may
        therefore remove.

        The answer is the one recorded when the set was mounted there: a
        default mount point in the mounts directory is snapm's to remove,
        and a mount point nominated with ``--mount-root`` belongs to
        whoever made it. A path with no record is not snapm's at all.

        :param path: The mount point to test.
        :returns: ``True`` if snapm created ``path``, or ``False`` otherwise.
        """
        record = self._state.get(path)
        return record is not None and record.created_mount_point

    def _check_mount_point(self, path: str) -> str:
        """
        Validate a mount point nominated in place of the default and return
        the resolved path to mount the snapshot set at.

        A symbolic link in a leading component is resolved: mount(8) follows
        it, and the kernel then reports the resolved path, so a mount rooted
        at an unresolved one can never be found again (#1029). A link as the
        final component is refused rather than resolved, since that is the
        component nominating the mount point.

        :param path: The mount point to validate.
        :returns: The resolved mount point path.
        :raises SnapmPathError: If ``path`` is within the mounts directory,
                                is not an existing directory, or is a
                                symbolic link.
        """
        real_path = os.path.realpath(path)

        # Mount points in the mounts directory are snapm's own, created and
        # removed with the mount: a nominated one is neither, and one
        # planted there would collide with the default mount point of a set
        # named for it. The test is made against the resolved path so that a
        # link to a path in the mounts directory does not slip past it.
        if real_path == self._root or real_path.startswith(self._root + os.sep):
            raise SnapmPathError(
                f"Mount point is within the snapm mounts directory "
                f"{self._root}: omit --mount-root to mount there"
            )
        try:
            st = os.lstat(path)
        except OSError as err:
            raise SnapmPathError(f"Mount point does not exist: {path}") from err
        if S_ISLNK(st.st_mode):
            raise SnapmPathError(f"Mount point is a symbolic link: {path}")
        if not S_ISDIR(st.st_mode):
            raise SnapmPathError(f"Mount point is not a directory: {path}")
        return real_path

    @staticmethod
    def _reusable_mount_point(path: str) -> bool:
        """
        Test whether ``path`` is a leftover mount point that snapm may
        re-use: an empty directory, with nothing mounted on it, that is not
        a symbolic link.

        :param path: The path to test.
        :returns: ``True`` if ``path`` may be re-used as a mount point, or
                  ``False`` otherwise.
        """
        # One lstat() answers both questions: a symbolic link is not
        # S_ISDIR, so it is refused without following it.
        try:
            st = os.lstat(path)
        except OSError:  # pragma: no cover
            return False
        if not S_ISDIR(st.st_mode):
            return False
        # An empty file system mounted here reads as an empty directory.
        if os.path.ismount(path):
            return False
        return not os.listdir(path)

    def _update_mount_root(self, snapset: SnapshotSet) -> None:
        """
        Point the snapshot set's reported mount root at its canonical mount,
        or clear it if the set is no longer mounted anywhere.

        :param snapset: The snapshot set to update.
        """
        canonical = self._canonical_mount(snapset)
        snapset.mount_root = canonical.root if canonical is not None else ""

    def _forget_stale_mounts(
        self, snapset: Optional[SnapshotSet] = None
    ) -> List[Mount]:
        """
        Drop the mount table entries that no longer describe a mount, and
        return them.

        A snapshot set unmounted outside snapm, or one whose mount point
        could not be removed when it was unmounted, leaves an entry naming a
        path with nothing mounted on it. Until it is dropped the set looks
        mounted there: the path is reported as the set's mount root, it
        blocks mounting the set there again, and it counts towards the
        ambiguity check in ``umount()``.

        Nothing here leaves this object: the entries are removed from the
        list in memory and the reported mount root is recomputed from what
        is left, both of which follow from the mount table as it is now.
        Removing what the dropped entries left on disk is
        ``_remove_stale_mounts()``'s to do, and needs the lock.

        :param snapset: Optional snapshot set to limit this to. All
                        snapshot sets are considered when it is ``None``.
        :returns: The entries that were dropped, in mount table order.
        """
        stale = [
            mount
            for mount in self._mounts
            if not mount.mounted
            and (snapset is None or mount.snapset.name == snapset.name)
        ]
        for mount in stale:
            _log_debug_mounts(
                "Discarding stale mount for %s at '%s'", mount.snapset.name, mount.root
            )
            self._mounts.remove(mount)
            # The discarded entry may have been the one reported for the set.
            self._update_mount_root(mount.snapset)
        return stale

    def _remove_stale_mounts(self, stale: List[Mount]) -> None:
        """
        Remove what the mounts dropped by ``_forget_stale_mounts()`` left
        behind: the record of each, and the mount point when snapm made it.

        A nominated mount root is left alone: it belongs to whoever made
        it.

        This writes to the mount state, so the caller holds the mount
        state lock.

        :param stale: The dropped entries, as returned by
                      ``_forget_stale_mounts()``.
        """
        for mount in stale:
            if self._owns_mount_point(mount.root):
                try:
                    os.rmdir(mount.root)
                except OSError as err:
                    _log_warn(
                        "Could not remove stale mount point '%s': %s", mount.root, err
                    )
            self._state.discard(mount.root)

    @staticmethod
    def _record_is_live(record: MountStateRecord, mountinfo: MountInfoReader) -> bool:
        """
        Test whether the mount described by ``record`` is still mounted.

        The recorded mount path has to still be a mount point, and the
        device mounted there has to still be the one recorded. Checking the
        device as well distinguishes a snapshot set that is still mounted
        from a mount point that snapm's mount was taken off and something
        else was then mounted on: unmounting the latter would take out a
        mount that is not snapm's (#1025).

        The device number recorded once the mount was made is the one the
        kernel reports for the mount, so it is compared with the mount
        table directly. A record with none was written by a command that
        never reached the end of its mount, and names only the device it
        set out to mount: match that through the mount table index, which
        resolves a device to its mounts whether or not the file system
        mounted from it has an anonymous superblock.

        :param record: The mount provenance record to verify.
        :param mountinfo: A ``MountInfoReader`` instance to use.
        :returns: ``True`` if the recorded mount is still present.
        """
        entry = mountinfo.lookup_mount_point(record.mount_path)
        if entry is None:
            _log_debug_mounts(
                "Nothing is mounted at recorded mount path '%s'", record.mount_path
            )
            return False

        if record.mount_devno is not None:
            matched = entry.devno == record.mount_devno
        else:
            matched = entry in mountinfo.lookup_devno(record.root_devno)

        if not matched:
            _log_debug_mounts(
                "Mount at '%s' is not the recorded device %s (%d:%d)",
                record.mount_path,
                record.root_device,
                os.major(record.root_devno),
                os.minor(record.root_devno),
            )
            return False
        return True

    @staticmethod
    def _mounted_devno(mount_path: str) -> Optional[int]:
        """
        Return the device number the kernel reports for the file system
        mounted at ``mount_path``.

        :param mount_path: The mount point to read the device number of.
        :returns: The device number of the mount at ``mount_path``, or
                  ``None`` if nothing is mounted there.
        """
        entry = MountInfoReader().lookup_mount_point(mount_path)
        if entry is None:  # pragma: no cover
            _log_warn("Nothing is mounted at '%s' after mounting it", mount_path)
            return None
        return entry.devno

    def _discard_record(self, record: MountStateRecord) -> None:
        """
        Discard the record for a mount that is no longer present, removing
        the mount point with it if it was snapm's to remove.

        :param record: The mount provenance record to discard.
        """
        _log_debug_mounts(
            "Discarding stale mount state record for %s at '%s'",
            record.snapset_name,
            record.mount_path,
        )
        if record.created_mount_point:
            try:
                os.rmdir(record.mount_path)
            except OSError as err:
                _log_warn(
                    "Could not remove stale mount point '%s': %s",
                    record.mount_path,
                    err,
                )
        self._state.discard(record.mount_path)

    def _refresh_state(self) -> None:
        """
        Re-read the mount state records now that the lock is held.

        The copy in memory was read when this object was built, which is
        before the lock was taken and may be a long time before for a
        caller that keeps a ``Manager`` around. Another command may have
        mounted or unmounted something since, so everything that follows
        has to be decided from the records as they are now rather than as
        they were at discovery.

        :raises SnapmPathError: If the record directory cannot be read.
        """
        self._state.load()

    def _recorded_mounts(self, mounts: List[Mount]) -> List[Mount]:
        """
        Return those of ``mounts`` that still have a provenance record.

        An entry says that the path was snapm's when it was discovered.
        By the time the lock is taken another command may have unmounted
        it and discarded its record, and an administrator may have
        mounted something else there; the path is still a mount point, so
        ``_forget_stale_mounts()`` keeps the entry and nothing else
        distinguishes the two. Acting on it would unmount a file system
        that is not snapm's, which is the outcome the records exist to
        prevent (#1025).

        :param mounts: The candidate mounts to filter.
        :returns: Those of ``mounts`` with a record, in the order given.
        """
        recorded = []
        for mount in mounts:
            if self._state.get(mount.root) is not None:
                recorded.append(mount)
            else:
                _log_warn(
                    "Ignoring mount at '%s': it is no longer recorded as a "
                    "mount of snapshot set %s",
                    mount.root,
                    mount.snapset.name,
                )
        return recorded

    def _reap_stale_records(self) -> None:
        """
        Remove the mount state records that no longer describe a mount,
        along with the mount points snapm created for them.

        Discovery leaves them alone, so this is where they go. A record is
        written before the mount it describes is made, which means a
        record for a path with nothing mounted on it is as likely to
        belong to a mount another process is making right now as to one
        that is over. Reaping from the commands that mount and unmount,
        rather than from discovery, keeps every read-only command out of
        the way of a mount in progress: a 'snapset list' or a scheduled
        garbage collection can no longer unlink the record of a mount
        that is halfway made, nor remove the directory it is about to be
        mounted on.

        The caller holds the mount state lock, so no other command is
        mounting or unmounting while this runs.
        """
        self._state.reap_partial_records()
        mountinfo = MountInfoReader()
        for record in self._state.records:
            if not self._record_is_live(record, mountinfo):
                self._discard_record(record)

    def discover_mounts(self):
        """
        Discover and validate existing snapshot set mounts.

        Mounts are found from the provenance records snapm writes as it
        mounts a snapshot set, so a set mounted at a custom mount root is
        found again without the path having to be derived from the mount
        table, and a snapshot that an administrator mounted by hand is not
        mistaken for a mount of snapm's (#1025).

        Nothing here writes. A record describing a mount that is no longer
        present is passed over rather than discarded: discovery runs for
        every command, including those that only report, and removing a
        record is ``_reap_stale_records()``'s to do.
        """
        _log_info("Discovering snapshot set mounts")
        self._mounts.clear()
        self._state.load()
        mountinfo = MountInfoReader()

        by_uuid = {str(s.uuid): s for s in self._manager.snapshot_sets}

        for record in self._state.records:
            if not self._record_is_live(record, mountinfo):
                _log_debug_mounts(
                    "Ignoring mount state record for '%s': not a managed"
                    " mount point",
                    record.mount_path,
                )
                continue

            # A set is identified by the UUID the record was written with,
            # and by nothing else. The name is recorded to make the record
            # readable: matching on it would attach the mount to whichever
            # set holds the name now, which need not be the set that was
            # mounted.
            snapset = by_uuid.get(record.snapset_uuid)
            if snapset is None:
                # The mount is still up but the set it belongs to is gone,
                # so there is nothing to attach the mount to. The record is
                # kept: it is the only description of a mount that still
                # needs tearing down.
                _log_warn(
                    "Snapshot set %s (%s) is mounted at '%s' but is no longer "
                    "known: unmount it by hand",
                    record.snapset_name,
                    record.snapset_uuid,
                    record.mount_path,
                )
                continue

            try:
                mount = Mount(snapset, record.mount_path, discover=True)
            except SnapmPathError as err:  # pragma: no cover
                # The recorded path was a mount point a moment ago, so this
                # is only reached if it is unmounted while discovery runs.
                _log_info(
                    "Ignoring invalid mount path: '%s' (%s)", record.mount_path, err
                )
                continue
            self._mounts.append(mount)
            _log_info(
                "Found snapshot set mount at '%s' (mounted=%s)",
                mount.root,
                mount.mounted,
            )

        for snapset in self._manager.snapshot_sets:
            roots = [mount.root for mount in self._snapset_mounts(snapset)]
            if len(roots) > 1:
                _log_warn(
                    "Snapshot set %s is mounted at multiple paths: %s",
                    snapset.name,
                    ", ".join(roots),
                )
            self._update_mount_root(snapset)

        _log_info("Found %d snapshot set mounts", len(self._mounts))

    def mount(
        self,
        snapset: SnapshotSet,
        mount_root: Optional[str] = None,
    ) -> Mount:
        """
        Mount the snapshot set `snapset`.

        The mount state lock is held for the whole operation, so that the
        record written before the mount and the update written after it
        are one change as far as any other command is concerned.

        :param snapset: The snapshot set to operate on.
        :param mount_root: Optional path to mount the snapshot set at, in
                           place of the default mount point beneath the
                           mounts directory. The directory must already
                           exist: snapm neither creates it nor removes it
                           when the set is unmounted.
        """
        with mount_state_lock(self._lock_dir, timeout=self._lock_timeout):
            return self._locked_mount(snapset, mount_root=mount_root)

    # pylint: disable=too-many-statements
    def _locked_mount(
        self,
        snapset: SnapshotSet,
        mount_root: Optional[str] = None,
    ) -> Mount:
        """
        Mount the snapshot set `snapset` with the mount state lock held.

        :param snapset: The snapshot set to operate on.
        :param mount_root: Optional path to mount the snapshot set at.
        """
        self._refresh_state()

        if mount_root is None:
            self._check_mounts_dir()
            mount_path = self._default_mount_path(snapset)
        else:
            mount_path = self._check_mount_point(os.path.abspath(mount_root))

        # Discard any entry for a path that is no longer mounted, along with
        # the mount point it names, so that the set can be mounted there
        # again. This command writes to the record directory, so it is also
        # the point at which the records discovery passed over are tidied
        # up.
        self._remove_stale_mounts(self._forget_stale_mounts(snapset))
        self._reap_stale_records()

        # A snapshot set may be mounted at more than one path at a time. An
        # explicit mount root asks for a mount at a specific path, so only a
        # mount already rooted there will do: with no mount root any mount of
        # the set is acceptable. Only a path that still has a record is
        # one of snapm's mounts.
        candidates = self._recorded_mounts(self._snapset_mounts(snapset))
        if mount_root is not None:
            candidates = [mount for mount in candidates if mount.root == mount_path]
        if candidates:
            existing = candidates[0]
            _log_info(
                "Snapshot set %s already mounted at %s", snapset.name, existing.root
            )
            return existing

        if mount_root is not None and os.path.ismount(mount_path):
            raise SnapmPathError(f"Mount point is already mounted: {mount_path}")

        # Ensure the snapshot set's volumes are active
        snapset.activate()

        # Create the default mount point. An unmount performed outside snapm
        # leaves it behind with nothing mounted on it and no entry in the
        # mount table to prune, so re-use an empty directory found here
        # rather than refusing to mount the set ever again. A symbolic link
        # is refused, which stops one planted here in advance from
        # redirecting the mount; a link swapped in after this check still
        # wins, since mount(8) resolves the path again.
        #
        # A mount point in the mounts directory is snapm's to remove when
        # the set is unmounted whether it was made here or left behind by an
        # earlier mount; a nominated one never is.
        created = mount_root is None
        if mount_root is None:
            try:
                os.mkdir(mount_path)
            except FileExistsError as err:
                if not self._reusable_mount_point(mount_path):
                    raise SnapmPathError(
                        f"Mount point {mount_path} is not an empty directory"
                    ) from err
                _log_info("Re-using leftover mount point '%s'", mount_path)
            except OSError as err:
                raise SnapmPathError(
                    f"Could not create mount point {mount_path}: {err}"
                ) from err

        try:
            # Settle the root device here and hand it to both the mount and
            # the record, so that discovery verifies the device that was
            # actually mounted rather than deriving a second answer that can
            # differ from it (#1031).
            root_device = find_snapset_root(snapset, FsTabReader())
            root_devno = device_number(root_device)
            if root_devno is None:
                raise SnapmPathError(
                    f"Root device for snapshot set {snapset.name} is not a "
                    f"block device: {root_device}"
                )

            # Record the mount before making it. A record left behind by a
            # mount that never happened is discarded by the next discovery
            # pass, which verifies every record against the mount table; a
            # mount made with no record is one snapm can never claim again.
            record = MountStateRecord(
                mount_path=mount_path,
                snapset_name=snapset.name,
                snapset_uuid=str(snapset.uuid),
                root_device=root_device,
                root_devno=root_devno,
                created_mount_point=created,
            )
            self._state.add(record)

            mount = Mount(snapset, mount_path, root_device=root_device)
            mount.mount()

            # The mount is up: note the device number the kernel gave it,
            # which is the one the mount table carries and is the only
            # value that can be compared with it for a file system whose
            # superblock is anonymous. Until this is written the record
            # describes an intention; afterwards it describes a mount.
            record.mount_devno = self._mounted_devno(mount_path)
            self._state.add(record)
        except (SnapmError, ValueError):
            # A rollback that cannot unmount what it mounted logs and
            # carries on, so the tree may still be up at this point. The
            # record is its only description: discard it only once nothing
            # is mounted at the path, or the mount is left with nothing to
            # find it by and has to be taken down by hand.
            if os.path.ismount(mount_path):
                _log_warn(
                    "Snapshot set %s is still mounted at '%s' after a failed "
                    "mount: keeping its mount state record",
                    snapset.name,
                    mount_path,
                )
            else:
                self._state.discard(mount_path)
                if created:
                    try:
                        os.rmdir(mount_path)
                    except OSError:
                        pass  # pragma: no cover
            raise

        self._mounts.append(mount)

        # Report the canonical mount for the set: this is the new mount
        # unless the set was already mounted at another path.
        self._update_mount_root(snapset)

        return mount

    def umount(self, snapset: SnapshotSet, mount_root: Optional[str] = None):
        """
        Unmount the snapshot set `snapset`.

        The mount state lock is held for the whole operation, so that the
        unmount and the discarding of the record that describes it are one
        change as far as any other command is concerned.

        :param snapset: The snapshot set to operate on.
        :param mount_root: Optional mount point selecting the mount to
                           unmount. Only required when ``snapset`` is mounted
                           at more than one path.
        :raises SnapmNotFoundError: If no matching mount is found.
        :raises SnapmArgumentError: If ``snapset`` is mounted at more than one
                                    path and ``mount_root`` does not select
                                    exactly one of them.
        """
        with mount_state_lock(self._lock_dir, timeout=self._lock_timeout):
            self._locked_umount(snapset, mount_root=mount_root)

    def _locked_umount(self, snapset: SnapshotSet, mount_root: Optional[str] = None):
        """
        Unmount the snapshot set `snapset` with the mount state lock held.

        :param snapset: The snapshot set to operate on.
        :param mount_root: Optional mount point selecting the mount to
                           unmount.
        """
        self._refresh_state()

        # Only the paths the set is still mounted at are candidates, and only
        # they make the request ambiguous. This command writes to the record
        # directory, so it is also the point at which the records discovery
        # passed over are tidied up.
        self._remove_stale_mounts(self._forget_stale_mounts(snapset))
        self._reap_stale_records()

        candidates = self._recorded_mounts(self._snapset_mounts(snapset))
        if mount_root is not None:
            # Mounts are recorded at the path mount(8) resolved them to, so
            # match the resolved path here too (#1029).
            mount_path = os.path.realpath(mount_root)
            candidates = [mount for mount in candidates if mount.root == mount_path]

        if not candidates:
            raise SnapmNotFoundError(f"Mount for snapshot set {snapset.name} not found")
        if len(candidates) > 1:
            roots = ", ".join(mount.root for mount in candidates)
            raise SnapmArgumentError(
                f"Snapshot set {snapset.name} is mounted at multiple paths: "
                f"use --mount-root to select one of ({roots})."
            )

        # Unmount and clean up root first
        mount = candidates[0]
        mount.umount()
        if self._owns_mount_point(mount.root):
            try:
                os.rmdir(mount.root)
            except OSError as err:
                # The unmount succeeded: the directory left behind is
                # untidy, not a failure, and the cleanup below has to run
                # either way. Reporting this as an error would report a
                # successful unmount as a failed one, and leaving the
                # record and the entry in place would leave both
                # describing a mount that is gone.
                _log_warn("Could not remove mount point '%s': %s", mount.root, err)

        # The mount is gone: so is snapm's claim on the path.
        self._state.discard(mount.root)

        # Update the mount table on success
        self._mounts.remove(mount)

        # Update snapset mount_root to any remaining mount for the set
        self._update_mount_root(snapset)

    def find_mounts(self, selection: Optional[Selection] = None) -> List[Mount]:
        """
        Return a list of `Mount` objects describing the currently mounted
        snapshot sets managed by this `Mounts` instance.

        Snapshot sets unmounted outside snapm are dropped before matching, so
        that every mount returned is one that is still mounted. Dropping
        them writes nothing, so reporting on mounts neither takes the mount
        state lock nor waits on one: what the dropped entries left on disk
        is removed by the next command that mounts or unmounts.
        """
        self._forget_stale_mounts()
        selection = selection or Selection()
        selection.check_valid_selection(snapshot_set=True)
        return [m for m in self._mounts if select_snapshot_set(selection, m.snapset)]

    def get_sys_mount(self) -> SysMount:
        """
        Return an object representing the system root file system mounts.

        :returns: A ``SysMount`` instance representing '/'.
        :rtype: ``SysMount``
        """
        return self._sys_mount


__all__ = [
    "Mount",
    "Mounts",
    "SysMount",
]
