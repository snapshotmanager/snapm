# Copyright Red Hat
#
# snapm/fsdiff/filetypes.py - Snapshot Manager fs diff file types
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
File type information support.
"""
from typing import Optional, ClassVar, Dict
from pathlib import Path
from enum import Enum
import logging
import magic

from snapm import SNAPM_SUBSYSTEM_FSDIFF

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


def _log_debug_fsdiff(msg, *args, **kwargs):
    """A wrapper for fsdiff subsystem debug logs."""
    _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_FSDIFF}, **kwargs)


class FileTypeCategory(Enum):
    """
    Enum for file type categories.
    """

    TEXT = "text"
    BINARY = "binary"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    ARCHIVE = "archive"
    EXECUTABLE = "executable"
    CONFIG = "config"
    LOG = "log"
    DATABASE = "database"
    DOCUMENT = "document"
    DIRECTORY = "directory"
    BLOCK = "block"
    CHAR = "char"
    SOCK = "socket"
    FIFO = "FIFO"
    UNKNOWN = "unknown"


class FileTypeInfo:
    """
    Class representing file type information and encoding.
    """

    def __init__(
        self,
        mime_type: str,
        description: str,
        category: FileTypeCategory,
        encoding: Optional[str] = None,
    ):
        """
        Initialise a new ``FileTypeInfo`` object.

        :param mime_type: The detected MIME type.
        :type mime_type: ``str``
        :param description: Type description returned by magic.
        :type description: ``str``
        :param category: File type category.
        :type category: ``FileTypeCategory``
        :param encoding: Optional file encoding.
        :type encoding: ``Optional[str]``
        """
        self.mime_type = mime_type
        self.description = description
        self.category = category
        self.encoding = encoding
        self.is_text_like = category in (
            FileTypeCategory.TEXT,
            FileTypeCategory.CONFIG,
            FileTypeCategory.LOG,
        )

    def __str__(self):
        """
        Return a string representation of this ``FileTypeInfo`` object.

        :returns: A human readable string describing this instance.
        :rtype: ``str``
        """
        return (
            f"MIME type: {self.mime_type}, "
            f"Category: {self.category.value}, "
            f"Encoding: {self.encoding if self.encoding else 'unknown'}, "
            f"Description: {self.description}"
        )


class FileTypeDetector:
    """
    Detect file types using ``magic`` from python3-file-magic.
    """

    # Custom rules for better categorization
    category_rules: ClassVar[Dict[str, FileTypeCategory]] = {
        "text/": FileTypeCategory.TEXT,
        "application/json": FileTypeCategory.CONFIG,
        "application/xml": FileTypeCategory.CONFIG,
        "application/yaml": FileTypeCategory.CONFIG,
        "image/": FileTypeCategory.IMAGE,
        "audio/": FileTypeCategory.AUDIO,
        "video/": FileTypeCategory.VIDEO,
        "application/zip": FileTypeCategory.ARCHIVE,
        "application/gzip": FileTypeCategory.ARCHIVE,
        "application/x-executable": FileTypeCategory.EXECUTABLE,
        "application/x-sharedlib": FileTypeCategory.EXECUTABLE,
        "inode/directory": FileTypeCategory.DIRECTORY,
        "inode/blockdevice": FileTypeCategory.BLOCK,
        "inode/chardevice": FileTypeCategory.CHAR,
        "inode/fifo": FileTypeCategory.FIFO,
        "inode/socket": FileTypeCategory.SOCK,
    }

    def detect_file_type(self, file_path: Path) -> FileTypeInfo:
        """
        Detect comprehensive file type information.

        :param file_path: The path to the file to inspect.
        :type file_path: ``Path``.
        :returns: File type information for ``file_path``.
        :rtype: ``FileTypeInfo``
        """
        # c9s magic does not have magic.error
        if hasattr(magic, "error"):
            magic_errors = (magic.error, OSError, ValueError)
        else:
            magic_errors = (OSError, ValueError)

        try:
            fm = magic.detect_from_filename(str(file_path))
            mime_type = fm.mime_type
            encoding = fm.encoding
            description = fm.name

            category = self._categorize_file(mime_type, file_path)

            return FileTypeInfo(mime_type, description, category, encoding)

        except magic_errors as err:
            _log_warn("Error detecting file type for %s: %s", str(file_path), err)
            return FileTypeInfo(
                "application/octet-stream", "unknown", FileTypeCategory.UNKNOWN
            )

    def _categorize_file(self, mime_type: str, file_path: Path) -> FileTypeCategory:
        """
        Categorize file based on MIME type and path patterns.

        :param mime_type: Detected file MIME type.
        :type mime_type: ``str``
        :param file_path: Path to the file to categorize.
        :type file_path: ``Path``
        :returns: File type categorization.
        :rtype: ``FileTypeCategory``
        """
        mime_type = mime_type.lower()
        # Check path-based rules for common locations
        path_str = str(file_path).lower()
        if "/log/" in path_str or path_str.endswith(".log"):
            return FileTypeCategory.LOG
        if path_str.startswith("/etc/") or path_str.endswith(".conf"):
            return FileTypeCategory.CONFIG
        if "database" in path_str or path_str.endswith((".db", ".sqlite")):
            return FileTypeCategory.DATABASE

        # Check MIME type rules
        for pattern, category in self.category_rules.items():
            if mime_type.startswith(pattern):
                return category

        return FileTypeCategory.BINARY
