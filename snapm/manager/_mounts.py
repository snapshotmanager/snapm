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
from os.path import exists as path_exists, ismount, isdir, join as path_join
from typing import List, Optional
import collections
import logging
import os

from snapm import (
    SNAPM_SUBSYSTEM_MOUNTS,
    SnapmError,
    SnapmNotFoundError,
    SnapmCalloutError,
    SnapmPathError,
    SnapmMountError,
    SnapmUmountError,
    SnapshotSet,
    FsTabReader,
    get_device_fstype,
    get_device_path,
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

#: Taken from kernel source: fs/xfs/libxfs/xfs_log_format.h:
_XFS_UQUOTA_ACCT = 0x0001  # user quota accounting ON
_XFS_UQUOTA_ENFD = 0x0002  # user quota limits enforced
_XFS_GQUOTA_ACCT = 0x0040  # group quota accounting ON
_XFS_GQUOTA_ENFD = 0x0080  # group quota limits enforced
_XFS_PQUOTA_ACCT = 0x0008  # project quota accounting ON
_XFS_PQUOTA_ENFD = 0x0200  # project quota limits enforced

#: Prefix for the output of the "print qflags" command.
_QFLAGS_PREFIX = "qflags = "


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


def _resolve_device(device: str) -> str:
    """
    Resolve a device that may be in the form of a LABEL=... or UUID=...
    expression into a device path. If the device is neither a UUID nor
    a label reference it is returned unmodified.

    :param device: The device string to evaluate; '/dev/...', 'LABEL=...',
                   or 'UUID=...'.
    :returns: The device path.
    :rtype: ``str``
    """
    if device.startswith("UUID="):
        uuid = device.split("=", maxsplit=1)[1]
        resolved = get_device_path("uuid", uuid)
        if resolved is None:
            raise SnapmNotFoundError(f"Device with UUID '{uuid}' not found")
        device = resolved
    elif device.startswith("LABEL="):
        label = device.split("=", maxsplit=1)[1]
        resolved = get_device_path("label", label)
        if resolved is None:
            raise SnapmNotFoundError(f"Device with label '{label}' not found")
        device = resolved
    elif device.startswith("PARTUUID="):
        ident = device.split("=", maxsplit=1)[1]
        cand = f"/dev/disk/by-partuuid/{ident}"
        if not path_exists(cand):
            raise SnapmNotFoundError(f"Device with PARTUUID '{ident}' not found")
        device = cand
    elif device.startswith("PARTLABEL="):
        ident = device.split("=", maxsplit=1)[1]
        cand = f"/dev/disk/by-partlabel/{ident}"
        if not path_exists(cand):
            raise SnapmNotFoundError(f"Device with PARTLABEL '{ident}' not found")
        device = cand
    return device


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

    what = _resolve_device(what)

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
        raise SnapmMountError(what, where, err.returncode, err.stderr) from err


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
            check=True,
            capture_output=True,
            encoding="utf8",
            timeout=_SNAPM_MOUNT_HELPER_TIMEOUT,
        )
    except TimeoutExpired as err:
        raise SnapmCalloutError(f"Timed out calling umount for {where}: {err}") from err
    except CalledProcessError as err:
        raise SnapmUmountError(where, err.returncode, err.stderr) from err


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

    def submounts(self, root):
        """Iterate over submounts under the given mount point root.

        :param root: The mount point root (e.g., '/run/snapm/mounts/before-upgrade')
        :returns: Yields ``MountsEntry`` objects for submounts under root.
        """
        with open(self.path, "r", encoding="utf8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue

                parts = line.split()
                if len(parts) == 6:
                    entry = self.MountsEntry(*parts)
                    mount_point = entry.where
                    root_prefix = root.rstrip("/") + "/"
                    if mount_point.startswith(root_prefix):
                        yield entry
                else:
                    _log_warn("Skipping malformed %s line: %s", self.path, line)


class Mount:
    """
    Representation of a mounted snapshot set, including snapshot set mounts,
    non-snapshot mounts from /etc/fstab, and bind mounts for API file systems.
    """

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
        :type snapset: SnapshotSet
        :param mount_root: The absolute path to the root directory where the snapshot
                           set will be mounted or is already mounted.
        :type mount_root: str
        :param discover: If True, validate that mount_root is an existing mount point
                         and discover its submounts. If False, prepare a new mount
                         object without validation.
        :type discover: bool
        :raises SnapmPathError: If mount_root is not a directory, or if discover is True
                                and mount_root is not a mount point.
        """
        if not isdir(mount_root):
            raise SnapmPathError(f"Mount path {mount_root} is not a directory.")

        _log_debug("Building snapshot set Mount instance for %s", snapset.name)

        # The root directory of this snapshot set mount.
        self.root = mount_root

        # The snapshot set that this mount belongs to.
        self.snapset = snapset

        if discover:
            if not ismount(mount_root):
                raise SnapmPathError(f"Mount path {mount_root} is not a mount point.")
            pmr = ProcMountsReader()
            submounts = [mnt.where for mnt in pmr.submounts(self.root)]
            submounts.sort(key=lambda mnt: mnt.count("/"), reverse=True)
            _log_debug_mounts("Submounts by depth (%s)", self.snapset.name)
            for submount in submounts:
                _log_debug_mounts(submount)

            self.snapset.mount_root = mount_root

            self._check_submounts(pmr)

    def _check_submounts(self, pmr: Optional[ProcMountsReader] = None):
        """
        Verify that the expected submounts (both snapshot set file systems and API
        file system bind mounts) are present under ``self.root``.
        """
        # Check snapshot set and API file system submounts
        checked = {mp: False for mp in self.snapset.mount_points if mp != "/"}
        bind_checked = {mp: False for mp in _BIND_MOUNTS}

        pmr = pmr or ProcMountsReader()
        for entry in pmr.submounts(self.root):
            abs_mount_path = entry.where
            rel_mount_path = entry.where.removeprefix(self.root)
            path_is_mount = ismount(abs_mount_path)
            if rel_mount_path in checked:
                checked[rel_mount_path] = path_is_mount
            elif rel_mount_path in bind_checked:
                bind_checked[rel_mount_path] = path_is_mount

        if not all(checked.values()):
            _log_warn("Missing snapshot set submounts for %s:", self.snapset.name)
            for mount_point in [mp for mp in checked if not checked[mp]]:
                _log_warn(
                    "  Missing: %s (%s)",
                    mount_point,
                    self.snapset.snapshot_by_mount_point(mount_point).name,
                )

        if not all(bind_checked.values()):
            _log_warn("Missing API file system submounts for %s:", self.snapset.name)
            for mount_point in [mp for mp in bind_checked if not bind_checked[mp]]:
                _log_warn("  Missing: %s", mount_point)

    @property
    def mounted(self):
        """
        Determine whether this snapshot set mount is currently mounted.
        """
        if not ismount(self.root):
            return False
        return True

    def mount(self):
        """
        Mount the configured snapshot set and its submounts at `self.root`.
        """
        if self.mounted:
            _log_info(
                "Snapshot set %s already mounted at %s", self.snapset.name, self.root
            )
            return

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

            # 3. Mount the discovered auxiliary mount points and make them unbindable.
            for mount_spec in mount_list:
                what, where, fstype, options = mount_spec
                where = path_join(self.root, where.lstrip("/"))
                if not isdir(where):
                    _log_warn(
                        "Mount point %s does not exist in snapshot set %s, skipping mount",
                        where,
                        self.snapset.name,
                    )
                    continue
                _mount(what, where, fstype=fstype, options=options)

            # 4. Recursively bind mount API file systems from the host.
            for what in _BIND_MOUNTS:
                if path_exists(what) and ismount(what):
                    where = path_join(self.root, what.lstrip("/"))
                    if not isdir(where):
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

    def umount(self):
        """
        Unmount the configured snapshot set and its submounts from `self.root`.
        """
        if not self.mounted:
            _log_warn("Snapshot set mount at '%s' is not mounted", self.root)
            return

        # 1. Discover and unmount each submount under self.root
        pmr = ProcMountsReader()
        submounts = [mnt.where for mnt in pmr.submounts(self.root)]
        submounts.sort(key=lambda mnt: mnt.count("/"), reverse=True)
        for submount in submounts:
            _log_debug_mounts(
                "Attempting to unmount %s (%s)", submount, self.snapset.name
            )
            _umount(submount)

        # 2. Unmount the root mount at self.root
        _log_debug_mounts("Attempting to unmount snapshot set root: %s", self.root)
        _umount(self.root)


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
        self.discover_mounts()

    def discover_mounts(self):
        """
        Discover and validate existing snapset mounts in the configured
        mount path.
        """
        _log_info("Discovering snapshot set mounts under '%s'", self._root)
        self._mounts.clear()
        self._mounts_by_name.clear()
        mounts = []
        for dirent in os.listdir(self._root):
            if dirent not in self._manager.by_name:
                _log_info("Skipping non-snapshot set path: '%s'", dirent)
                continue
            snapset = self._manager.by_name[dirent]
            try:
                mount_path = path_join(self._root, dirent)
                mount = Mount(snapset, mount_path, discover=True)
                mounts.append(mount)
                snapset.mount_root = mount.root
                _log_info(
                    "Found snapshot set mount at '%s' (mounted=%s)",
                    mount.root,
                    mount.mounted,
                )
            except SnapmPathError as err:
                _log_info("Ignoring invalid mount path: '%s' (%s)", mount_path, err)
                continue
        self._mounts.extend(mounts)
        self._mounts_by_name.update({mount.snapset.name: mount for mount in mounts})
        _log_info("Found %d snapshot set mounts", len(self._mounts))

    def mount(self, snapset: SnapshotSet) -> Mount:
        """
        Mount the snapshot set `snapset`.

        :param snapset: The snapshot set to operate on.
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

        mount_path = path_join(self._root, snapset.name)
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

    def umount(self, snapset: SnapshotSet):
        """
        Unmount the snapshot set `snapset`.

        :param snapset: The snapshot set to operate on.
        """
        # Unmount and clean up root first
        mount = self._mounts_by_name.get(snapset.name)
        if not mount:
            raise SnapmNotFoundError(f"Mount for snapshot set {snapset.name} not found")

        mount.umount()
        os.rmdir(mount.root)

        # Update registries on success
        self._mounts_by_name.pop(snapset.name, None)
        try:
            self._mounts.remove(mount)
        except ValueError:  # pragma: no cover
            pass

        # Clear snapset mount_root
        snapset.mount_root = ""

    def find_mounts(self) -> List[Mount]:
        """
        Return a list of `Mount` objects describing the currently mounted
        snapshot sets managed by this `Mounts` instance.
        """
        return self._mounts.copy()


__all__ = [
    "Mount",
    "Mounts",
]
