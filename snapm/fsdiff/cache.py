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
from typing import Optional, TYPE_CHECKING
from stat import S_ISDIR, S_ISLNK
from datetime import datetime
from uuid import UUID, uuid5
from io import RawIOBase
from math import floor
import logging
import pickle
import os

try:
    import zstandard as zstd

    _HAVE_ZSTD = True
except ModuleNotFoundError:
    _HAVE_ZSTD = False

from snapm import (
    NAMESPACE_SNAPSHOT_SET,
    SnapmSystemError,
    SnapmNotFoundError,
    SnapmInvalidIdentifierError,
)

from snapm.progress import ProgressBase, ProgressFactory, TermControl

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


def _get_max_cache_records() -> int:
    """
    Generate an approximate maximum number of ``FsDiffRecord`` objects
    to attempt to compress given system memory constraints if the
    DiffOptions.include_content_diff option is enabled. This is a
    safety guard to avoid attempting to compress huge results sets on
    memory constrained systems.

    memtotal <= 4GiB: 10,000
    memtotal <= 8GiB: 50,000
    else: unlimited

    :returns: The maximum number of records to attempt to compress, or
              zero for unlimited.
    :rtype: ``int``
    """
    extra_small = 0  # < 2GiB
    small = 2 * 2**30  # 2GiB - 4GiB
    medium = 4 * 2**30  # 4GiB - 8GiB
    large = 8 * 2**30  # 8GIB - 16GiB
    extra_large = 16 * 2**30  # > 16GiB

    record_limits = {
        extra_small: 1000,
        small: 10000,
        medium: 50000,
        large: 100000,
        extra_large: 0,
    }

    memtotal = 0
    with open(_PROC_MEMINFO, "r", encoding="utf8") as fp:
        for line in fp.readlines():
            if line.startswith("MemTotal"):
                try:
                    _, memtotal_str, _ = line.split()
                    memtotal = int(memtotal_str) * 2**10
                    break
                except ValueError as err:
                    _log_debug(
                        "Could not parse %s line '%s': %s", _PROC_MEMINFO, line, err
                    )

    limit = record_limits[extra_small]
    for thresh, value in record_limits.items():
        if memtotal >= thresh:
            limit = value

    return limit


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
    color: str = "auto",
    quiet: bool = False,
    term_control: Optional[TermControl] = None,
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
    :param color: A string to control color diff rendering: "auto",
                  "always", or "never".
    :type color: ``str``
    :param quiet: Suppress progress output.
    :type quiet: ``bool``
    :param term_control: An optional ``TermControl`` instance to use for
                         formatting. The supplied instance overrides any
                         ``color`` argument if set.
    :type term_control: ``Optional[TermControl]``
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

    term_control = term_control or TermControl(color=color)

    for file_name in os.listdir(_DIFF_CACHE_DIR):
        if not file_name.endswith(".cache") and not file_name.endswith(".cache.zstd"):
            continue

        try:
            cache_name = file_name.removesuffix(".zstd")
            (load_uuid_a, load_uuid_b, _, timestamp_str, _) = cache_name.split(".")
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

        def _read_cache(
            reader: RawIOBase,
            progress: ProgressBase,
            name: str,
        ) -> Optional[FsDiffResults]:
            # We trust the files in /var/cache/snapm/diffcache since the
            # directory is root-owned and has restrictive permissions that
            # are verified on startup.
            candidate = pickle.load(reader)
            _log_debug(
                "Loaded candidate diffcache pickle from %s with options=%s",
                name,
                repr(candidate.options),
            )
            if candidate.options != options:
                _log_debug("Ignoring cache entry with mismatched options")
                return None
            records = []
            start_time = datetime.now()
            try:
                progress.start(candidate.count)
            except AttributeError as attr_err:
                _log_debug(
                    "Ignoring mistmatched cache file version: %s",
                    attr_err,
                )
                return None
            for i in range(0, candidate.count):
                progress.progress(i, f"Loading record {i}")
                try:
                    record = pickle.load(reader)
                    if record is None:
                        break
                    records.append(record)
                except KeyboardInterrupt:
                    progress.cancel("Quit!")
                    raise
                except EOFError:
                    progress.end("Done!")
                    break
            end_time = datetime.now()
            progress.end(
                f"Loaded {candidate.count} records from diffcache "
                f"in {end_time - start_time}"
            )
            return FsDiffResults(records, candidate.options, candidate.timestamp)

        if str(uuid_a) == load_uuid_a and str(uuid_b) == load_uuid_b:
            progress = ProgressFactory.get_progress(
                "Loading cache",
                quiet=quiet,
                term_control=term_control,
            )
            try:
                if cache_path.endswith(".zstd"):
                    dctx = zstd.ZstdDecompressor()
                    with open(cache_path, mode="rb") as fp:
                        with dctx.stream_reader(fp) as reader:
                            results = _read_cache(reader, progress, file_name)
                            if results is None:
                                continue
                else:
                    with open(cache_path, mode="rb") as reader:
                        results = _read_cache(reader, progress, file_name)
                        if results is None:
                            continue
                return results
            except (OSError, EOFError, pickle.UnpicklingError) as err:
                _log_warn("Deleting unreadable cache file %s: %s", file_name, err)
                try:
                    os.unlink(cache_path)
                except OSError as err2:
                    _log_error(
                        "Error unlinking unreadable cache file %s: %s", file_name, err2
                    )
                continue
            except KeyboardInterrupt:
                if progress.total:
                    progress.cancel("Quit!")
                raise

    raise SnapmNotFoundError("No matching cache file found")


