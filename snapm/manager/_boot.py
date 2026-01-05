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
import collections
from os import uname
from os.path import exists as path_exists
import logging
from typing import Optional
import subprocess

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
    SnapmNotFoundError,
    SnapmCalloutError,
    SnapmSystemError,
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


class Fstab:
    """
    A class to read and query data from an fstab file.

    This class reads an fstab-like file and provides methods to iterate
    over its entries and look up specific entries based on their properties.

    """

    # Define a named tuple to give structure to each fstab entry.
    # This makes the code more readable than using a regular tuple.
    FstabEntry = collections.namedtuple(
        "FstabEntry", ["what", "where", "fstype", "options", "freq", "passno"]
    )

    def __init__(self, path="/etc/fstab"):
        """
        Initializes the Fstab object by reading and parsing the file.

        :param path: The path to the fstab file. Defaults to '/etc/fstab'.
        :type path: str
        :raises SnapmNotFoundError: If the specified fstab file does not exist.
        :raises SnapmSystemError: If there is an error reading the fstab file.

        """
        self.path = path
        self.entries = []
        self._read_fstab()

    def _read_fstab(self):
        """
        Private method to read and parse the fstab file.
        It populates the self.entries list.
        """
        try:
            with open(self.path, "r", encoding="utf8") as f:
                for line in f:
                    # 1. Strip leading/trailing whitespace.
                    line = line.strip()

                    # 2. Ignore empty lines and comments.
                    if not line or line.startswith("#"):
                        continue

                    # 3. Split the line into parts.
                    parts = line.split()

                    # 4. An fstab entry must have 6 fields.
                    if len(parts) != 6:
                        # You might want to log a warning here in a real application.
                        continue

                    # 5. Convert freq and passno to integers for proper typing.
                    try:
                        parts[4] = int(parts[4])
                        parts[5] = int(parts[5])
                    except KeyError:
                        continue

                    # 6. Create a named tuple and append it to our list.
                    entry = self.FstabEntry(*parts)
                    self.entries.append(entry)

        except FileNotFoundError as exc:
            _log_error("Error: The file '%s' was not found.", self.path)
            raise SnapmNotFoundError(f"Fstab file not found: {self.path}") from exc
        except IOError as e:
            _log_error("Error: Could not read the file '%s': %s", self.path, e)
            raise SnapmSystemError(f"Error reading fstab file: {self.path}") from e

    def __iter__(self):
        """
        Allows iteration over the fstab entries.

        :yields:
            A 6-tuple for each entry in the fstab file:
            (what, where, fstype, options, freq, passno)
        """
        yield from self.entries

    def lookup(self, key, value):
        """
        Finds and generates all entries matching a specific key-value pair.

        :params key: The field to search by. Must be one of 'what', 'where',
                    'fstype', 'options', 'freq', or 'passno'.
        :type key: str
        :params value: The value to match for the given key.
        :type value: str|int
        :yields: A 6-tuple for each matching fstab entry.

        :raises KeyError: If the provided key is not a valid fstab field name.
        """
        if key not in self.FstabEntry._fields:
            raise KeyError(
                f"Invalid lookup key: '{key}'. "
                f"Valid keys are: {self.FstabEntry._fields}"
            )

        for entry in self.entries:
            # getattr() allows us to get an attribute by its string name.
            if getattr(entry, key) == value:
                yield entry

    def __repr__(self):
        """Return a machine readable string representation of this Fstab."""
        return f"Fstab(path='{self.path}')"


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


