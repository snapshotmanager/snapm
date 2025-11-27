# Copyright Red Hat
#
# snapm/_fsdiff/treewalk.py - Snapshot Manager fs differ tree walk
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Tree walking support for fsdiff.
"""
from typing import Dict, Optional, TYPE_CHECKING
from pathlib import Path
import stat
import os

from .filetypes import FileTypeDetector, FileTypeInfo

if TYPE_CHECKING:
    from snapm.manager._mounts import Mount


class FsEntry:
    """
    Representation of a single file system entry for comparison.
    """

    def __init__(
        self,
        path: Path,
        stat_info: os.stat_result,
        content_hash: Optional[str] = None,
        file_type_info: Optional[FileTypeInfo] = None,
    ):
        """
        Initialise a new ``FsEntry`` object.

        :param path: The path this ``FsEntry`` represents.
        :type path: ``Path``
        :param stat_info: An ``os.stat_result`` for this ``FsEntry``.
        :type stat_info: ``os.stat_result``
        :param content_hash: A hash of the content of this ``FsEntry``.
        :type content_hash: ``str``
        :param file_type_info: A ``FileTypeInfo`` object for this ``FsEntry``.
        :type file_type_info: ``FileTypeInfo``
        """
        self.path = path
        self.stat = stat_info
        self.mode = stat_info.st_mode
        self.size = stat_info.st_size
        self.mtime = stat_info.st_mtime
        self.uid = stat_info.st_uid
        self.gid = stat_info.st_gid
        self.content_hash = content_hash
        self.symlink_target = None
        self.xattrs = {}
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

    def __init__(self, hash_algorithm="sha256"):
        """
        Initialise a new ``TreeWalker`` object.

        :param hash_algorithm: A string describing the hash algorithm to be
                               used for this ``TreeWalker`` instance.
        :type hash_algorithm: ``str``
        """
        self.hash_algorithm = hash_algorithm
        self.file_type_detector = FileTypeDetector()
        self.exclude_patterns = [
            "/proc/*",
            "/sys/*",
            "/dev/*",
            "/tmp/*",
            "/run/*",
            "/var/run/*",
            "/var/lock/*",
        ]

    def walk_tree(
        self,
        mount: "Mount",
        include_content_hash=True,
        include_file_types=True,
        follow_symlinks=False,
    ) -> Dict[str, FsEntry]:
        """
        Walk filesystem tree starting from mount.root and return indexed
        entries.

        :param mount: A ``snapm.manager._mounts.Mount`` object representing the
                      file system to walk.
        :type mount: ``snapm.manager._mounts.Mount``
        :param include_content_hash: ``True`` to hash file contents or
                                     ``False`` otherwise.
        :type include_content_hash: ``bool``
        :param include_file_types: ``True`` to generate file type information
                                   or ``False`` otherwise.
        :type include_file_types: ``bool``
        :param follow_symlinks: ``True`` to enable following symlinks or
                                ``False`` otherwise.
        :type follow_symlinks: ``bool``
        :returns: A dictionary mapping path strings to ``FsEntry`` objects.
        :rtype: ``Dict[str, FsEntry]``
        """
        raise NotImplementedError(
            "TreeWalker.walk_tree is not yet implemented"
        )  # pragma: no cover

    def _calculate_content_hash(self, file_path: Path) -> str:
        """
        Calculate content hash for regular files

        :param file_path: The path to the file to hash.
        :type file_path: ``Path``
        :returns: A string representation of the hash of the file content using
                  the configured hash algorithm.
        :rtype: ``str``
        """
        raise NotImplementedError(
            "TreeWalker._calculate_content_hash is not yet implemented"
        )  # pragma: no cover

    def _get_extended_attributes(self, file_path: Path) -> Dict[str, bytes]:
        """
        Extract extended attributes (xattrs) for ``Path``.

        :param file_path: The path to the file to retrieve xattrs for.
        :type file_path: ``Path``
        :returns: A dictionary mapping xattr names to byte array values.
        :rtype: ``Dict[str, bytes]``
        """
        raise NotImplementedError(
            "TreeWalker._get_extended_attributes is not yet implemented"
        )  # pragma: no cover
