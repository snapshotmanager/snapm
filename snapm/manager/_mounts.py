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
from stat import S_ISBLK
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
    select_snapshot_set,
    FsTabReader,
    unescape_mounts,
    get_device_fstype,
    resolve_device_spec,
    find_snapset_root,
    build_snapset_mount_list,
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
    if rprivate:
        mount_cmd.append("--make-rprivate")
    if unbindable:
        mount_cmd.append("--make-unbindable")

    mount_cmd.extend(["--options", options, what, where])
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

    def lookup_device(self, devpath):
        """
        Find and generate all entries backed by the block device at ``devpath``.

        Entries are matched on device number rather than on the device path, so
        that the ``/dev/VG/LV`` paths used by ``Snapshot.devpath`` match the
        ``/dev/mapper/VG-LV`` names reported in ``/proc/mounts``.

        :param devpath: The path to the block device to search for.
        :type devpath: str
        :yields: A ``MountsEntry`` for each entry backed by ``devpath``.
        """
        try:
            st = os.stat(devpath)
        except OSError as err:
            _log_debug_mounts("Cannot stat device %s: %s", devpath, err)
            return
        if not S_ISBLK(st.st_mode):
            _log_debug_mounts("Path %s is not a block device", devpath)
            return

        for entry in self.entries:
            if not entry.what.startswith("/dev/"):
                continue
            try:
                entry_st = os.stat(entry.what)
            except OSError:  # pragma: no cover
                continue
            if S_ISBLK(entry_st.st_mode) and entry_st.st_rdev == st.st_rdev:
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
        # pmr: Optional[ProcMountsReader] = None,
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
        :raises SnapmPathError: If mount_root is not a directory, or if discover is True
                                and mount_root is not a mount point.
        """
        super().__init__(mount_root, snapset.name)

        _log_debug("Building snapshot set Mount instance for %s", snapset.name)

        # The snapshot set that this mount belongs to.
        self.snapset: Optional[SnapshotSet] = snapset

        if discover:
            if not os.path.ismount(mount_root):
                raise SnapmPathError(f"Mount path {mount_root} is not a mount point.")
            pmr = ProcMountsReader()
            submounts = [mnt.where for mnt in pmr.submounts(self.root)]
            submounts.sort(key=lambda mnt: mnt.count("/"), reverse=True)
            _log_debug_mounts("Submounts by depth (%s)", self.snapset.name)
            for submount in submounts:
                _log_debug_mounts(submount)

            self.snapset.mount_root = mount_root

            name_map = {
                mp: self.snapset.snapshot_by_source(mp).name
                for mp in self.snapset.mount_points
            }
            self._check_submounts(
                self.snapset.mount_points, submounts, name_map=name_map
            )

    def _do_mount(self):
        """
        Mount the configured snapshot set and its submounts at `self.root`.
        """
        try:
            # 0. Initialise FsTabReader
            fstab = FsTabReader()

            # 1. Find and mount the snapshot set root filesystem and make it unbindable.
            root_device = find_snapset_root(self.snapset, fstab)
            try:
                root_opts = next(fstab.lookup("where", "/")).options
            except StopIteration:
                _log_warn("No entry matching mount point '/' found in fstab")
                root_opts = "defaults"
            _mount(root_device, self.root, options=root_opts, unbindable=True)

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
                _mount(what, where, fstype=fstype, options=options)

            # 4. Recursively bind mount API file systems from the host.
            for what in _BIND_MOUNTS:
                if os.path.exists(what) and os.path.ismount(what):
                    where = os.path.join(self.root, what.lstrip("/"))
                    if not os.path.isdir(where):
                        _log_warn(
                            "Bind mount point %s does not exist in snapshot set %s, skipping mount",
                            where,
                            self.snapset.name,
                        )
                        continue
                    _mount(what, where, rbind=True, rprivate=True)

        except (ValueError, SnapmError, StopIteration) as err:
            # Rollback: discover all submounts and unmount in depth-first order
            _log_warn(
                "Mount operation failed for %s at %s: %s. Rolling back.",
                self.snapset.name,
                self.root,
                err,
            )
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
                    _log_warn(
                        "Failed to roll back mount '%s': %s", mount_path, cleanup_err
                    )
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

    def __init__(self, manager, mounts_dir: str):
        """
        Initialise a new `Mounts` object.

        :param manager: The `Manager` object that this instance belongs to.
        :param mounts_dir: The directory under which mounts are managed.
        """
        self._manager = manager
        self._root = mounts_dir
        self._mounts = []
        self._mounts_by_name = {}
        self._sys_mount = SysMount()
        self.discover_mounts()

    @staticmethod
    def _snapset_mount_roots(snapset: SnapshotSet, pmr: ProcMountsReader) -> List[str]:
        """
        Find the paths at which the snapshot set ``snapset`` is currently
        mounted by looking up its member devices in ``/proc/mounts``.

        Each member snapshot is mounted at ``<root>/<mount point>`` within the
        snapshot set mount tree, so the mount root is recovered by stripping
        the member's own mount point from the path reported for its device.
        The root member, if present, is checked first since its mount point is
        the mount root itself.

        :param snapset: The snapshot set to locate.
        :param pmr: A ``ProcMountsReader`` instance to use.
        :returns: A sorted list of distinct mount roots for ``snapset``.
        """
        roots = set()
        snapshots = sorted(
            snapset.snapshots, key=lambda snapshot: snapshot.mount_point != "/"
        )
        for snapshot in snapshots:
            mount_point = snapshot.mount_point
            if not mount_point:
                continue
            for entry in pmr.lookup_device(snapshot.devpath):
                if mount_point == "/":
                    roots.add(entry.where)
                    continue
                suffix = "/" + mount_point.strip("/")
                if entry.where.endswith(suffix):
                    roots.add(entry.where[: -len(suffix)])
        return sorted(roots)

    def discover_mounts(self):
        """
        Discover and validate existing snapshot set mounts.

        Mounts are located by matching each snapshot set's member devices
        against ``/proc/mounts``, so snapshot sets mounted at a custom mount
        base are found without needing the mount root to be persisted
        anywhere.
        """
        _log_info("Discovering snapshot set mounts")
        self._mounts.clear()
        self._mounts_by_name.clear()
        mounts = []
        pmr = ProcMountsReader()

        for snapset in self._manager.snapshot_sets:
            roots = self._snapset_mount_roots(snapset, pmr)
            if not roots:
                snapset.mount_root = ""
                continue
            if len(roots) > 1:
                _log_warn(
                    "Snapshot set %s is mounted at multiple paths: %s",
                    snapset.name,
                    ", ".join(roots),
                )
            snapset_mounts = []
            for root in roots:
                try:
                    mount = Mount(snapset, root, discover=True)
                except SnapmPathError as err:
                    _log_info("Ignoring invalid mount path: '%s' (%s)", root, err)
                    continue
                snapset_mounts.append(mount)
                _log_info(
                    "Found snapshot set mount at '%s' (mounted=%s)",
                    mount.root,
                    mount.mounted,
                )
            mounts.extend(snapset_mounts)

            # Record the first valid mount root for report output.
            snapset.mount_root = snapset_mounts[0].root if snapset_mounts else ""

        self._mounts.extend(mounts)
        for mount in mounts:
            # Keep the first mount found for a set: a set mounted at more than
            # one path is disambiguated by the caller passing an explicit root.
            self._mounts_by_name.setdefault(mount.snapset.name, mount)
        _log_info("Found %d snapshot set mounts", len(self._mounts))

    def mount(
        self,
        snapset: SnapshotSet,
        mount_base: Optional[str] = None,
    ) -> Mount:
        """
        Mount the snapshot set `snapset`.

        :param snapset: The snapshot set to operate on.
        :param mount_base: Optional base directory to mount the snapshot set
                           beneath, in place of the default mounts directory.
                           The snapshot set root is mounted at
                           ``<mount_base>/<snapset name>``.
        """
        if snapset.name in self._mounts_by_name:
            existing = self._mounts_by_name[snapset.name]
            if existing.mounted:
                _log_info(
                    "Snapshot set %s already mounted at %s",
                    snapset.name,
                    existing.root,
                )
                return existing
            try:
                self._mounts.remove(existing)
            except ValueError:
                pass
            self._mounts_by_name.pop(snapset.name, None)

        # Ensure the snapshot set's volumes are active
        snapset.activate()

        if mount_base is not None:
            mount_base = os.path.abspath(mount_base)
            if not os.path.isdir(mount_base):
                raise SnapmPathError(
                    f"Mount base path does not exist or is not a directory: {mount_base}"
                )
            mount_path = os.path.join(mount_base, snapset.name)
        else:
            mount_path = os.path.join(self._root, snapset.name)
        os.makedirs(mount_path, exist_ok=True)

        mount = Mount(snapset, mount_path)
        try:
            mount.mount()
        except (SnapmError, ValueError):
            try:
                os.rmdir(mount_path)
            except OSError:
                pass  # pragma: no cover
            raise

        self._mounts.append(mount)
        self._mounts_by_name[snapset.name] = mount

        # Set snapset mount_root
        snapset.mount_root = mount.root

        return mount

    def umount(self, snapset: SnapshotSet, mount_base: Optional[str] = None):
        """
        Unmount the snapshot set `snapset`.

        :param snapset: The snapshot set to operate on.
        :param mount_base: Optional base directory to select the mount to
                           unmount. Only required when ``snapset`` is mounted
                           at more than one path.
        :raises SnapmNotFoundError: If no matching mount is found.
        :raises SnapmArgumentError: If ``snapset`` is mounted at more than one
                                    path and ``mount_base`` does not select
                                    exactly one of them.
        """
        candidates = [m for m in self._mounts if m.snapset.name == snapset.name]
        if mount_base is not None:
            wanted = os.path.join(os.path.abspath(mount_base), snapset.name)
            candidates = [m for m in candidates if m.root == wanted]

        if not candidates:
            raise SnapmNotFoundError(f"Mount for snapshot set {snapset.name} not found")
        if len(candidates) > 1:
            raise SnapmArgumentError(
                f"Snapshot set {snapset.name} is mounted at multiple paths "
                f"({', '.join(m.root for m in candidates)}): use --mount-root "
                "to select one."
            )

        # Unmount and clean up root first
        mount = candidates[0]
        mount.umount()
        os.rmdir(mount.root)

        # Update registries on success
        try:
            self._mounts.remove(mount)
        except ValueError:  # pragma: no cover
            pass
        if self._mounts_by_name.get(snapset.name) is mount:
            self._mounts_by_name.pop(snapset.name, None)
            remaining = next(
                (m for m in self._mounts if m.snapset.name == snapset.name), None
            )
            if remaining is not None:
                self._mounts_by_name[snapset.name] = remaining

        # Update snapset mount_root to any remaining mount for the set
        snapset.mount_root = (
            self._mounts_by_name[snapset.name].root
            if snapset.name in self._mounts_by_name
            else ""
        )

    def find_mounts(self, selection: Optional[Selection] = None) -> List[Mount]:
        """
        Return a list of `Mount` objects describing the currently mounted
        snapshot sets managed by this `Mounts` instance.
        """
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
