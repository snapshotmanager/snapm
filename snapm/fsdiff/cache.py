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
from typing import Dict, Optional, Tuple, TYPE_CHECKING
from datetime import datetime
from uuid import UUID, uuid5
from io import RawIOBase
from math import floor
import logging
import pickle
import lzma
import os

try:
    import zstandard as zstd

    _HAVE_ZSTD = True
except ModuleNotFoundError:
    _HAVE_ZSTD = False

from snapm import (
    NAMESPACE_SNAPSHOT_SET,
    get_current_rss,
    get_total_memory,
    SnapmNotFoundError,
    SnapmInvalidIdentifierError,
)
from snapm._snapm import _check_snapm_dir

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
_SNAPM_CACHE_DIR: str = "/var/cache/snapm"

#: Cache directory file mode
_SNAPM_CACHE_MODE: int = 0o700

#: Directory for storing diff cache files
_DIFF_CACHE_DIR: str = "/var/cache/snapm/diffcache"

#: Default cache expiry: 15m
_CACHE_EXPIRES_SECS: int = 900

#: Magic number to represent default expiry time.
_DEFAULT_EXPIRES: int = -1

#: Magic number for fake root file system timestamp
_ROOT_TIMESTAMP: int = 282528000

#: Maximum fraction of memory used to attempt caching
_MAX_RSS_FRACTION = 0.6

#: Compression types
_COMPRESSION_EXTENSIONS: Dict[str, str] = {
    None: "cache",
    "lzma": "xz",
    "zstd": "zst",
}


def _should_cache(options: DiffOptions) -> bool:
    """
    Determine whether it is safe to attempt writing the cache given system
    memory constraints and current state.

    :param options: Change detection options including ``no_mem_check``.
    :type options: ``DiffOptions``
    :returns: ``True`` if caching should be attempted or ``False`` otherwise.
    :rtype: ``bool``
    """
    if not options.include_content_diffs or options.no_mem_check:
        return True

    memtotal = get_total_memory()
    rss = get_current_rss()

    if not memtotal:
        _log_warn("Cannot determine available system memory: disabling cache writing")
        return False

    if not rss:
        _log_warn(
            "Cannot determine current process memory use: disabling cache writing"
        )
        return False

    fraction = rss / memtotal
    if fraction > _MAX_RSS_FRACTION:
        _log_warn(
            "Current RSS exceeds safe threshold of system memory: disabling cache (%d%% > %d%%)",
            round(fraction * 100),
            round(_MAX_RSS_FRACTION * 100),
        )
        return False

    return True


def _check_dirs():
    """
    Check structure and permissions of cache directories.
    """
    _check_snapm_dir(_SNAPM_CACHE_DIR, _SNAPM_CACHE_MODE, "snapm cache dir")
    _check_snapm_dir(_DIFF_CACHE_DIR, _SNAPM_CACHE_MODE, "diff cache dir")


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


