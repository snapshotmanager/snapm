# Copyright Red Hat
#
# snapm/manager/boot.py - Snapshot Manager boot support
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Boot integration for snapshot manager
"""
from os import uname
from os.path import exists as path_exists
import logging

import boom
import boom.cache
import boom.command
from boom.config import load_boom_config
from boom.bootloader import (
    OPTIONAL_KEYS,
    optional_key_default,
    key_to_bls_name,
)
from boom.osprofile import match_os_profile_by_version

from snapm import (
    SnapmNotFoundError,
    SnapmCalloutError,
    ETC_FSTAB,
)

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error

#: Path to the system machine-id file
_MACHINE_ID = "/etc/machine-id"
#: Path to the legacy system machine-id file
_DBUS_MACHINE_ID = "/var/lib/dbus/machine-id"
#: Path to the system os-release file
_OS_RELEASE = "/etc/os-release"

#: Snapshot set kernel command line argument
SNAPSET_ARG = "snapm.snapset"

#: Snapshot set revert kernel command line argument
REVERT_ARG = "snapm.revert"

#: /dev path prefix
_DEV_PREFIX = "/dev/"


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

    with open(path, "r", encoding="utf8") as file:
        try:
            machine_id = file.read().strip()
        except OSError as err:
            _log_error("Could not read machine-id from '%s': %s", path, err)
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
    # Note: add fstab lookup for non-root snapsets
    # needs either root=UUID/LABEL support in boom or a lookup to resolve any
    # UUID/LABEL found in the file.
    raise SnapmNotFoundError(f"Could not find root device for snapset {snapset.name}")


def _create_default_os_profile():
    """
    Create a default boom OsProfile for the running system.
    This uses the boom API to run the equivalent of:
      `boom profile create --from-host`.
    """
    options = boom.command.os_options_from_cmdline()
    _log_info(
        "Creating default boot profile from %s with options %s", _OS_RELEASE, options
    )
    return boom.command.create_profile(
        None, None, None, None, profile_file=_OS_RELEASE, options=options
    )


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


def _create_boom_boot_entry(
    version, title, tag_arg, root_device, lvm_root_lv=None, mounts=None
):
    """
    Create a boom boot entry according to the passed arguments.

    :param version: The UTS release name for the boot entry
    :param title: The title for the boot entry.
    :param tag_arg: A tag argument to be added to the kernel command line and
                    used to associate the entry with a snapshot set name or
                    UUID.
    :param root_device: The root device for the entry. Passed to root=...
    :param lvm_root_lv: An optional LVM2 root logical volume.
    :param mounts: An optional list of mount specifications to use for the
                   boot entry. If defined fstab=no will be appended to the
                   generated kernel command line.
    """
    assert title is not None, "Boot entry argument title must have a value"
    assert version is not None, "Boot entry argument version must have a value"
    assert tag_arg is not None, "Boot entry argument tag_arg must have a value"
    assert root_device is not None, "Boot entry argument root_device must have a value"

    machine_id = _get_machine_id()

    osp = match_os_profile_by_version(version)
    if not osp:
        try:
            osp = _create_default_os_profile()
        except ValueError as err:
            raise SnapmCalloutError(
                f"Error calling boom to create default OsProfile: {err}"
            ) from err

    if mounts:
        add_opts = f"rw {tag_arg}"
        del_opts = "ro"
    else:
        add_opts = tag_arg
        del_opts = None

    entry = boom.command.create_entry(
        title,
        version,
        machine_id,
        root_device,
        lvm_root_lv=lvm_root_lv,
        profile=osp,
        add_opts=add_opts,
        del_opts=del_opts,
        write=False,
        images=boom.command.I_BACKUP,
        no_fstab=bool(mounts),
        mounts=mounts,
    )

    # Apply defaults for optional keys enabled in profile
    for opt_key in OPTIONAL_KEYS:
        bls_key = key_to_bls_name(opt_key)
        if bls_key in osp.optional_keys:
            setattr(entry, bls_key, optional_key_default(opt_key))

    # Write BLS snippet for entry
    entry.write_entry()

    return entry


def create_snapset_boot_entry(snapset, title=None):
    """
    Create a boom boot entry to boot into the snapshot set represented by
    ``snapset``.

    :param snapset: The snapshot set for which to create a boot entry.
    :param title: An optional title for the boot entry. If ``title`` is
                  ``None`` the boot entry will be titled as
                  "Snapshot snapset_name snapset_time".
    """
    version = _get_uts_release()
    title = title or f"Snapshot {snapset.name} {snapset.time} ({version})"
    root_snapshot = _find_snapset_root(snapset)
    root_device = root_snapshot.devpath
    if root_snapshot.provider.name in ("lvm2-cow", "lvm2-thin"):
        lvm_root_lv = root_snapshot.name
    else:
        lvm_root_lv = None
    mounts = _build_snapset_mount_list(snapset)
    snapset.boot_entry = _create_boom_boot_entry(
        version,
        title,
        f"{SNAPSET_ARG}={snapset.uuid}",
        root_device,
        lvm_root_lv=lvm_root_lv,
        mounts=mounts,
    )
    _log_debug(
        "Created boot entry '%s' for snapshot set with UUID=%s", title, snapset.uuid
    )


def create_snapset_revert_entry(snapset, title=None):
    """
    Create a boom boot entry to revert the snapshot set represented by
    ``snapset``.

    :param snapset: The snapshot set for which to create a revert entry.
    :param title: An optional title for the revert entry. If ``title`` is
                  ``None`` the revert entry will be titled as
                  "Revert snapset_name snapset_time".
    """
    version = _get_uts_release()
    title = title or f"Revert {snapset.name} {snapset.time} ({version})"
    root_snapshot = _find_snapset_root(snapset)
    root_device = root_snapshot.origin
    if root_snapshot.provider.name in ("lvm2-cow", "lvm2-thin"):
        lvm_root_lv = root_device.removeprefix(_DEV_PREFIX)
    else:
        lvm_root_lv = None
    snapset.revert_entry = _create_boom_boot_entry(
        version,
        title,
        f"{REVERT_ARG}={snapset.uuid}",
        root_device,
        lvm_root_lv=lvm_root_lv,
    )
    _log_debug(
        "Created revert entry '%s' for snapshot set with UUID=%s", title, snapset.uuid
    )


def _delete_boot_entry(boot_id):
    """
    Delete a boom boot entry by ID.

    :param boot_id: The boot identifier to delete.
    """
    selection = boom.Selection(boot_id=boot_id)
    boom.command.delete_entries(selection=selection)
    boom.cache.clean_cache()


def delete_snapset_boot_entry(snapset):
    """
    Delete the boot entry corresponding to ``snapset``.

    :param snapset: The snapshot set for which to remove a boot entry.
    """
    if snapset.boot_entry is None:
        return
    _delete_boot_entry(snapset.boot_entry.boot_id)


def delete_snapset_revert_entry(snapset):
    """
    Delete the revert entry corresponding to ``snapset``.

    :param snapset: The snapshot set for which to remove a revert entry.
    """
    if snapset.revert_entry is None:
        return
    _delete_boot_entry(boot_id=snapset.revert_entry.boot_id)


def check_boom_config():
    """
    Check for boom configuration and create the default config if not found.
    """
    try:
        load_boom_config()
    except ValueError:
        _log_info("No boom configuration found: generating default config in /boot")
        boom.command.create_config()


class BootEntryCache(dict):
    """
    Cache mapping snapshot sets to boom ``BootEntry`` instances.

    Boot entries in the cache are either snapshot set boot entries or revert
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
    Set of caches mapping snapshot sets to boot entries and revert entries.
    """

    def __init__(self):
        self.entry_cache = BootEntryCache(SNAPSET_ARG)
        _log_debug(
            "Initialised boot entry cache with %d entries", len(self.entry_cache)
        )
        self.revert_cache = BootEntryCache(REVERT_ARG)
        _log_debug(
            "Initialised revert boot entry cache with %d entries",
            len(self.revert_cache),
        )

    def refresh_cache(self):
        """
        Refresh the cache of boot entry mappings held by this ``BootCache``
        instance.
        """
        self.entry_cache.refresh_cache()
        _log_debug("Refreshed boot entry cache with %d entries", len(self.entry_cache))
        self.revert_cache.refresh_cache()
        _log_debug(
            "Refreshed revert boot entry cache with %d entries",
            len(self.revert_cache),
        )
