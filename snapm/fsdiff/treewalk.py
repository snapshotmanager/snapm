# Copyright Red Hat
#
# snapm/fsdiff/treewalk.py - Snapshot Manager fs differ tree walk
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Tree walking support for fsdiff.
"""
from typing import Dict, List, Optional, TYPE_CHECKING
from hashlib import md5, sha1, sha256, sha512
from fnmatch import fnmatch
from pathlib import Path
from datetime import datetime
import itertools
import logging
import stat
import os

from snapm import SNAPM_SUBSYSTEM_FSDIFF

from snapm.progress import (
    ProgressBase,
    ProgressFactory,
    TermControl,
    ThrobberBase,
)

from .filetypes import FileTypeDetector, FileTypeInfo
from .options import DiffOptions

if TYPE_CHECKING:
    from snapm.manager._mounts import Mount

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


def _log_debug_fsdiff(msg, *args, **kwargs):
    """A wrapper for fsdiff subsystem debug logs."""
    _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_FSDIFF}, **kwargs)


_HASH_TYPES = {
    "md5": md5,
    "sha1": sha1,
    "sha256": sha256,
    "sha512": sha512,
}


class FsEntry:
    """
    Representation of a single file system entry for comparison.
    """

    def __init__(
        self,
        path: str,
        strip_prefix: str,
        stat_info: os.stat_result,
        content_hash: Optional[str] = None,
        file_type_info: Optional[FileTypeInfo] = None,
    ):
        """
        Initialise a new ``FsEntry`` object.

        :param path: The full path to this ``FsEntry`` represents.
        :type path: ``str``
        :param strip_prefix: An optional leading path prefix (mount root) to
                             strip from ``path`` before storing.
        :type strip_prefix: ``str``
        :param stat_info: An ``os.stat_result`` for this ``FsEntry``.
        :type stat_info: ``os.stat_result``
        :param content_hash: A hash of the content of this ``FsEntry``.
        :type content_hash: ``str``
        :param file_type_info: A ``FileTypeInfo`` object for this ``FsEntry``.
        :type file_type_info: ``FileTypeInfo``
        """

        def _get_xattrs(path, follow_symlinks):
            """Helper to retrieve all xattrs for ``path``."""
            checker = os.path.exists if follow_symlinks else os.path.lexists
            if not checker(path):
                return {}
            return {
                xattr: os.getxattr(path, xattr, follow_symlinks=follow_symlinks)
                for xattr in os.listxattr(path, follow_symlinks=follow_symlinks)
            }

        #: The path relative to this tree's mount point
        self.path: Path = Path(path.removeprefix(strip_prefix))
        #: The full path from the host perspective
        self.full_path: Path = Path(path)
        #: An ``os.stat_result`` for this path
        self.stat: os.stat_result = stat_info
        #: File mode returned by ``stat()``
        self.mode: int = stat_info.st_mode
        #: File size returned by ``stat()``
        self.size: int = stat_info.st_size
        #: File modification time returned by ``stat()``
        self.mtime: int = stat_info.st_mtime
        #: File owner returned by ``stat()``
        self.uid: int = stat_info.st_uid
        #: File group returned by ``stat()``
        self.gid: int = stat_info.st_gid
        #: Calculated content hash or ``None`` if not available.
        self.content_hash: Optional[str] = content_hash

        if stat.S_ISLNK(stat_info.st_mode):
            if os.path.lexists(path):
                #: The symbolic link target
                self.symlink_target = os.readlink(path)
                if os.path.isabs(self.symlink_target):
                    target_for_check = self.symlink_target
                else:
                    target_for_check = os.path.join(
                        os.path.dirname(path), self.symlink_target
                    )
                #: True if symlink target not found
                self.broken_symlink = not os.path.exists(target_for_check)
                #: Extended attributes as ``Dict[str, bytes]``
                self.xattrs: Dict[str, bytes] = _get_xattrs(path, False)
            else:
                #: The symbolic link target
                self.symlink_target = None
                #: True if symlink target not found or link vanished
                self.broken_symlink = True
                #: Extended attributes as ``Dict[str, bytes]``
                self.xattrs = {}
        else:
            #: The symbolic link target: ``None`` for non-symlinks
            self.symlink_target = None
            #: Not a broken symlink
            self.broken_symlink = False
            #: Extended attributes as ``Dict[str, bytes]``
            self.xattrs: Dict[str, bytes] = _get_xattrs(path, True)

        #: An optional instance of ``FileTypeInfo`` describing this path.
        self.file_type_info = file_type_info

    def __str__(self):
        """
        Return a string representation of this ``FsEntry`` object.

        :returns: A human readable representation of this ``FsEntry``.
        :rtype: ``str``
        """
        fse_str = f"path: {self.path}, stat: {self.stat}, hash: {self.content_hash}"
        if self.symlink_target:
            fse_str += f", symlink_target: {self.symlink_target}"
        if self.xattrs:
            fse_str += ", extended_attributes: "
            xattr_strs = []
            for xattr, value in self.xattrs.items():
                xattr_strs.append(f"{xattr}={value}")
            fse_str += ", ".join(xattr_strs)
        return fse_str

    @property
    def is_file(self) -> bool:
        """
        True if this ``FsEntry`` is a regular file.

        :returns: ``True`` if this ``FsEntry`` corresponds to a regular file or
                  ``False`` otherwise.
        :rtype: ``bool``
        """
        return stat.S_ISREG(self.mode)

    @property
    def is_dir(self) -> bool:
        """
        True if this ``FsEntry`` is a directory.

        :returns: ``True`` if this ``FsEntry`` corresponds to a directory or
                  ``False`` otherwise.
        :rtype: ``bool``
        """
        return stat.S_ISDIR(self.mode)

    @property
    def is_symlink(self) -> bool:
        """
        True if this ``FsEntry`` is a symlink.

        :returns: ``True`` if this ``FsEntry`` corresponds to a symlink or
                  ``False`` otherwise.
        :rtype: ``bool``
        """
        return stat.S_ISLNK(self.mode)

    @property
    def is_block(self) -> bool:
        """
        True if this ``FsEntry`` is a block special file.

        :returns: ``True`` if this ``FsEntry`` corresponds to a block special
                  file or ``False`` otherwise.
        :rtype: ``bool``
        """
        return stat.S_ISBLK(self.mode)

    @property
    def is_char(self) -> bool:
        """
        True if this ``FsEntry`` is a character special file.

        :returns: ``True`` if this ``FsEntry`` corresponds to a character
                  special file or ``False`` otherwise.
        :rtype: ``bool``
        """
        return stat.S_ISCHR(self.mode)

    @property
    def is_sock(self) -> bool:
        """
        True if this ``FsEntry`` is a socket.

        :returns: ``True`` if this ``FsEntry`` corresponds to a socket or
                  ``False`` otherwise.
        :rtype: ``bool``
        """
        return stat.S_ISSOCK(self.mode)

    @property
    def is_fifo(self) -> bool:
        """
        True if this ``FsEntry`` is a FIFO special file.

        :returns: ``True`` if this ``FsEntry`` corresponds to a FIFO special
                  file or ``False`` otherwise.
        :rtype: ``bool``
        """
        return stat.S_ISFIFO(self.mode)

    @property
    def is_text_like(self) -> bool:
        """
        True if this ``FsEntry`` is a text-like file.

        :returns: ``True`` if this ``FsEntry`` corresponds to a text-like file
                  or ``False`` otherwise.
        :rtype: ``bool``
        """
        if not self.is_file:
            return False
        return bool(self.file_type_info and self.file_type_info.is_text_like)

    @property
    def type_desc(self):
        """
        Return a string description of the entry type: "file", "dir", or
        "symlink".

        :returns: A string description of the entry type.
        :rtype: ``str``
        """
        desc = "other"
        if self.is_file:
            desc = "file"
        elif self.is_dir:
            desc = "directory"
        elif self.is_symlink:
            desc = "symbolic link"
        elif self.is_block:
            desc = "block device"
        elif self.is_char:
            desc = "char device"
        elif self.is_sock:
            desc = "socket"
        elif self.is_fifo:
            desc = "FIFO"
        return desc


class TreeWalker:
    """
    Simple file system tree walker for comparisons.
    """

    def __init__(self, options, hash_algorithm: str = "sha256"):
        """
        Initialise a new ``TreeWalker`` object.

        :param options: Options to control this ``TreeWalker`` instance.
        :type options: ``DiffOptions``
        :param hash_algorithm: A string describing the hash algorithm to be
                               used for this ``TreeWalker`` instance.
        :type hash_algorithm: ``str``
        """
        if hash_algorithm not in _HASH_TYPES:
            raise ValueError(f"Unknown hash algorithm: {hash_algorithm}")

        self.options: DiffOptions = options
        self.hash_algorithm: str = hash_algorithm
        self.file_type_detector: FileTypeDetector = FileTypeDetector()
        self.exclude_patterns: List[str] = options.exclude_patterns or [
            "/proc/*",
            "/sys/*",
            "/dev/*",
            "/tmp/*",
            "/run/*",
            "/var/run/*",
            "/var/lock/*",
        ]
        self.file_patterns: Optional[List[str]] = options.file_patterns

        self.hasher = _HASH_TYPES[hash_algorithm]

    def _process_dir(
        self, dir_path: str, dir_stat: os.stat_result, strip_prefix: str
    ) -> FsEntry:
        """
        Process a single directory at ``dir_path`` with stat data ``dir_stat``.

        :param dir_path: The path to examine.
        :type dir_path: ``str``
        :param dir_stat: ``os.stat()`` result for ``dir_path``.
        :type dir_stat: ``os.stat_result``
        :returns: A new ``FsEntry`` representing the directory.
        :rtype: ``FsEntry``
        """
        fti = None
        if self.options.include_file_type:
            fti = self.file_type_detector.detect_file_type(dir_path)

        return FsEntry(dir_path, strip_prefix, dir_stat, file_type_info=fti)

    def _process_file(
        self, file_path: str, file_stat: os.stat_result, strip_prefix: str
    ) -> FsEntry:
        """
        Process a single file at ``file_path`` with stat data ``file_stat``.

        :param file_path: The path to examine.
        :type file_path: ``str``
        :param file_stat: ``os.stat()`` result for ``file_path``.
        :type file_stat: ``os.stat_result``
        :returns: A new ``FsEntry`` representing the file.
        :rtype: ``FsEntry``
        """
        fti = None
        if self.options.include_file_type:
            fti = self.file_type_detector.detect_file_type(file_path)

        content_hash = None
        if stat.S_ISREG(file_stat.st_mode) and getattr(
            self.options, "include_content_hash", True
        ):
            if file_stat.st_size <= self.options.max_content_hash_size:
                content_hash = self._calculate_content_hash(file_path)
        return FsEntry(
            file_path,
            strip_prefix,
            file_stat,
            content_hash=content_hash,
            file_type_info=fti,
        )

    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    def walk_tree(
        self,
        mount: "Mount",
        strip_prefix: str = "",
        quiet: bool = False,
        throbber: Optional[ThrobberBase] = None,
        progress: Optional[ProgressBase] = None,
        term_control: Optional[TermControl] = None,
    ) -> Dict[str, FsEntry]:
        """
        Walk filesystem tree starting from mount.root and return indexed
        entries.

        :param mount: A ``snapm.manager._mounts.Mount`` object representing the
                      file system to walk.
        :type mount: ``snapm.manager._mounts.Mount``
        :param strip_prefix: An optional leading path prefix to strip from
                             stored path values.
        :type strip_prefix: ``str``
        :param quiet: Suppress progress output.
        :type quiet: ``bool``
        :param progress: Optional pre-initialized progress object.
        :type progress: ``Optional[ProgressBase]``
        :param term_control: Optional pre-initialised terminal control object.
        :type term_control: ``Optional[TermControl]``
        :returns: A dictionary mapping path strings to ``FsEntry`` objects.
        :rtype: ``Dict[str, FsEntry]``
        """
        if self.options.from_path is not None:
            from_path = self.options.from_path.lstrip(os.sep)
            start = os.path.join(mount.root, from_path)
        else:
            from_path = "/"
            start = mount.root

        _log_info(
            "Gathering paths to scan from %s (%s)", mount.name, self.options.from_path
        )

        target = f"{mount.name} {self.options.from_path}"
        throbber = throbber or ProgressFactory.get_throbber(
            f"Gathering paths from {target}",
            style="bouncingball",
            quiet=quiet,
            term_control=term_control,
            no_clear=False,
        )

        def _throb(obj: object) -> object:
            """
            Dummy function to throb the throbber.
            """
            throbber.throb()
            return obj

        throbber.start()
        try:
            to_visit = [start] + [
                _throb(os.path.join(root, name))
                for root, dirs, files in os.walk(start)
                for name in itertools.chain(files, dirs)
            ]
        except (KeyboardInterrupt, SystemExit):
            throbber.end("Quit!")
            raise

        throbber.end(message="Done!")
        total = len(to_visit)

        # map of path: FsEntry objects
        tree = {}

        if not progress:
            progress = ProgressFactory.get_progress(
                f"Walking {target}",
                quiet=quiet,
                term_control=term_control,
                no_clear=True,
            )

        start_time = datetime.now()
        progress.start(total)

        follow_symlinks = self.options.follow_symlinks
        excluded = 0
        try:
            for i, pathname in enumerate(to_visit):
                stripped_pathname = pathname.removeprefix(strip_prefix) or "/"
                if any(
                    fnmatch(stripped_pathname, pat) for pat in self.exclude_patterns
                ):
                    excluded += 1
                    continue
                if self.file_patterns and not any(
                    fnmatch(stripped_pathname, pat) for pat in self.file_patterns
                ):
                    excluded += 1
                    continue

                progress.progress(i, f"Scanning {stripped_pathname}")
                if follow_symlinks:
                    try:
                        if os.path.exists(pathname):
                            path_stat = os.stat(pathname)
                        else:
                            _log_debug_fsdiff(
                                "Found dangling symbolic link '%s'", pathname
                            )
                            path_stat = os.lstat(pathname)
                            tree[stripped_pathname] = FsEntry(
                                pathname,
                                strip_prefix,
                                path_stat,
                            )
                            continue
                    except FileNotFoundError:
                        # Path vanished between discovery and stat; skip it.
                        continue
                else:
                    try:
                        path_stat = os.lstat(pathname)
                    except FileNotFoundError:
                        # Not a broken symlink: start does not exist in this root.
                        continue

                exists = os.path.exists(pathname)
                is_lnk = stat.S_ISLNK(path_stat.st_mode)
                if not exists and not is_lnk:
                    continue

                if is_lnk:
                    if not exists:
                        _log_debug_fsdiff("Found dangling symbolic link '%s'", pathname)
                        tree[stripped_pathname] = FsEntry(
                            pathname, strip_prefix, path_stat
                        )
                    else:
                        tree[stripped_pathname] = self._process_file(
                            pathname, path_stat, strip_prefix
                        )
                elif stat.S_ISDIR(path_stat.st_mode):
                    tree[stripped_pathname] = self._process_dir(
                        pathname, path_stat, strip_prefix
                    )
                else:
                    tree[stripped_pathname] = self._process_file(
                        pathname, path_stat, strip_prefix
                    )
        except (KeyboardInterrupt, SystemExit):
            progress.cancel("Quit!")
            raise

        end_time = datetime.now()
        progress.end(
            f"Scanned {total} paths in {end_time - start_time} (excluded {excluded})"
        )
        return tree

    def _calculate_content_hash(self, file_path: str) -> str:
        """
        Calculate content hash for regular files

        :param file_path: The path to the file to hash.
        :type file_path: ``str``
        :returns: A string representation of the hash of the file content using
                  the configured hash algorithm.
        :rtype: ``str``
        """
        hasher = self.hasher(usedforsecurity=False)
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