def _compress_type() -> Tuple[str, Tuple[Exception, ...]]:
    """
    Determine the compression type to use when writing the diffcache.

    :returns: A 2-tuple containing a string describing the chosen
              compression format or and a tuple of exception types for the
              chosen format.
    :rtype: ``Tuple[str, Tuple[Exception, ...]]``
    """
    compress = None
    compress_errors = ()
    if _HAVE_ZSTD:
        compress = "zstd"
        compress_errors = (zstd.ZstdError,)
    else:
        compress = "lzma"
        compress_errors = (lzma.LZMAError,)
    return (compress, compress_errors)


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

    def _read_cache(
        reader: RawIOBase,
        progress: ProgressBase,
        name: str,
        errors: Tuple[Exception, ...],
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
            if candidate.count:
                progress.start(candidate.count)
        except AttributeError as attr_err:
            _log_debug(
                "Ignoring mismatched cache file version: %s",
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
            except EOFError:
                if len(records) == candidate.count:
                    break
                _log_error("Detected truncated cache file %s", name)
                progress.cancel("Error.")
                raise
            except (OSError, pickle.UnpicklingError, *errors):
                progress.cancel("Error.")
                raise

        end_time = datetime.now()
        if candidate.count:
            progress.end(
                f"Loaded {candidate.count} records from diffcache "
                f"in {end_time - start_time}"
            )
        return FsDiffResults(records, candidate.options, candidate.timestamp)

    for file_name in os.listdir(_DIFF_CACHE_DIR):
        if file_name.rsplit(".", 1)[-1] not in _COMPRESSION_EXTENSIONS.values():
            _log_debug(
                "Ignoring cache file with unknown compression type: %s", file_name
            )
            continue

        uncompress = None
        uncompress_errors = ()
        cache_name = file_name
        if file_name.endswith(".zst"):
            if not _HAVE_ZSTD:
                _log_warn(
                    "Ignoring zstd compressed cache file: zstd support not available"
                )
                continue
            cache_name = file_name.removesuffix(".zst")
            uncompress = "zstd"
            uncompress_errors = (zstd.ZstdError,)
        elif file_name.endswith(".xz"):
            cache_name = file_name.removesuffix(".xz")
            uncompress = "lzma"
            uncompress_errors = (lzma.LZMAError,)

        try:
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

        if str(uuid_a) == load_uuid_a and str(uuid_b) == load_uuid_b:
            progress = ProgressFactory.get_progress(
                "Loading cache",
                quiet=quiet,
                term_control=term_control,
            )
            try:
                results = None
                if uncompress == "zstd":
                    dctx = zstd.ZstdDecompressor()
                    with open(cache_path, mode="rb") as fp:
                        with dctx.stream_reader(fp) as reader:
                            results = _read_cache(
                                reader, progress, file_name, uncompress_errors
                            )
                elif uncompress == "lzma":
                    with lzma.LZMAFile(filename=cache_path, mode="rb") as reader:
                        results = _read_cache(
                            reader, progress, file_name, uncompress_errors
                        )
                else:
                    raise SnapmNotFoundError(f"Unknown compression type: {uncompress}")
                if results is None:
                    continue
                return results
            except (
                OSError,
                EOFError,
                pickle.UnpicklingError,
                *uncompress_errors,
            ) as err:
                _log_warn("Deleting unreadable cache file %s: %s", file_name, err)
                try:
                    os.unlink(cache_path)
                except OSError as err2:
                    _log_error(
                        "Error unlinking unreadable cache file %s: %s", file_name, err2
                    )
                if progress.total:
                    progress.cancel("Error.")
                continue
            except KeyboardInterrupt:
                if progress.total:
                    progress.cancel("Quit!")
                raise
            except SystemExit:
                if progress.total:
                    progress.cancel("Exiting.")
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

    if not _should_cache(results.options):
        return

    term_control = term_control or TermControl(color=color)

    progress = ProgressFactory.get_progress(
        "Saving cache",
        quiet=quiet,
        term_control=term_control,
    )

    count = len(results)

    compress, compress_errors = _compress_type()

    cache_path += "." + _COMPRESSION_EXTENSIONS[compress]

    start_time = datetime.now()
    if count:
        progress.start(count)

    def _write_cache(results: FsDiffResults, writer: RawIOBase):
        """
        Helper to write pickled cache objects to file.

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
        if not count:
            return
        for i, record in enumerate(results):
            progress.progress(i, f"Saving record {i}")
            pickle.dump(record, writer)
            writer.flush()

    try:
        if compress == "zstd":
            cctx = zstd.ZstdCompressor()
            with open(cache_path, "wb") as fc:
                with cctx.stream_writer(fc) as compressor:
                    _write_cache(results, compressor)
        elif compress == "lzma":
            with lzma.LZMAFile(filename=cache_path, mode="wb") as compressor:
                _write_cache(results, compressor)
        else:
            raise SnapmNotFoundError(f"Unknown compression type: {compress}")

    except (OSError, pickle.PicklingError, *compress_errors) as err:
        _log_error("Error saving cache: %s", err)
        if count:
            progress.cancel("Error.")
        raise
    except KeyboardInterrupt:
        if count:
            progress.cancel("Quit!")
        raise
    except SystemExit:
        if count:
            progress.cancel("Exiting.")
        raise

    end_time = datetime.now()
    if count:
        progress.end(f"Saved {count} records to diffcache in {end_time - start_time}")


__all__ = [
    "load_cache",
    "save_cache",
]
