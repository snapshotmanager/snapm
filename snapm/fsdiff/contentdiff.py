# Copyright Red Hat
#
# snapm/fsdiff/contentdiff.py - Snapshot Manager fs diff content diffs
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Content-aware diff support.
"""
from typing import Any, Dict, Optional
from abc import ABC, abstractmethod
from pathlib import Path
import logging
import difflib
import json

from snapm import SNAPM_SUBSYSTEM_FSDIFF

from .filetypes import FileTypeCategory, FileTypeInfo
from .treewalk import FsEntry

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


def _log_debug_fsdiff(msg, *args, **kwargs):
    """A wrapper for fsdiff subsystem debug logs."""
    _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_FSDIFF}, **kwargs)


class ContentDiff:
    """
    Represents a content-aware diff between two files.
    """

    def __init__(
        self,
        diff_type: str,
        old_content: Optional[str] = None,
        new_content: Optional[str] = None,
        summary: str = "",
    ):
        """
        Initialise a new ``ContentDiff`` object.

        :param diff_type: The type of diff to perform.
        :type diff_type: ``str``
        :param old_content: The original content.
        :type old_content: ``str``
        :param new_content: The updated content.
        :type new_content: ``str``
        :param summary: A summary of the difference.
        :type summary: ``str``
        """
        self.diff_type = diff_type  # 'unified', 'json', 'binary', 'image', etc.
        self.old_content = old_content
        self.new_content = new_content
        self.diff_data = None  # Type-specific diff representation
        self.summary = summary
        self.has_changes = False
        self.error_message = None

    def __str__(self):
        """
        Return a string representation of this ``ContentDiff`` object.

        :returns: A human readable string representing this instance.
        :rtype: ``str``
        """

        def content_str(content: Optional[str]) -> str:
            """
            Return the first 16 chars of ``content`` followed by ellipsis if
            set, otherwise the empty string.

            :param content: The content to abridge.
            :type content: ``str``
            :returns: The abridged content followed by ellipsis or the empty
                      string if content is unset.
            :rtype: ``str``
            """
            return repr(
                f'"{content[0:16] if content else ""}'
                f'{"..." if content and len(content) > 16 else ""}"'
            )

        if isinstance(self.diff_data, (list, tuple)):
            diff_repr = f"<{len(self.diff_data)} items>"
        elif self.diff_data is None:
            diff_repr = "<none>"
        else:
            diff_repr = "<non-sequence diff data>"

        return (
            f"diff_type: {self.diff_type}, "
            f"old_content: {content_str(self.old_content)}, "
            f"new_content: {content_str(self.new_content)}, "
            f"diff_data: {diff_repr}, "
            f"summary: {self.summary}, "
            f"has_changes: {self.has_changes}, "
            f"error_message: {self.error_message if self.error_message else ''}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert this ``ContentDiff`` object into a dictionary representation
        suitable for encoding as JSON.

        :returns: A dictionary mapping this instance's keys to values.
        :rtype: ``Dict[str, Any]``
        """
        return {
            "diff_type": self.diff_type,
            "old_content": self.old_content,
            "new_content": self.new_content,
            "diff_data": self.diff_data,
            "summary": self.summary,
            "has_changes": self.has_changes,
            "error_message": self.error_message,
        }


class ContentDifferBase(ABC):
    """
    Base class for content-aware diff implementations.
    """

    @abstractmethod
    def can_handle(self, file_type_info: FileTypeInfo) -> bool:
        """
        Return True if this differ can handle the given file type.

        :param file_type_info: File type information for the file to compare.
        :type file_type_info: ``FileTypeInfo``
        :returns: ``True`` if this content differ can handle this file.
        :rtype: ``bool``
        """

    @abstractmethod
    def generate_diff(
        self,
        old_path: Optional[Path],
        new_path: Optional[Path],
        old_entry: Optional[FsEntry],
        new_entry: Optional[FsEntry],
    ) -> ContentDiff:
        """
        Generate content diff between two files.

        :param old_path: ``Path`` describing the original path to diff.
        :type old_path: ``Optional[Path]``
        :param new_path: ``Path`` describing the updated path to diff.
        :type new_path: ``Optional[Path]``
        :param old_entry: The original ``FsEntry`` to diff.
        :type old_entry: ``Optional[FsEntry]``
        :param new_entry: The updated ``FsEntry`` to diff.
        :type new_entry: ``Optional[FsEntry]``
        :returns: A diff of the two entries.
        :rtype: ``ContentDiff``
        """

    @property
    @abstractmethod
    def priority(self) -> int:
        """
        Priority for selection when multiple differs match (higher = preferred)

        :returns: Integer priority level.
        :rtype: ``int``
        """