def get_device_path(identifier: str, by_type: str) -> Optional[str]:
    """
    Translates a filesystem UUID or label to its corresponding device path
    using the blkid command.

    :param:   identifier: The UUID or label of the filesystem.
    :param:   by_type: The type of identifier to search for.

    :returns:   The device path if found, otherwise None.
    :rtype:    Optional[str]

    :raises:   ValueError: If 'identifier' is empty or 'by_type' is not 'uuid' or 'label'.
    :raises:   SnapmNotFoundError: If the 'blkid' command is not found on the system.
    :raises:   SnapmSystemError: If the 'blkid' command exits with a non-zero status
                                due to reasons other than the identifier not being found
                                (e.g., permission issues).
    :raises:   SnapmCalloutError: For any other unexpected errors during command execution
                                  or parsing.
    """
    if not identifier:
        raise ValueError("Identifier cannot be an empty string.")
    if by_type not in ["uuid", "label"]:
        raise ValueError("Invalid 'by_type'. Must be 'uuid' or 'label'.")

    try:
        command = ["blkid"]

        if by_type == "uuid":
            command.extend(["--uuid", identifier])
        else:  # by_type == "label"
            command.extend(["--label", identifier])

        # Execute the command
        result = subprocess.run(command, capture_output=True, text=True, check=True)

        # The output of 'blkid --uuid <UUID>' or 'blkid --label <LABEL>' is simply the device path
        # if found. If not found, it returns a non-zero exit code, which
        # 'check=True' converts into a CalledProcessError.
        device_path = result.stdout.strip()

        if device_path:
            return device_path

        # This case should ideally not be reached if check=True is working as expected
        # for "not found" scenarios, but it's a safeguard.
        return None

    except FileNotFoundError as exc:
        _log_error(
            "Error: 'blkid' command not found. Please ensure it is installed and in your PATH."
        )
        raise SnapmNotFoundError("blkid command not found.") from exc
    except subprocess.CalledProcessError as e:
        # blkid returns 1 if the specified UUID/label is not found.
        # Other non-zero codes indicate different errors (e.g., permissions).
        if e.returncode == 1:
            # This is the expected behavior when the identifier is not found.
            _log_error(
                "Identifier '%s' (%s) not found by blkid. Stderr: %s",
                identifier,
                by_type,
                e.stderr.strip(),
            )
            return None

        # Other errors (e.g., permission denied) should still be raised.
        _log_error(
            "Error executing blkid command (return code %d): %s", e.returncode, e
        )
        _log_error("Stdout: %s", e.stdout.strip())
        _log_error("Stderr: %s", e.stderr.strip())
        raise SnapmSystemError(f"Error executing blkid command: {e}") from e
    except Exception as e:
        _log_error("An unexpected error occurred while executing blkid: %s", str(e))
        raise SnapmCalloutError(
            f"An unexpected error occurred while executing blkid: {e}"
        ) from e


def _find_snapset_root(snapset, origin=False):
    """
    Find the device that backs the root filesystem for snapshot set ``snapset``.

    If the snapset does not include the root volume look the device up via the
    fstab.

    :param snapset: The ``SnapshotSet`` to check.
    :param origin: Always return the origin device, even if a snapshot exists
                   for the root mount point.
    """
    for snapshot in snapset.snapshots:
        if snapshot.mount_point == "/":
            if origin:
                return snapshot.origin
            return snapshot.devpath
    dev_path = None
    fstab_reader = Fstab()
    for entry in fstab_reader.lookup("where", "/"):
        if entry.what.startswith("UUID="):
            dev_path = get_device_path(entry.what.split("=", maxsplit=1)[1], "uuid")
        if entry.what.startswith("LABEL="):
            dev_path = get_device_path(entry.what.split("=", maxsplit=1)[1], "label")
        if entry.what.startswith("/"):
            dev_path = entry.what
    if dev_path:
        return dev_path
    raise SnapmNotFoundError(f"Could not find root device for snapset {snapset.name}")


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
            parts = line.split()
            if len(parts) != 6:
                _log_warn("Skipping malformed fstab line: %s", line.strip())
                continue
            what, where, fstype, options, _, _ = parts
            if where == "/" or fstype == "swap":
                continue
            if where in snapset_mounts:
                snapshot = snapset.snapshot_by_mount_point(where)
                mounts.append(f"{_escape(snapshot.devpath)}:{_escape(where)}:{fstype}:{_escape(options)}")
            else:
                mounts.append(f"{_escape(what)}:{_escape(where)}:{fstype}:{_escape(options)}")
    return mounts


def _build_swap_list():
    """
    Build a list of command line swap unit definitions for the running system.
    Swap entries are extracted from /etc/fstab and returned as a list of
    "WHAT:OPTIONS" strings.
    """
    swaps = []
    with open(ETC_FSTAB, "r", encoding="utf8") as fstab:
        for line in fstab.readlines():
            if line == "\n" or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 6:
                _log_warn("Skipping malformed fstab line: %s", line.strip())
                continue
            what, _, fstype, options, _, _ = parts
            if fstype != "swap":
                continue
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
    if not isinstance(version, str) or not version:
        raise TypeError("Boot entry version must have a string value")
    if not isinstance(title, str) or not title:
        raise TypeError("Boot entry title must have a string value")
    if not isinstance(tag_arg, str) or not tag_arg:
        raise TypeError("Boot entry tag_arg must have a string value")
    if not isinstance(root_device, str) or not root_device:
        raise TypeError("Boot entry root_device must have a string value")

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
    root_device = _find_snapset_root(snapset)
    mounts = _build_snapset_mount_list(snapset)
    swaps = _build_swap_list()
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
    root_device = _find_snapset_root(snapset, origin=True)
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
                _, value = word.split("=", maxsplit=1)
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
