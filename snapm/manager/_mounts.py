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
from subprocess import run, CalledProcessError
from os.path import ismount, isdir
from typing import List, Optional
import logging

from snapm import (
    SNAPM_SUBSYSTEM_MOUNTS,
    SnapmPathError,
    SnapmMountError,
    SnapmUmountError,
    SnapshotSet,
    # FsTab,
)

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


def _log_debug_mounts(msg, *args, **kwargs):
    """A wrapper for mounts subsystem debug logs."""
    _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_MOUNTS}, **kwargs)


#: List of API file systems to bind if present and a mount point.
_bind_list: List[str] = [
    "/proc",
    "/sys",
    "/dev",
    "/sys/kernel/security",
    "/dev/shm",  # nosec: B108
    "/dev/pts",
    "/run",
    "/sys/fs/cgroup",
    "/sys/fs/pstore",
    "/sys/firmware/efi/efivars",
    "/sys/fs/bpf",
]


def _mount(
    what: str,
    where: str,
    fstype: Optional[str] = None,
    options: str = "defaults",
    bind: bool = False,
):
    """
    Call the mount program to mount a file system.

    :param what: The source for the mount operation.
    :param where: The path to the mount point.
    :param fstype: An optional file system type.
    :param options: Options to pass to the mount program.
    """
    mount_cmd = ["mount"]
    if bind:
        mount_cmd.append("--bind")
    if fstype:
        mount_cmd.extend(["--type", fstype])
    mount_cmd.extend(["--options", options, what, where])

    try:
        run(mount_cmd, check=True, capture_output=True, encoding="utf8")
    except CalledProcessError as err:
        raise SnapmMountError(what, where, err.returncode, err.stderr) from err


def _umount(where: str):
    """
    Call the umount program to unmount a file system.

    :param where: The mount point to be unmounted.
    """
    umount_cmd = ["umount", where]
    try:
        run(umount_cmd, check=True, capture_output=True, encoding="utf8")
    except CalledProcessError as err:
        raise SnapmUmountError(where, err.returncode, err.stderr) from err


class Mount:
    """
    Representation of a mounted snapshot set, including snapshot set mounts,
    non-snapshot mounts from /etc/fstab, and bind mounts for API file systems.
    """

    def __init__(self, mount_root: str, _snapshot_set: SnapshotSet):
        if not isdir(mount_root) or not ismount(mount_root):
            raise SnapmPathError(f"Mount path {mount_root} is not a mount point.")
        self.root = mount_root
        # Discover mounts beneath `mount_root`
        # ...


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
        self._mounts = self._discover_mounts()

    def _discover_mounts(self):
        """
        Discover and validate existing snapset mounts in the configured
        mount path.
        """
        return []

    def mount(self, _snapshot_set: SnapshotSet) -> Mount:
        """
        Mount the snapshot set `snapshot_set`.

        :param snapshot_set: The snapshot set to operate on.
        """

    def umount(self, _snapshot_set: SnapshotSet):
        """
        Unmount the snapshot set `snapshot_set`.

        :param snapshot_set: The snapshot set to operate on.
        """

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