class TextContentDiffer(ContentDifferBase):
    """
    Default text-based content differ.
    """

    def can_handle(self, file_type_info: FileTypeInfo) -> bool:
        """
        Return True if this differ can handle the given file type.

        :param file_type_info: File type information for the file to compare.
        :type file_type_info: ``FileTypeInfo``
        :returns: ``True`` if this content differ can handle this file.
        :rtype: ``bool``
        """
        return file_type_info.is_text_like

    def generate_diff(
        self,
        old_path: Optional[Path],
        new_path: Optional[Path],
        old_entry: Optional[FsEntry],
        new_entry: Optional[FsEntry],
    ) -> ContentDiff:
        """
        Generate unified diff for text files.

        :param old_path: ``Path`` describing the original path to diff.
        :type old_path: ``Optional[Path]``
        :param new_path: ``Path`` describing the updated path to diff.
        :type new_path: ``Optional[Path]``
        :param old_entry: The original ``FsEntry`` to diff.
        :type old_entry: ``Optional[FsEntry]``
        :param new_entry: The updated ``FsEntry`` to diff.
        :type new_entry: ``Optional[FsEntry]``
        :returns: A diff of the two entries.
        :rtype: ``ContentDiff``
        """
        try:
            file_type_info = (
                new_entry.file_type_info
                if new_entry
                else old_entry.file_type_info if old_entry else None
            )

            if file_type_info and file_type_info.mime_type != "inode/x-empty":
                encoding = file_type_info.encoding or "utf8"
            else:
                encoding = "utf8"

            if old_path and old_path.exists():
                with open(old_path, "r", encoding=encoding, errors="replace") as f:
                    old_lines = f.readlines()
            else:
                old_lines = []

            if new_path and new_path.exists():
                with open(new_path, "r", encoding=encoding, errors="replace") as f:
                    new_lines = f.readlines()
            else:
                new_lines = []

            diff_lines = list(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=str(old_path) if old_path else "/dev/null",
                    tofile=str(new_path) if new_path else "/dev/null",
                    lineterm="\n",
                )
            )

            content_diff = ContentDiff(
                "unified",
                old_content="".join(old_lines),
                new_content="".join(new_lines),
            )
            content_diff.diff_data = diff_lines
            content_diff.has_changes = len(diff_lines) > 0

            def diff_summary(lines, prefix, desc):
                """
                Generate a summary of diff lines ``lines``.

                :param lines: Lines to summarize.
                :param prefix: Diff prefix ("-" or "+")
                :param desc: Description ("additions" or "deletions")
                """
                count = len(
                    [
                        ln
                        for ln in lines
                        if ln.startswith(prefix) and not ln.startswith(3 * prefix)
                    ]
                )
                return f"{count} {desc}"

            content_diff.summary = ", ".join(
                (
                    diff_summary(diff_lines, "-", "deletions"),
                    diff_summary(diff_lines, "+", "additions"),
                )
            )

        except (OSError, TypeError, ValueError) as err:
            _log_debug_fsdiff(
                "TextContentDiffer error reading %s / %s: %s",
                old_path,
                new_path,
                err,
            )
            content_diff = ContentDiff("text")
            content_diff.has_changes = False
            content_diff.error_message = str(err)
            content_diff.summary = "Error reading text content"

        return content_diff

    @property
    def priority(self) -> int:
        """
        Priority for selection when multiple differs match (higher = preferred)

        :returns: Integer priority level.
        :rtype: ``int``
        """
        return 10  # Default priority


