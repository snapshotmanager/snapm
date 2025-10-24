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
from typing import List, Optional, Tuple
import logging

import boom
import boom.cache
import boom.command
from boom.config import load_boom_config, BoomConfigError
from boom.bootloader import (
    OPTIONAL_KEYS,
    optional_key_default,
    key_to_bls_name,
)
from boom.osprofile import match_os_profile_by_version

from snapm import (
    SnapmCalloutError,
    FsTabReader,
    find_snapset_root,
    build_snapset_mount_list,
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


def _escape(orig: str) -> str:
    """
    Convert literal ':' characters into the hexadecimal escape (\x3a): systemd
    will decode these when reading systemd.{mount,swap}-extra values.

    :param orig: The original string possibly containing literal ':' characters.
    :returns: An escaped string with ':' replaced by '\x3a'.
    """
    return orig.replace(":", r"\x3a")


def _get_uts_release():
    """
    Return the UTS release (kernel version) of the running system.

    :returns: A string representation of the UTS release value.
    """
    return uname()[2]


def _get_machine_id() -> Optional[str]:
    """
    Return the current host's machine-id.

    Get the machine-id value for the running system by reading from
    ``/etc/machine-id`` and return it as a string.

    :returns: The ``machine_id`` as a string, or ``None`` if not available.
    :rtype: Optional[str]
    """
    if path_exists(_MACHINE_ID):
        path = _MACHINE_ID
    elif path_exists(_DBUS_MACHINE_ID):  # pragma: no cover
        path = _DBUS_MACHINE_ID
    else:
        return None

    with open(path, "r", encoding="utf8") as file:
        try:
            machine_id = file.read().strip()
        except OSError as err:  # pragma: no cover
            _log_error("Could not read machine-id from '%s': %s", path, err)
            machine_id = None
    return machine_id


def _create_default_os_profile():
    """
    Create a default boom OsProfile for the running system.
    This uses the boom API to run the equivalent of:
    ``boom profile create --from-host``.
    """
    options = boom.command.os_options_from_cmdline()
    _log_info(
        "Creating default boot profile from %s with options %s", _OS_RELEASE, options
    )
    return boom.command.create_profile(
        None, None, None, None, profile_file=_OS_RELEASE, options=options
    )


def _mount_list_to_units(mount_list: List[Tuple[str, str, str, str]]) -> List[str]:
    """
    Transform a list of ``(what, where, fstype, options)`` tuples into systemd
    command line mount unit notation.

    :param mount_list: The list of mount tuples to transform.
    :returns: A list of systemd.mount-extra= argument strings.
    :rtype: ``List[str]``
    """
    mounts = []

    for mount in mount_list:
        what, where, fstype, options = mount
        mounts.append(f"{_escape(what)}:{_escape(where)}:{fstype}:{_escape(options)}")
    return mounts


def _build_swap_list(fstab: FsTabReader) -> List[str]:
    """
    Build a list of command line swap unit definitions for the running system.
    Swap entries are extracted from /etc/fstab and returned as a list of
    "WHAT:OPTIONS" strings.
    """
    swaps = []
    for entry in fstab:
        what, where, fstype, options, _, _ = entry
        if fstype != "swap":
            continue
        if where != "none":
            _log_warn(
                "Swap entry %s has invalid mount point '%s' (expected 'none')",
                what,
                where,
            )
        swaps.append(f"{_escape(what)}:{_escape(options)}")
    return swaps


def _create_boom_boot_entry(
    version, title, tag_arg, root_device, mounts=None, swaps=None
):
    """
    Create a boom boot entry according to the passed arguments.

    :param version: The UTS release name for the boot entry
    :param title: The title for the boot entry.
    :param tag_arg: A tag argument to be added to the kernel command line and
                    used to associate the entry with a snapshot set name or
                    UUID.
    :param root_device: The root device for the entry. Passed to root=...
    :param mounts: An optional list of mount specifications to use for the
                   boot entry. If defined fstab=no will be appended to the
                   generated kernel command line.
    :param swaps: An optional list of swap specifications to use for the
                  boot entry. Each entry should be in the format "WHAT:OPTIONS".
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
        except ValueError as err:  # pragma: no cover
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
        profile=osp,
        add_opts=add_opts,
        del_opts=del_opts,
        write=False,
        images=boom.command.I_BACKUP,
        no_fstab=bool(mounts),
        mounts=mounts,
        swaps=swaps,
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

    fstab = FsTabReader()
    root_device = find_snapset_root(snapset, fstab)
    mounts = _mount_list_to_units(build_snapset_mount_list(snapset, fstab))
    swaps = _build_swap_list(fstab)

    snapset.boot_entry = _create_boom_boot_entry(
        version,
        title,
        f"{SNAPSET_ARG}={snapset.uuid}",
        root_device,
        mounts=mounts,
        swaps=swaps,
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

    fstab = FsTabReader()
    root_device = find_snapset_root(snapset, fstab, origin=True)

    snapset.revert_entry = _create_boom_boot_entry(
        version,
        title,
        f"{REVERT_ARG}={snapset.uuid}",
        root_device,
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
    not_found_err = SnapmCalloutError("No usable boom configuration found")
    try:
        load_boom_config()
    except ValueError:  # pragma: no cover
        _log_warn(
            "No boom configuration found: attempting to generate defaults in /boot"
        )
        try:
            boom.command.create_config()
        except AttributeError as err:
            _log_error("Installed boom version does not support create_config()")
            raise not_found_err from err
        except (BoomConfigError, FileExistsError) as err:
            _log_error("Missing or invalid boom configuration: %s", err)
            raise not_found_err from err
        except OSError as err:
            _log_error("Error creating boom configuration: %s", err)
            raise not_found_err from err


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
