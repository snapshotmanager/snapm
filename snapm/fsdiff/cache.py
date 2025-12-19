# Copyright Red Hat
#
# snapm/fsdiff/cache.py - Snapshot Manager fs diff cache
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
File system diff cache
"""
from stat import S_ISDIR, S_ISLNK
from typing import TYPE_CHECKING
from datetime import datetime
from uuid import UUID, uuid5
from math import floor
import logging
import pickle
import lzma
import os

from snapm import (
    NAMESPACE_SNAPSHOT_SET,
    SnapmSystemError,
    SnapmNotFoundError,
    SnapmInvalidIdentifierError,
)

from .engine import FsDiffResults
from .options import DiffOptions


if TYPE_CHECKING:
    from snapm.manager._mounts import Mount


_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


#: Top-level snapm cache directory
_SNAPM_CACHE_DIR = "/var/cache/snapm"

#: Cache directory file mode
_SNAPM_CACHE_MODE = 0o700

#: Directory for storing diff cache files
_DIFF_CACHE_DIR = "/var/cache/snapm/diffcache"

#: Default cache expiry: 15m
_CACHE_EXPIRES_SECS = 900

#: Magic number to represent default expiry time.
_DEFAULT_EXPIRES = -1

#: Magic number for fake root file system timestamp
_ROOT_TIMESTAMP = 282528000

#: Location of the meminfo file in procfs
_PROC_MEMINFO = "/proc/meminfo"


def _get_dict_size() -> int:
    """
    Return an LZMA dictionary size appropriate for the running system:

    memtotal <= 4GiB: 256MiB
    memtotal <= 8GiB: 512MiB
    else: 1536GiB

    :returns: An integer dictionary size value.
    :rtype: ``int``
    """
    small = 0  # 0GiB - 4GiB
    medium = 4 * 2**30  # 4GiB - 8GiB
    large = 8 * 2**30  # > 8GIB

    dict_sizes = {
        small: 256 * 2**20,  # 256MiB
        medium: 512 * 2**20,  # 512MiB
        large: 1536 * 2**20,  # 1536MiB
    }

    memtotal = 0
    with open(_PROC_MEMINFO, "r", encoding="utf8") as fp:
        for line in fp.readlines():
            print(f"Line: {line}")
            if line.startswith("MemTotal"):
                try:
                    _, memtotal_str, _ = line.split()
                    memtotal = int(memtotal_str) * 2**10
                    break
                except ValueError as err:
                    _log_debug(
                        "Could not parse %s line '%s': %s", _PROC_MEMINFO, line, err
                    )

    dict_size = dict_sizes[small]
    for thresh, size in dict_sizes.items():
        if memtotal >= thresh:
            dict_size = size

    return dict_size


def _check_cache_dir(dirpath: str, mode: int, name: str) -> str:
    """
    Check for the presence of a snapm runtime directory and create
    it if necessary.

    :param dirpath: Path to the directory
    :param mode: Permissions mode for the directory
    :param name: Human-readable name for error messages
    :returns: The directory path
    """
    # Check if path exists and validate it's a proper directory
    existed = os.path.exists(dirpath)
    if existed:
        try:
            st = os.lstat(dirpath)
            if S_ISLNK(st.st_mode):
                raise SnapmSystemError(f"{name} {dirpath} is a symlink (not secure)")
            if not S_ISDIR(st.st_mode):
                raise SnapmSystemError(
                    f"{name} {dirpath} exists but is not a directory"
                )
        except OSError as err:
            raise SnapmSystemError(f"Failed to stat {name} {dirpath}: {err}") from err

    # Create directory if it doesn't exist
    try:
        os.makedirs(dirpath, mode=mode, exist_ok=True)
    except OSError as err:
        raise SnapmSystemError(f"Failed to create {name} {dirpath}: {err}") from err

    # Ensure correct permissions (only if directory already existed and differs)
    if existed:
        try:
            st = os.stat(dirpath)
            if (st.st_mode & 0o777) != mode:
                os.chmod(dirpath, mode)
        except OSError as err:
            raise SnapmSystemError(
                f"Failed to set permissions on {name} {dirpath}: {err}"
            ) from err

    # Validate final permissions
    try:
        st = os.stat(dirpath)
        if (st.st_mode & 0o777) != mode:
            raise SnapmSystemError(
                f"{name} {dirpath} has incorrect permissions: "
                f"{st.st_mode & 0o777:04o} (expected {mode:04o})"
            )
    except OSError as err:
        raise SnapmSystemError(f"Failed to verify {name} {dirpath}: {err}") from err

    return dirpath


def _check_dirs():
    """
    Check structure and permissions of cache directories.
    """
    _check_cache_dir(_SNAPM_CACHE_DIR, _SNAPM_CACHE_MODE, "snapm cache dir")
    _check_cache_dir(_DIFF_CACHE_DIR, _SNAPM_CACHE_MODE, "diff cache dir")


def _root_uuid(root: "Mount") -> UUID:
    """
    Calculate a fake SnapshotSet UUID for the root file system
    at the current time.

    :param root: The ``SysMount`` instance representing the root
                 file system.
    :type root: ``Mount``
    :returns: A UUID representing the current root file system.
    :rtype: ``UUID``
    """
    return uuid5(NAMESPACE_SNAPSHOT_SET, root.name + str(_ROOT_TIMESTAMP))


def _cache_name(mount_a: "Mount", mount_b: "Mount", results: FsDiffResults) -> str:
    """
    Generate a name for the cache file for ``mount_a``, ``mount_b`` and
    ``results``.

    :param mount_a: The first (left hand) mount.
    :type mount_a: ``Mount``
    :param mount_b: The second (right hand) mount.
    :type mount_b: ``Mount``
    :param results: The results to cache.
    :type results: ``FsDiffResults``
    :returns: The cache filename.
    :rtype: ``str``
    """
    uuid_a = mount_a.snapset.uuid if mount_a.snapset else _root_uuid(mount_a)
    uuid_b = mount_b.snapset.uuid if mount_b.snapset else _root_uuid(mount_b)

    if uuid_a == uuid_b:
        raise SnapmInvalidIdentifierError(
            "Attempting to cache diff results with mount_a == mount_b"
        )

    timestamp = results.timestamp
    opts_hash = abs(hash(results.options))

    return f"{uuid_a!s}.{uuid_b!s}.{opts_hash}.{timestamp}.cache"


# pylint: disable=too-many-locals
# pylint: disable=too-many-branches
# pylint: disable=too-many-statements
def load_cache(
    mount_a: "Mount",
    mount_b: "Mount",
    options: DiffOptions,
    expires: int = _DEFAULT_EXPIRES,
) -> FsDiffResults:
    """
    Attempt to load diff cache for ``mount_a``/``mount_b``.

    :param mount_a: The first (left hand) mount to retrieve.
    :type mount_a: ``Mount``
    :param mount_b: The second (right hand) mount to retrieve.
    :type mount_b: ``Mount``
    :param expires: The expiry time for cache entries in seconds (0 to disable
                    expiry).
    :type expires: ``int``
    :returns: The diff results for the comparison.
    :rtype: ``FsDiffResults``

    """

    def _valid(timestamp: int, expires: int = _CACHE_EXPIRES_SECS) -> bool:
        """
        Check whether cache taken at ``timestamp`` is expired.

        :param timestamp: The cache timestamp in UNIX epoch format.
        :type timestamp: ``int``
        :returns: ``True`` if the cache is valid else ``False``.
        :rtype: ``bool``
        """
        now = floor(datetime.now().timestamp())
        if not expires or (timestamp >= (now - expires)):
            return True
        return False

    _check_dirs()

    expires = _CACHE_EXPIRES_SECS if expires == _DEFAULT_EXPIRES else expires

    uuid_a = mount_a.snapset.uuid if mount_a.snapset else _root_uuid(mount_a)
    uuid_b = mount_b.snapset.uuid if mount_b.snapset else _root_uuid(mount_b)

    if uuid_a == uuid_b:
        raise SnapmInvalidIdentifierError(
            "Attempting to load diff results with mount_a == mount_b"
        )

    for file_name in os.listdir(_DIFF_CACHE_DIR):
        if not file_name.endswith(".cache"):
            continue

        try:
            (load_uuid_a, load_uuid_b, _, timestamp_str, _) = file_name.split(".")
        except ValueError:
            _log_debug("Ignoring cache file with malformed name: %s", file_name)
            continue

        try:
            timestamp = int(timestamp_str)
        except ValueError:
            _log_debug("Ignoring cache file with invalid timestamp: %s", file_name)
            continue

        cache_path = os.path.join(_DIFF_CACHE_DIR, file_name)
        if not _valid(timestamp, expires=expires):
            _log_info("Pruning expired cache file %s", file_name)
            try:
                os.unlink(cache_path)
            except OSError as err:
                _log_error("Error unlinking stale cache file %s: %s", file_name, err)
            continue

        if str(uuid_a) == load_uuid_a and str(uuid_b) == load_uuid_b:
            try:
                with lzma.open(cache_path, mode="rb") as fp:
                    # We trust the files in /var/cache/snapm/diffcache since the
                    # directory is root-owned and has restrictive permissions that
                    # are verified on startup.
                    candidate = pickle.load(fp)
                    _log_debug(
                        "Loaded candidate diffcache pickle from %s with options=%s",
                        file_name,
                        repr(candidate.options),
                    )
                    if candidate.options != options:
                        _log_debug("Ignoring cache entry with mismatched options")
                        continue
                    records = []
                    while True:
                        try:
                            record = pickle.load(fp)
                            if record is None:
                                break
                            records.append(record)
                        except EOFError:
                            break
                    return FsDiffResults(
                        records, candidate.options, candidate.timestamp
                    )

            except (OSError, EOFError, lzma.LZMAError, pickle.UnpicklingError) as err:
                _log_warn("Deleting unreadable cache file %s: %s", file_name, err)
                try:
                    os.unlink(cache_path)
                except OSError as err2:
                    _log_error(
                        "Error unlinking unreadable cache file %s: %s", file_name, err2
                    )
                continue

    raise SnapmNotFoundError("No matching cache file found")


def save_cache(mount_a: "Mount", mount_b: "Mount", results: FsDiffResults):
    """
    Attempt to save diff cache for ``mount_a``/``mount_b``.

    :param mount_a: The first (left hand) mount to store.
    :type mount_a: ``Mount``
    :param mount_b: The second (right hand) mount to store.
    :type mount_b: ``Mount``
    :param results: The results to cache.
    :type results: ``FsDiffResults``
    """
    _check_dirs()
    cache_name = _cache_name(mount_a, mount_b, results)
    cache_path = os.path.join(_DIFF_CACHE_DIR, cache_name)

    filters = ({"id": lzma.FILTER_LZMA2, "dict_size": _get_dict_size()},)

    # Empty version of FsDiffResults to pickle.
    results_save = FsDiffResults([], results.options, results.timestamp)

    with lzma.open(cache_path, mode="wb", filters=filters) as fp:
        pickle.dump(results_save, fp)
        for record in results:
            pickle.dump(record, fp)


__all__ = [
    "load_cache",
    "save_cache",
]