class JsonContentDiffer(ContentDifferBase):
    """
    JSON-aware content differ.
    """

    def can_handle(self, file_type_info: FileTypeInfo) -> bool:
        """
        Return True if this differ can handle the given file type.

        :param file_type_info: File type information for the file to compare.
        :type file_type_info: ``FileTypeInfo``
        :returns: ``True`` if this content differ can handle this file.
        :rtype: ``bool``
        """
        return file_type_info.mime_type.startswith("application/json")

    def generate_diff(
        self,
        old_path: Optional[Path],
        new_path: Optional[Path],
        old_entry: Optional[FsEntry],
        new_entry: Optional[FsEntry],
    ) -> ContentDiff:
        """
        Generate structured JSON diff.

        :param old_path: ``Path`` describing the original path to diff.
        :type old_path: ``Optional[Path]``
        :param new_path: ``Path`` describing the updated path to diff.
        :type new_path: ``Optional[Path]``
        :param old_entry: The original ``FsEntry`` to diff.
        :type old_entry: ``Optional[FsEntry]``
        :param new_entry: The updated ``FsEntry`` to diff.
        :type new_entry: ``Optional[FsEntry]``
        :returns: A diff of the two entries.
        :rtype: ``ContentDiff``
        """
        if not old_path or not new_path:
            # Added/removed file - fall back to text diff
            return TextContentDiffer().generate_diff(
                old_path, new_path, old_entry, new_entry
            )

        try:
            file_type_info = (
                new_entry.file_type_info
                if new_entry
                else old_entry.file_type_info if old_entry else None
            )

            if file_type_info and file_type_info.mime_type != "inode/x-empty":
                encoding = file_type_info.encoding or "utf8"
            else:
                encoding = "utf8"

            if old_path and old_path.exists():
                with open(old_path, "r", encoding=encoding, errors="replace") as f:
                    old_data = json.load(f)
            else:
                old_data = {}

            if new_path and new_path.exists():
                with open(new_path, "r", encoding=encoding, errors="replace") as f:
                    new_data = json.load(f)
            else:
                new_data = {}

            # Use a JSON diff library like jsondiff if available
            # For now, fall back to text diff of pretty-printed JSON
            old_pretty = json.dumps(old_data, indent=2, sort_keys=True)
            new_pretty = json.dumps(new_data, indent=2, sort_keys=True)

            diff_lines = list(
                difflib.unified_diff(
                    old_pretty.splitlines(keepends=True),
                    new_pretty.splitlines(keepends=True),
                    fromfile=str(old_path) if old_path else "/dev/null",
                    tofile=str(new_path) if new_path else "/dev/null",
                    lineterm="",
                )
            )

            content_diff = ContentDiff(
                "json",
                old_content=old_pretty,
                new_content=new_pretty,
            )
            content_diff.diff_data = diff_lines
            content_diff.has_changes = len(diff_lines) > 0
            content_diff.summary = (
                "JSON structure changes detected"
                if content_diff.has_changes
                else "No changes"
            )

        except (OSError, TypeError, ValueError) as err:
            _log_debug_fsdiff(
                "JsonContentDiffer error reading %s / %s as JSON (%s), "
                "falling back to TextContentDiffer",
                old_path,
                new_path,
                err,
            )
            # Fall back to text diff
            return TextContentDiffer().generate_diff(
                old_path, new_path, old_entry, new_entry
            )

        return content_diff

    @property
    def priority(self) -> int:
        """
        Priority for selection when multiple differs match (higher = preferred)

        :returns: Integer priority level.
        :rtype: ``int``
        """
        return 50  # Higher than text differ