def save_cache(
    mount_a: "Mount",
    mount_b: "Mount",
    results: FsDiffResults,
    color: str = "auto",
    quiet: bool = False,
    term_control: Optional[TermControl] = None,
):
    """
    Attempt to save diff cache for ``mount_a``/``mount_b``.

    :param mount_a: The first (left hand) mount to store.
    :type mount_a: ``Mount``
    :param mount_b: The second (right hand) mount to store.
    :type mount_b: ``Mount``
    :param color: A string to control color diff rendering: "auto",
                  "always", or "never".
    :type color: ``str``
    :param quiet: Suppress progress output.
    :type quiet: ``bool``
    :param term_control: An optional ``TermControl`` instance to use for
                         formatting. The supplied instance overrides any
                         ``color`` argument if set.
    :param results: The results to cache.
    :type results: ``FsDiffResults``
    """
    _check_dirs()
    cache_name = _cache_name(mount_a, mount_b, results)
    cache_path = os.path.join(_DIFF_CACHE_DIR, cache_name)

    term_control = term_control or TermControl(color=color)

    progress = ProgressFactory.get_progress(
        "Saving cache",
        quiet=quiet,
        term_control=term_control,
    )

    count = len(results)
    limit = _get_max_cache_records()
    compress = False
    if limit and count > limit and results.options.include_content_diffs:
        _log_warn(
            "Large cache with include_content_diffs detected: "
            "disabling cache compression (%d > %d)",
            count,
            limit,
        )
    else:
        if _HAVE_ZSTD:
            compress = True
            cache_path += ".zstd"

    start_time = datetime.now()
    progress.start(count)

    def _write_cache(results: FsDiffResults, writer: RawIOBase):
        """
        Helper to write pickled cache objects to file.

        :param save: The empty results header to save.
        :type save: ``FsDiffResults``
        :param results: The diff results containing records to cache.
        :type results: ``FsDiffResults``.
        :param writer: A writeable object to write to.
        :type writer: ``RawIOBase``
        """
        # Empty version of FsDiffResults to pickle.
        results_save = FsDiffResults(
            [], results.options, results.timestamp, count=len(results)
        )
        pickle.dump(results_save, writer)
        for i, record in enumerate(results):
            progress.progress(i, f"Saving record {i}")
            pickle.dump(record, writer)

    try:
        if compress:
            cctx = zstd.ZstdCompressor()
            with open(cache_path, "wb") as fc:
                with cctx.stream_writer(fc) as compressor:
                    _write_cache(results, compressor)
        else:
            with open(cache_path, "wb") as fp:
                _write_cache(results, fp)

    except (OSError, pickle.PicklingError) as err:
        _log_error("Error saving cache: %s", err)
        progress.cancel()
        raise
    except KeyboardInterrupt:
        progress.cancel("Quit!")
        raise
    end_time = datetime.now()
    progress.end(f"Saved {count} records to diffcache in {end_time - start_time}")


__all__ = [
    "load_cache",
    "save_cache",
]
