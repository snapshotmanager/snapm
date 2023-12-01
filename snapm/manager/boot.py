# Copyright (C) 2023 Red Hat, Inc., Bryn M. Reeves <bmr@redhat.com>
#
# snapm/manager/boot.py - Snapshot Manager boot support
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
Boot integration for snapshot manager
"""
from os import uname
from os.path import exists as path_exists
import logging

import boom
import boom.command
from boom.osprofile import match_os_profile_by_version

from snapm import SnapmNotFoundError, ETC_FSTAB

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error

#: Path to the system machine-id file
_MACHINE_ID = "/etc/machine-id"
#: Path to the legacy system machine-id file
_DBUS_MACHINE_ID = "/var/lib/dbus/machine-id"

#: Snapshot set kernel command line argument
SNAPSET_ARG = "snapm.snapset"

#: Snapshot set rollback kernel command line argument
ROLLBACK_ARG = "snapm.rollback"


def _get_uts_release():
    """
    Return the UTS release (kernel version) of the running system.

    :returns: A string representation of the UTS release value.
    """
    return uname()[2]


def _get_machine_id():
    """
    Return the current host's machine-id.

    Get the machine-id value for the running system by reading from
    ``/etc/machine-id`` and return it as a string.

    :returns: The ``machine_id`` as a string
    :rtype: str
    """
    if path_exists(_MACHINE_ID):
        path = _MACHINE_ID
    elif path_exists(_DBUS_MACHINE_ID):
        path = _DBUS_MACHINE_ID
    else:
        return None

    with open(path, "r", encoding="utf8") as f:
        try:
            machine_id = f.read().strip()
        except Exception as e:
            _log_error("Could not read machine-id from '%s': %s", path, e)
            machine_id = None
    return machine_id


def _find_snapset_root(snapset):
    """
    Find the device that backs the root filesystem for snapshot set ``snapset``.

    If the snapset does not include the root volume look the device up via the
    fstab.
    """
    for snapshot in snapset.snapshots:
        if snapshot.mount_point == "/":
            return snapshot
    # FIXME: add fstab lookup for non-root snapsets
    # needs either root=UUID/LABEL support in boom or a lookup to resolve any
    # UUID/LABEL found in the file.
    raise SnapmNotFoundError(f"Could not find root device for snapset {snapset.name}")


def _build_snapset_mount_list(snapset):
    """
    Build a list of command line mount unit definitions for the snapshot set
    ``snapset``. Mount points that are not part of the snapset are substituted
    from /etc/fstab.

    :param snapset: The snapshot set to build a mount list for.
    """
    mounts = []
    snapset_mounts = snapset.mount_points
    with open(ETC_FSTAB, "r", encoding="utf8") as fstab:
        for line in fstab.readlines():
            if line == "\n" or line.startswith("#"):
                continue
            what, where, fstype, options, _, _ = line.split()
            if where == "/":
                continue
            if where in snapset_mounts:
                snapshot = snapset.snapshot_by_mount_point(where)
                mounts.append(f"{snapshot.devpath}:{where}:{fstype}:{options}")
            else:
                mounts.append(f"{what}:{where}:{fstype}:{options}")
    return mounts


def create_snapset_boot_entry(snapset, title=None):
    """
    Create a boom boot entry to boot into the snapshot set represented by
    ``snapset``.

    :param snapset: The snapshot set for which to create a boot entry.
    :param title: An optional title for the boot entry. If ``title`` is
                  ``None`` the boot entry will be titled as
                  "Snapshot snapset_name snapset_time".
    """
    title = title or f"Snapshot {snapset.name} {snapset.time}"
    version = _get_uts_release()
    machine_id = _get_machine_id()
    root_device = _find_snapset_root(snapset).devpath
    mounts = _build_snapset_mount_list(snapset)
    osp = match_os_profile_by_version(version)
    entry_arg = f"{SNAPSET_ARG}={snapset.uuid}"
    boom.command.create_entry(
        title,
        version,
        machine_id,
        root_device,
        profile=osp,
        no_fstab=True,
        mounts=mounts,
        add_opts=entry_arg,
    )
    _log_debug(
        "Created boot entry '%s' for snapshot set with UUID=%s", title, snapset.uuid
    )


def delete_snapset_boot_entry(snapset):
    """
    Delete the boot entry corresponding to ``snapset``.

    :param snapset: The snapshot set for which to remove a boot entry.
    """
    if snapset.boot_entry is None:
        return
    selection = boom.Selection(boot_id=snapset.boot_entry.boot_id)
    boom.command.delete_entries(selection=selection)


def create_snapset_rollback_entry(snapset, title=None):
    """
    Create a boom boot entry to roll back the snapshot set represented by
    ``snapset``.

    :param snapset: The snapshot set for which to create a rollback entry.
    :param title: An optional title for the rollback entry. If ``title`` is
                  ``None`` the rollback entry will be titled as
                  "Rollback snapset_name snapset_time".
    """
    title = title or f"Rollback {snapset.name} {snapset.time}"
    version = _get_uts_release()
    machine_id = _get_machine_id()
    root_snapshot = _find_snapset_root(snapset)
    root_device = root_snapshot.origin
    osp = match_os_profile_by_version(version)
    entry_arg = f"{ROLLBACK_ARG}={snapset.uuid}"
    boom.command.create_entry(
        title,
        version,
        machine_id,
        root_device,
        profile=osp,
        add_opts=entry_arg,
    )
    _log_debug(
        "Created rollback entry '%s' for snapshot set with UUID=%s", title, snapset.uuid
    )


def delete_snapset_rollback_entry(snapset):
    """
    Delete the rollback entry corresponding to ``snapset``.

    :param snapset: The snapshot set for which to remove a rollback entry.
    """
    if snapset.rollback_entry is None:
        return
    selection = boom.Selection(boot_id=snapset.rollback_entry.boot_id)
    boom.command.delete_entries(selection=selection)


class BootEntryCache(dict):
    """
    Cache mapping snapshot sets to boom ``BootEntry`` instances.

    Boot entries in the cache are either snapshot set boot entries or rollback
    entries depending on the value of ``entry_arg``.
    """

    def __init__(self, entry_arg):
        super().__init__()
        self.entry_arg = entry_arg
        self.refresh_cache()

    def _parse_entry(self, boot_entry):
        """
        Parse a boom ``BootEntry`` options string and return the value
        of the ``snapm.snapset`` argument if present.

        :param boot_entry: The boot entry to process.
        :returns: The snapset name for the boot entry.
        """
        for word in boot_entry.options.split():
            if word.startswith(self.entry_arg):
                _, value = word.split("=")
                return value
        return None

    def refresh_cache(self):
        """
        Generate mappings from snapshot sets to boot entries.
        """
        self.clear()
        entries = boom.command.find_entries()
        for boot_entry in entries:
            snapset = self._parse_entry(boot_entry)
            if snapset:
                self[snapset] = boot_entry


class BootCache:
    """
    Set of caches mapping snapshot sets to boot entries and rollback entries.
    """

    def __init__(self):
        self.entry_cache = BootEntryCache(SNAPSET_ARG)
        _log_debug(
            "Initialised boot entry cache with %d entries", len(self.entry_cache)
        )
        self.rollback_cache = BootEntryCache(ROLLBACK_ARG)
        _log_debug(
            "Initialised rollback boot entry cache with %d entries",
            len(self.rollback_cache),
        )

    def refresh_cache(self):
        self.entry_cache.refresh_cache()
        _log_debug(
            "Refreshed boot entry cache with %d entries", len(self.entry_cache)
        )
        self.rollback_cache.refresh_cache()
        _log_debug(
            "Refreshed rollback boot entry cache with %d entries",
            len(self.rollback_cache),
        )