class BinaryContentDiffer(ContentDifferBase):
    """
    Binary file content differ.
    """

    binary_types = (
        FileTypeCategory.BINARY,
        FileTypeCategory.IMAGE,
        FileTypeCategory.AUDIO,
        FileTypeCategory.VIDEO,
        FileTypeCategory.ARCHIVE,
        FileTypeCategory.EXECUTABLE,
        FileTypeCategory.DATABASE,
        FileTypeCategory.DOCUMENT,
    )

    def can_handle(self, file_type_info: FileTypeInfo) -> bool:
        """
        Return True if this differ can handle the given file type.

        :param file_type_info: File type information for the file to compare.
        :type file_type_info: ``FileTypeInfo``
        :returns: ``True`` if this content differ can handle this file.
        :rtype: ``bool``
        """
        return file_type_info.category in self.binary_types

    def generate_diff(
        self,
        _old_path: Optional[Path],
        _new_path: Optional[Path],
        old_entry: Optional[FsEntry],
        new_entry: Optional[FsEntry],
    ) -> ContentDiff:
        """
        Generate binary diff summary.

        :param _old_path: ``Path`` describing the original path to diff
                         (unused).
        :type _old_path: ``Optional[Path]``
        :param _new_path: ``Path`` describing the updated path to diff
                         (unused).
        :type _new_path: ``Optional[Path]``
        :param old_entry: The original ``FsEntry`` to diff.
        :type old_entry: ``Optional[FsEntry]``
        :param new_entry: The updated ``FsEntry`` to diff.
        :type new_entry: ``Optional[FsEntry]``
        :returns: A diff of the two entries.
        :rtype: ``ContentDiff``
        """
        content_diff = ContentDiff("binary")

        # For binary files, just report size differences and hash changes
        if new_entry and old_entry:
            size_diff = new_entry.size - old_entry.size
        elif new_entry:
            size_diff = new_entry.size
        elif old_entry:
            size_diff = -old_entry.size
        else:
            size_diff = 0

        hash_changed = (
            old_entry.content_hash != new_entry.content_hash
            if (old_entry and new_entry)
            else True
        )

        # Consider any size or hash change as a content change.
        has_changes = size_diff != 0 or hash_changed
        content_diff.has_changes = has_changes

        if size_diff != 0:
            content_diff.summary = f"Binary file size changed by {size_diff:+d} bytes"
        elif hash_changed:
            content_diff.summary = "Binary file content changed (same size)"
        else:
            content_diff.summary = "Binary file unchanged"

        return content_diff

    @property
    def priority(self) -> int:
        """
        Priority for selection when multiple differs match (higher = preferred)

        :returns: Integer priority level.
        :rtype: ``int``
        """
        return 5  # Lower than text differ


class ContentDifferManager:
    """
    Manager for content-aware diff implementations.
    """

    def __init__(self):
        """
        Initialise a new ``ContentDifferManager`` instance.
        """
        self.differs = []
        self._register_default_differs()

    def _register_default_differs(self):
        """
        Register built-in content differs.
        """
        self.register_differ(JsonContentDiffer())
        self.register_differ(TextContentDiffer())
        self.register_differ(BinaryContentDiffer())

    def register_differ(self, differ: ContentDifferBase):
        """
        Register a new content differ.
        """
        self.differs.append(differ)
        # Sort by priority (highest first)
        self.differs.sort(key=lambda d: d.priority, reverse=True)

    def get_differ_for_file(self, file_type_info: FileTypeInfo) -> ContentDifferBase:
        """
        Get the best content differ for a file type.

        :param file_type_info: The file type to find a differ for.
        :type file_type_info: ``FileTypeInfo``
        :returns: An appropriate differ for ``file_type_info``.
        :rtype: A ``ContentDifferBase`` subclass.
        """
        for differ in self.differs:
            if differ.can_handle(file_type_info):
                return differ

        # Fallback - this shouldn't happen with proper default differs
        return BinaryContentDiffer()

    def generate_content_diff(
        self,
        old_path: Optional[Path],
        new_path: Optional[Path],
        old_entry: Optional[FsEntry],
        new_entry: Optional[FsEntry],
    ) -> Optional[ContentDiff]:
        """
        Generate content diff using appropriate differ.

        :param old_path: ``Path`` describing the original path to diff.
        :type old_path: ``Optional[Path]``
        :param new_path: ``Path`` describing the updated path to diff.
        :type new_path: ``Optional[Path]``
        :param old_entry: The original ``FsEntry`` to diff.
        :type old_entry: ``Optional[FsEntry]``
        :param new_entry: The updated ``FsEntry`` to diff.
        :type new_entry: ``Optional[FsEntry]``
        :returns: A diff of the two entries.
        :rtype: ``Optional[ContentDiff]``
        """

        # Only generate content diffs for files
        if not ((old_entry and old_entry.is_file) or (new_entry and new_entry.is_file)):
            return None

        file_type_info = (
            new_entry.file_type_info
            if (new_entry and new_entry.file_type_info)
            else (
                old_entry.file_type_info
                if (old_entry and old_entry.file_type_info)
                else None
            )
        )

        try:
            differ = (
                self.get_differ_for_file(file_type_info)
                if file_type_info is not None
                else TextContentDiffer()
            )
            return differ.generate_diff(old_path, new_path, old_entry, new_entry)
        except (LookupError, OSError, UnicodeError, TypeError) as err:
            _log_error(
                "Error generating content diff for %s - %s (mime_type=%s): %s",
                new_entry.full_path if new_entry else "/dev/null",
                old_entry.full_path if old_entry else "/dev/null",
                file_type_info.mime_type if file_type_info else "unknown",
                err,
            )
            return None
