# Copyright Red Hat
#
# snapm/fsdiff/engine.py - Snapshot Manager fs diff engine
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
File system diff engine
"""
from typing import Any, ClassVar, Dict, Iterator, List, Optional, Tuple
from collections import defaultdict
from datetime import datetime
from math import floor
import logging
import json
import os
import re

from snapm import SNAPM_SUBSYSTEM_FSDIFF
from snapm.progress import ProgressFactory, TermControl

from .changes import ChangeDetector, ChangeType, FileChange
from .contentdiff import ContentDiff, ContentDifferManager
from .difftypes import DiffType
from .options import DiffOptions
from .treewalk import FsEntry
from .tree import DiffTree

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error

ENGINE_LOG_ME_HARDER = False

_SIBLING_REGEX = re.compile("[-_.,:@ ]+")


def _log_debug_fsdiff(msg, *args, **kwargs):
    """A wrapper for fsdiff subsystem debug logs."""
    _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_FSDIFF}, **kwargs)


def _log_debug_fsdiff_extra(msg, *args, **kwargs):
    """A wrapper for fsdiff subsystem debug logs."""
    if ENGINE_LOG_ME_HARDER:  # pragma: no cover
        _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_FSDIFF}, **kwargs)


class FsDiffRecord:
    """
    Diff record compatible with snapm.report system
    Represents a single file/directory change
    """

    def __init__(
        self,
        path: str,
        diff_type: DiffType,
        old_entry: Optional[FsEntry] = None,
        new_entry: Optional[FsEntry] = None,
    ):
        """
        Initialise a new ``FsDiffRecord`` object.

        :param path: A string describing the path for this diff record.
        :type path: ``str``
        :param diff_type: The diff type for this diff record.
        :type diff_type: ``DiffType``
        :param old_entry: The original entry for the comparison.
        :param new_entry: The updated entry for the comparison.
        """
        self.path = path
        self.diff_type = diff_type
        self.old_entry = old_entry
        self.new_entry = new_entry
        self.changes = []  # List of FileChange objects
        self.content_diff = None  # ContentDiff object
        self.moved_from = None  # For move operations
        self.moved_to = None

        # Fields for reporting (compatible with snapm.report)
        self.file_path = path
        self.change_type = diff_type
        self.file_type = self._get_file_type()
        self.file_type_desc = self._get_file_type_desc()
        self.file_category = self._get_file_category()
        self.size_old = old_entry.size if old_entry else 0
        self.size_new = new_entry.size if new_entry else 0
        self.size_delta = self.size_new - self.size_old
        self.mode_old = oct(old_entry.mode) if old_entry else None
        self.mode_new = oct(new_entry.mode) if new_entry else None
        self.owner_old = f"{old_entry.uid}:{old_entry.gid}" if old_entry else None
        self.owner_new = f"{new_entry.uid}:{new_entry.gid}" if new_entry else None
        self.mtime_old = old_entry.mtime if old_entry else None
        self.mtime_new = new_entry.mtime if new_entry else None
        self.content_changed = False
        self.metadata_changed = False
        self.has_content_diff = False
        self.content_diff_summary = ""

    def __str__(self) -> str:
        """
        Return a string representation of this ``FsDiffRecord`` object.

        :returns: A human readable representation of this ``FsDiffRecord``.
        :rtype: ``str``
        """

        def _format_mtime(mtime: Optional[float]) -> str:
            """
            Format a (possibly ``None``) mtime value.

            :param mtime: The mtime to format.
            :type mtime: ``Optional[float]``
            :returns: The formatted value or the empty string.
            :rtype: ``str``
            """
            return f"{'(' + str(mtime) + ')' if mtime is not None else ''}"

        # Possibly empty/missing fields
        nl = "\n"
        changes = (
            ("  changes:\n" + f"{nl.join(str(chg) for chg in self.changes)}\n")
            if self.changes
            else ""
        )

        moved_from = f"moved_from: {self.moved_from}\n" if self.moved_from else ""
        moved_to = f"moved_to: {self.moved_to}\n" if self.moved_to else ""

        content_diff = (
            ("  content_diff:\n" + f"{self.content_diff}\n")
            if self.content_diff is not None
            else ""
        )

        content_diff_summary = (
            f"\n  content_diff_summary: {self.content_diff_summary}"
            if self.content_diff_summary
            else ""
        )

        # Pre-formatted fields
        mtime_old = (
            str(datetime.fromtimestamp(self.mtime_old)) + " "
            if self.mtime_old is not None
            else ""
        )

        mtime_new = (
            str(datetime.fromtimestamp(self.mtime_new)) + " "
            if self.mtime_new is not None
            else ""
        )

        fsd_str = (
            f"Path: {self.path}\n"
            f"  diff_type: {self.diff_type.value}\n"
            f"  old_entry:{nl + str(self.old_entry) if self.old_entry else ''}\n"
            f"  new_entry:{nl + str(self.new_entry) if self.new_entry else ''}\n"
            f"{changes}"  # no newline (embedded if set)
            f"{content_diff}"  # no newline (embedded if set)
            f"{'  ' + moved_from if moved_from else ''}"  # no newline (embedded if set)
            f"{'  ' + moved_to if moved_to else ''}"  # no newline (embedded if set)
            f"  file_path: {self.file_path}\n"
            f"  file_type: {self.file_type}\n"
            f"  file_type_desc: {self.file_type_desc}\n"
            f"  file_category: {self.file_category}\n"
            f"  size_old: {self.size_old}\n"
            f"  size_new: {self.size_new}\n"
            f"  size_delta: {self.size_delta}\n"
            f"  mode_old: {self.mode_old if self.mode_old else ''}\n"
            f"  mode_new: {self.mode_new if self.mode_new else ''}\n"
            f"  owner_old: {self.owner_old if self.owner_old else ''}\n"
            f"  owner_new: {self.owner_new if self.owner_new else ''}\n"
            f"  mtime_old: {mtime_old}{_format_mtime(self.mtime_old)}\n"
            f"  mtime_new: {mtime_new}{_format_mtime(self.mtime_new)}\n"
            f"  content_changed: {self.content_changed}\n"
            f"  metadata_changed: {self.metadata_changed}\n"
            f"  has_content_diff: {self.has_content_diff}"  # no newline: end
            f"{content_diff_summary}"  # no newline (prefixed if set)
        )
        return fsd_str

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert this ``FsDiffRecord`` object into a dictionary representation
        suitable for encoding as JSON.

        :returns: A dictionary mapping this instance's keys to values.
        :rtype: ``Dict[str, Any]``
        """
        out = {
            "path": self.path,
            "diff_type": self.diff_type.value,
            "file_path": self.file_path,
            "file_type": self.file_type,
            "file_type_desc": self.file_type_desc,
            "file_category": self.file_category,
            "size_old": self.size_old,
            "size_new": self.size_new,
            "size_delta": self.size_delta,
            "mode_old": self.mode_old or "",
            "mode_new": self.mode_new or "",
            "owner_old": self.owner_old or "",
            "owner_new": self.owner_new or "",
            "mtime_old": self.mtime_old,
            "mtime_new": self.mtime_new,
            "content_changed": self.content_changed,
            "metadata_changed": self.metadata_changed,
            "has_content_diff": self.has_content_diff,
        }
        if self.old_entry:
            out["old_entry"] = self.old_entry.to_dict()
        if self.new_entry:
            out["new_entry"] = self.new_entry.to_dict()
        if self.changes:
            out["changes"] = [change.to_dict() for change in self.changes]
        if self.content_diff is not None:
            out["content_diff"] = self.content_diff.to_dict()
        if self.moved_from:
            out["moved_from"] = self.moved_from
        if self.moved_to:
            out["moved_to"] = self.moved_to
        if self.content_diff_summary:
            out["content_diff_summary"] = self.content_diff_summary

        return out

    def json(self, pretty=False) -> str:
        """
        Return a string representation of this ``FsDiffRecord`` in JSON
        notation.

        :param pretty: Indent JSON to be human readable.
        :type pretty: ``bool``
        :returns: A JSON representation of this instance.
        :rtype: ``str``
        """
        return json.dumps(self.to_dict(), indent=4 if pretty else None)

    def _get_file_type(self) -> str:
        """
        Get file type for reporting.

        :returns: A description of the file type for this diff record.
        :rtype: ``str``
        """
        entry = self.new_entry or self.old_entry
        if not entry:
            return "unknown"
        if entry.is_dir:
            return "directory"
        if entry.is_symlink:
            return "symlink"
        if entry.file_type_info:
            return entry.file_type_info.mime_type
        return "file"

    def _get_file_type_desc(self) -> str:
        """
        Get file type description for reporting.

        :returns: A detailed description of the file type for this diff record.
        :rtype: ``str``
        """
        entry = self.new_entry or self.old_entry
        if not entry:
            return "unknown"
        if entry.is_dir:
            return "filesystem directory"
        if entry.is_symlink:
            return "symbolic link"
        if entry.file_type_info:
            return entry.file_type_info.description
        return "unknown"

    def _get_file_category(self) -> str:
        """
        Get file category for reporting

        :returns: The file category for this diff record.
        :rtype: ``str``
        """
        entry = self.new_entry or self.old_entry
        if not entry or not entry.file_type_info:
            return "unknown"
        return entry.file_type_info.category.value

    def add_change(self, change: "FileChange"):
        """
        Add a detected change.

        :param change: The file change to record.
        :type change: ``FileChange``
        """
        self.changes.append(change)
        if change.change_type == ChangeType.CONTENT:
            self.content_changed = True
        else:
            self.metadata_changed = True

    def set_content_diff(self, content_diff: ContentDiff):
        """
        Set content-level diff.

        :param content_diff: The content diff for this diff record.
        :type content_diff: ``ContentDiff``
        """
        self.content_diff = content_diff
        self.has_content_diff = True
        self.content_diff_summary = content_diff.summary

    def get_change_summary(self) -> str:
        """
        Get human-readable change summary
        """
        summary = "Modified"
        if self.diff_type == DiffType.ADDED:
            summary = f"Added {self.file_type}"
        elif self.diff_type == DiffType.REMOVED:
            summary = f"Removed {self.file_type}"
        elif self.diff_type == DiffType.MOVED:
            summary = f"Moved from {self.moved_from} to {self.moved_to}"
        elif self.diff_type == DiffType.TYPE_CHANGED:
            if not self.old_entry or not self.new_entry:
                summary = "Type changed"
            else:
                summary = (
                    f"Type changed from {self.old_entry.type_desc} to "
                    f"{self.new_entry.type_desc}"
                )
        elif self.changes:
            change_types = [c.change_type.value for c in self.changes]
            summary = f"Changed: {', '.join(set(change_types))}"
        return summary


def render_unified_diff(record: FsDiffRecord, tc: Optional[TermControl]) -> str:
    """
    Render a unified diff for a modified file.

    :param record: The diff record to render.
    :type record: ``FsDiffRecord``
    :param tc: An optional ``TermControl`` instance to use for rendering color
               output.
    :type tc: ``Optional[TermControl]``
    :returns: Rendered unified diff string.
    :rtype: ``str``
    """

    def _format_timestamp(timestamp: Optional[float]) -> str:
        """
        Format human-readable timestamp.

        :param timestamp: A UNIX epoch timestamp.
        :type timestamp: ``Optional[float]``
        :returns: Human readable datetime string.
        :rtype: ``str``
        """
        return str(datetime.fromtimestamp(timestamp)) if timestamp is not None else ""

    def _hunk_header(header: str) -> str:
        """
        Format a chunk header with colored output.

        :param header: The header to format.
        :type header: ``str``
        :returns: Colorized header string.
        :rtype: ``str``
        """
        before, sep, after = header.partition(" @@")
        if not sep:  # No closing @@
            return header
        return tc.CYAN + before + " @@" + tc.NORMAL + after.strip()

    if not record.has_content_diff:
        return ""

    if record.content_diff.diff_type not in ("unified", "json"):
        return ""

    added = record.new_entry and not record.old_entry
    deleted = record.old_entry and not record.new_entry

    from_path = f"a{record.file_path}"
    to_path = f"b{record.file_path}"

    header_lines = [f"diff {from_path} {to_path}"]

    if added:
        header_lines.append(f"new file mode {record.mode_new}")
    if deleted:
        header_lines.append(f"deleted file mode {record.mode_old}")

    from_path = from_path if not added else "/dev/null"
    to_path = to_path if not deleted else "/dev/null"

    lines = [
        *header_lines,
        f"--- {from_path}\t{_format_timestamp(record.mtime_old)}",
        f"+++ {to_path}\t{_format_timestamp(record.mtime_new)}",
    ]

    def _rstrip_nl(s: str) -> str:
        return s[:-1] if s.endswith("\n") else s

    if not record.content_diff.diff_data or len(record.content_diff.diff_data) < 2:
        return ""

    if tc:
        diff_lines = [
            (
                (tc.RED + _rstrip_nl(line) + tc.WHITE)
                if line.startswith("-")
                else (
                    (tc.GREEN + _rstrip_nl(line) + tc.WHITE)
                    if line.startswith("+")
                    else (
                        _hunk_header(line)
                        if line.startswith("@@")
                        else _rstrip_nl(line)
                    )
                )
            )
            for line in record.content_diff.diff_data[2:]
        ]
    else:
        diff_lines = [_rstrip_nl(line) for line in record.content_diff.diff_data[2:]]

    lines.extend(diff_lines)

    # Preserve content exactly; we already stripped only trailing newlines
    # from content-diff input via _rstrip_nl().
    return "\n".join(lines)


def render_diff_stat(records: List[FsDiffRecord], term_control: TermControl) -> str:
    """
    Render a diffstat-style summary for the given records.

    :param records: Diff records with content diffs.
    :type records: ``FsDiffRecord``
    :param term_control: A ``TermControl`` instance to use for rendering color
               output.
    :type term_control: ``TermControl``
    :returns: Diffstat-style summary string.
    :rtype: ``str``
    """
    # Filter to only records with line-based diff data
    records = [r for r in records if r.content_diff and r.content_diff.diff_data]

    if not records:
        return ""

    adds = 0
    dels = 0

    def _render_one(record: FsDiffRecord):
        """
        :param record: The diff record to render.
        :type record: ``FsDiffRecord``
        :returns: A diffstat for ``record``.
        :rtype: ``str``
        """
        nonlocal adds, dels

        this_path = record.path.lstrip(os.sep)
        pad = path_width - len(this_path)
        header = f" {this_path}{pad * ' '} | "
        added = len(
            [
                data
                for data in record.content_diff.diff_data
                if (data.startswith("+") and not data.startswith("+++"))
            ]
        )
        removed = len(
            [
                data
                for data in record.content_diff.diff_data
                if (data.startswith("-") and not data.startswith("---"))
            ]
        )
        adds += added
        dels += removed
        plus = f"{term_control.GREEN}+{term_control.NORMAL}"
        minus = f"{term_control.RED}-{term_control.NORMAL}"
        return header + f"{added + removed:4} {plus * added}{minus * removed}"

    path_width = max(len(record.path) for record in records) - 1
    count = len(records)
    diffstat = "\n".join(_render_one(record) for record in records)
    trailer = (
        f"\n {count} file{'s' if count > 1 else ''} changed, "
        f"{adds} insertions(+), {dels} deletions(-)"
    )
    return diffstat + trailer


class FsDiffResults:
    """Container for filesystem diff results with formatting methods."""

    #: Constant for the names of the string diff formats
    DIFF_FORMATS: ClassVar[List[str]] = [
        "paths",
        "full",
        "short",
        "json",
        "diff",
        "summary",
        "tree",
    ]

    def __init__(
        self,
        records: List[FsDiffRecord],
        options: DiffOptions,
        timestamp: int,
        count: int = 0,
    ):
        self._records = records
        self.options = options
        self.timestamp = timestamp
        self.count = count or len(records)

    def __repr__(self) -> str:
        """
        Return a machine-readable representation of this instance.

        :returns: ``FsDiffResults`` constructor style string.
        :rtype: ``str``
        """
        return f"FsDiffResults([...], {self.options!r}, {self.timestamp})"

    # List-like interface
    def __iter__(self) -> Iterator[FsDiffRecord]:
        """
        Implement iter(self).
        """
        return iter(self._records)

    def __len__(self):
        """
        Implement len(self).
        """
        return len(self._records)

    def __getitem__(self, index: int) -> FsDiffRecord:
        """
        Return self[index]

        :param index: The index to return.
        :type index: ``int``
        """
        return self._records[index]

    # Summary properties
    @property
    def total_changes(self) -> int:
        """
        Return the total number of changes in this ``FsDiffResults`` instance:
        equivalent to ``len(self)``.

        :returns: Count of changes.
        :rtype: ``int``
        """
        return len(self)

    @property
    def content_changes(self) -> int:
        """
        Return the number of content changes in this ``FsDiffResults`` instance.

        :returns: Count of changes with content diff.
        :rtype: ``int``
        """
        return len([r for r in self._records if r.has_content_diff])

    @property
    def added(self) -> List[FsDiffRecord]:
        """
        Return added changes in this ``FsDiffResults`` instance.

        :returns: Changes with ``DiffType.ADDED`` type.
        :rtype: ``List[FsDiffRecord]``
        """
        return [r for r in self._records if r.diff_type == DiffType.ADDED]

    @property
    def removed(self) -> List[FsDiffRecord]:
        """
        Return removed changes in this ``FsDiffResults`` instance.

        :returns: Changes with ``DiffType.REMOVED`` type.
        :rtype: ``List[FsDiffRecord]``
        """
        return [r for r in self._records if r.diff_type == DiffType.REMOVED]

    @property
    def modified(self) -> List[FsDiffRecord]:
        """
        Return modified changes in this ``FsDiffResults`` instance.

        :returns: Changes with ``DiffType.MODIFIED`` type.
        :rtype: ``List[FsDiffRecord]``
        """
        return [r for r in self._records if r.diff_type == DiffType.MODIFIED]

    @property
    def moved(self) -> List[FsDiffRecord]:
        """
        Return moved changes in this ``FsDiffResults`` instance.

        :returns: Changes with ``DiffType.MOVED`` type.
        :rtype: ``List[FsDiffRecord]``
        """
        return [r for r in self._records if r.diff_type == DiffType.MOVED]

    @property
    def type_changed(self) -> List[FsDiffRecord]:
        """
        Return type_changed changes in this ``FsDiffResults`` instance.

        :returns: Changes with ``DiffType.TYPE_CHANGED`` type.
        :rtype: ``List[FsDiffRecord]``
        """
        return [r for r in self._records if r.diff_type == DiffType.TYPE_CHANGED]

    # Output formats
    def paths(self) -> List[str]:
        """
        Return a list of paths that changed in this ``FsDiffResults``.

        :returns: Path list.
        :rtype: ``List[str]``
        """
        return [record.path for record in self._records]

    def full(self) -> str:
        """
        Return a string with full ``FsDiffRecord`` content for this instance.

        :returns: String description of file system changes.
        :rtype: ``str``
        """
        return "\n\n".join(str(record) for record in self._records)

    def short(self) -> str:
        """
        Return brief summary of ``FsDiffRecord`` content for this instance.

        :returns: Brief string description of file system changes.
        :rtype: ``str``
        """
        first = True
        out = ""
        for record in self._records:
            summary = (
                f"\n  content_diff_summary: {record.content_diff_summary}"
                if record.content_diff_summary
                else ""
            )

            change_descs = ", ".join(chg.description for chg in record.changes)
            description = f"\n  changes: {change_descs}" if change_descs else ""

            sep = "" if first else "\n"
            out += (
                f"{sep}"
                f"Path: {record.path}\n"
                f"  diff_type: {record.diff_type.value}\n"
                f"  file_type: {record.file_type}\n"
                f"  file_type_desc: {record.file_type_desc}"
                f"{description}{summary}"
            )
            first = False
        return out

    def json(self, pretty: bool = False) -> str:
        """
        Return JSON representation of ``FsDiffRecord`` content for this
        instance.

        :returns: JSON string description of file system changes.
        :rtype: ``str``
        """
        dicts = [record.to_dict() for record in self._records]
        return json.dumps(dicts, indent=4 if pretty else None)

    def diff(
        self,
        diffstat: bool = False,
        color: str = "auto",
        term_control: Optional[TermControl] = None,
    ) -> str:
        """
        Return unified diff representation of content changes for this
        instance.

        :param diffstat: Include "diffstat"-like change summary.
        :type diffstat: ``bool``
        :param color: A string to control color diff rendering: "auto",
                      "always", or "never".
        :type color: ``str``
        :param term_control: An optional ``TermControl`` instance to use for
                             formatting. The supplied instance overrides any
                             ``color`` argument if set.
        :type term_control: ``Optional[TermControl]``
        :returns: unified diff string description of file system changes.
        :rtype: ``str``
        """
        term_control = term_control or TermControl(color=color)
        content_diffs = [r for r in self._records if r.has_content_diff]
        diffs = [
            rendered
            for r in content_diffs
            if (rendered := render_unified_diff(r, term_control))
        ]
        stat_str = ""
        if diffstat and content_diffs:
            stat_str = render_diff_stat(content_diffs, term_control) + "\n\n"

        return stat_str + "\n".join(diffs)

    def summary(
        self,
        diffstat: bool = False,
        color: str = "auto",
        term_control: Optional[TermControl] = None,
    ) -> str:
        """
        Return a summary of this ``FsDiffResults`` instance.

        :param diffstat: Include "diffstat"-like change summary.
        :type diffstat: ``bool``
        :param color: A string to control color rendering: "auto", "always", or
                      "never".
        :type color: ``str``
        :param term_control: An optional ``TermControl`` instance to use for
                             formatting. The supplied instance overrides any
                             ``color`` argument if set.
        :type term_control: ``Optional[TermControl]``
        :returns: A string summarizing this instance.
        :rtype: ``str``
        """

        tc = term_control or TermControl(color=color)
        content_diffs = [r for r in self._records if r.has_content_diff]
        summary = (
            f"Total changes:     {len(self)}\n"
            f"  Paths {tc.GREEN + 'added:    ' + tc.NORMAL} {len(self.added)}\n"
            f"  Paths {tc.RED + 'removed:  ' + tc.NORMAL} {len(self.removed)}\n"
            f"  Paths {tc.YELLOW + 'modified: ' + tc.NORMAL} {len(self.modified)}\n"
            f"  Paths {tc.MAGENTA + 'withdiff: ' + tc.NORMAL} {len(content_diffs)}\n"
            f"  Paths {tc.CYAN + 'moved:    ' + tc.NORMAL} {len(self.moved)}\n"
            f"  Paths {tc.BLUE + 'different:' + tc.NORMAL} {len(self.type_changed)}"
        )
        stat_str = ""
        if diffstat and content_diffs:
            stat_str = "\n\n" + render_diff_stat(content_diffs, tc)
        return summary + stat_str

    def tree(
        self,
        color: str = "auto",
        desc: str = "none",
        term_control: Optional[TermControl] = None,
    ) -> str:
        """
        Render a ``DiffTree`` of this ``FsDiffResults`` instance.

        :param color: A string to control color tree rendering: "auto",
                      "always", or "never".
        :type color: ``str``
        :param desc: Include descriptions: "short" for brief description, "full"
                     for complete change descriptions or "none" to omit.
        :type desc: ``Optional[str]``
        :param term_control: An optional ``TermControl`` instance to use for
                             formatting. The supplied instance overrides any
                             ``color`` argument if set.
        :type term_control: ``Optional[TermControl]``
        :returns: A string representation of a difference tree
        :rtype: ``str``
        """
        tree = DiffTree.build_tree(
            self, color=color, quiet=self.options.quiet, term_control=term_control
        )
        return tree.render(desc=desc)


class DiffEngine:
    """
    Core class for generating fsdiff comparisons.
    """

    def __init__(self):
        """
        Initialise a new ``DiffEngine`` instance.
        """
        self.change_detector = ChangeDetector()
        self.content_differ = ContentDifferManager()

    # pylint: disable=too-many-locals
    # pylint: disable=too-many-branches
    # pylint: disable=too-many-statements
    # pylint: disable=too-many-nested-blocks
    def compute_diff(
        self,
        tree_a: Dict[str, FsEntry],
        tree_b: Dict[str, FsEntry],
        options: "DiffOptions" = None,
        term_control: Optional[TermControl] = None,
    ) -> FsDiffResults:
        """
        Main diff computation logic.

        :param tree_a: The first file system tree to compare: a dictionary of
                       path name -> file system entry mappings.
        :type tree_a: ``Dict[str, FsEntry]``
        :param tree_b: The second file system tree to compare: a dictionary of
                       path name -> file system entry mappings.
        :type tree_b: ``Dict[str, FsEntry]``
        :param options: Options to apply to the diff generation.
        :type options: ``DiffOptions``
        :param term_control: A ``TermControl`` instance to use for rendering color
                   output.
        :type term_control: ``TermControl``
        :returns: An ``FsDiffResults`` instance containing ``FsDiffRecord``
                  objects.
        :rtype: ``FsDiffResults``
        """
        if options is None:
            options = DiffOptions()

        diffs: List[FsDiffRecord] = []
        all_paths = set(tree_a.keys()) | set(tree_b.keys())
        _log_debug("Starting compute_diff with %d paths", len(all_paths))

        start_time = datetime.now()

        if not all_paths:
            _log_info("No paths to diff; returning empty FsDiffResults")
            return FsDiffResults(diffs, options, floor(start_time.timestamp()))

        if options.ignore_timestamps:
            _log_debug("Ignoring timestamp changes")
        if options.ignore_permissions:
            _log_debug("Ignoring permission changes")
        if options.ignore_ownership:
            _log_debug("Ignoring ownership changes")
        if options.content_only:
            _log_debug("Checking content changes only")

        term_control = term_control or TermControl()
        progress = ProgressFactory.get_progress(
            "Computing diffs",
            quiet=options.quiet,
            term_control=term_control,
        )

        progress.start(len(all_paths))
        try:
            # pylint: disable=too-many-nested-blocks
            for i, path in enumerate(sorted(all_paths)):
                entry_a = tree_a.get(path)
                entry_b = tree_b.get(path)
                _log_debug_fsdiff(
                    "Comparing path '%s' (A:%s // B:%s)", path, entry_a, entry_b
                )
                progress.progress(i, f"Comparing trees for '{path}'")

                if entry_a is None:
                    # File added in tree_b
                    diff_record = FsDiffRecord(path, DiffType.ADDED, new_entry=entry_b)
                    changes = self.change_detector.detect_added(entry_b, options)

                    for change in changes:
                        diff_record.add_change(change)

                    # Generate content diff if requested, appropriate, and within size limits
                    if options.include_content_diffs and entry_b.is_file:
                        within_limit = (
                            options.max_content_diff_size <= 0
                            or entry_b.size <= options.max_content_diff_size
                        )
                        if within_limit:
                            content_diff = self.content_differ.generate_content_diff(
                                None,
                                entry_b.full_path,
                                None,
                                entry_b,
                            )
                            if content_diff:
                                diff_record.set_content_diff(content_diff)

                    diffs.append(diff_record)

                elif entry_b is None:
                    # File removed from tree_a
                    diff_record = FsDiffRecord(
                        path, DiffType.REMOVED, old_entry=entry_a
                    )

                    changes = self.change_detector.detect_removed(entry_a, options)

                    for change in changes:
                        diff_record.add_change(change)

                    # Generate content diff if requested, appropriate, and within size limits
                    if options.include_content_diffs and entry_a.is_file:
                        within_limit = (
                            options.max_content_diff_size <= 0
                            or entry_a.size <= options.max_content_diff_size
                        )
                        if within_limit:
                            content_diff = self.content_differ.generate_content_diff(
                                entry_a.full_path,
                                None,
                                entry_a,
                                None,
                            )
                            if content_diff:
                                diff_record.set_content_diff(content_diff)

                    diffs.append(diff_record)

                else:
                    # File exists in both; first, detect any type changes.
                    if (
                        # pylint: disable=too-many-boolean-expressions
                        entry_a.is_file != entry_b.is_file
                        or entry_a.is_dir != entry_b.is_dir
                        or entry_a.is_symlink != entry_b.is_symlink
                        or entry_a.is_block != entry_b.is_block
                        or entry_a.is_char != entry_b.is_char
                        or entry_a.is_sock != entry_b.is_sock
                        or entry_a.is_fifo != entry_b.is_fifo
                    ):
                        diffs.append(
                            FsDiffRecord(path, DiffType.TYPE_CHANGED, entry_a, entry_b)
                        )
                        continue

                    # Otherwise, check for metadata/content changes.
                    changes = self.change_detector.detect_changes(
                        entry_a, entry_b, options
                    )

                    # If there are no effective changes (e.g. only metadata changes in
                    # content-only mode), skip this path entirely.
                    if not changes:
                        continue

                    diff_record = FsDiffRecord(
                        path, DiffType.MODIFIED, entry_a, entry_b
                    )
                    for change in changes:
                        diff_record.add_change(change)

                    has_content_change = any(
                        change.change_type == ChangeType.CONTENT for change in changes
                    )

                    # Generate content diff if requested, appropriate, and within size limits
                    if (
                        has_content_change
                        and options.include_content_diffs
                        and entry_a.is_file
                        and entry_b.is_file
                    ):
                        within_limit = (
                            options.max_content_diff_size <= 0
                            or max(entry_a.size, entry_b.size)
                            <= options.max_content_diff_size
                        )
                        if within_limit:
                            content_diff = self.content_differ.generate_content_diff(
                                entry_a.full_path,
                                entry_b.full_path,
                                entry_a,
                                entry_b,
                            )
                            if content_diff:
                                diff_record.set_content_diff(content_diff)

                    diffs.append(diff_record)

            end_time = datetime.now()

        except KeyboardInterrupt:
            progress.cancel("Quit!")
            raise
        except SystemExit:
            progress.cancel("Exiting.")
            raise

        progress.end(f"Found {len(diffs)} differences in {end_time - start_time}")

        # Detect moves/renames
        diffs = self._detect_moves(
            diffs, tree_a, tree_b, options, term_control=term_control
        )

        return FsDiffResults(diffs, options, floor(start_time.timestamp()))

    @staticmethod
    def _is_move_diff(diff, src_path, dest_path):
        """
        Return ``True`` if ``diff`` reflects a move diff type for the
        current ``path``/``dest_path`` or ``False`` otherwise.

        :param diff: The ``FsDiffRecord`` to inspect.
        :param src_path: The original path to check.
        :param dest_path: The destination path to check.
        """
        if diff.path == src_path and diff.diff_type == DiffType.REMOVED:
            return True
        if diff.path == dest_path and diff.diff_type == DiffType.ADDED:
            return True
        return False

    @staticmethod
    def _best_sibling_proximity(
        orig_path: str, candidates: List[Tuple[str, FsEntry]]
    ) -> Tuple[str, FsEntry]:
        """
        Sibling proximity heuristic: choose the "best" move destination
        when multiple candidates exist.

        :param orig_path: The original path before the move.
        :type orig_path: ``str``
        :param candidates: Possible candidates for the destination, as
                           a list of (path, entry) pairs.
        :type candidates: ``List[Tuple[str, FsEntry]]``
        :returns: The winning (path, entry) pair.
        :rtype: ``Tuple[str, FsEntry]``
        """

        def filename_similarity(path_a: str, path_b: str) -> float:
            """
            Quick and cheap similarity metric based on string matching and
            token overlap.

            If either path is ``None`` or empty the similarity is 0.0.

            If the paths are identical, in the same directory, or substrings of
            one another the similarity is 1.0.

            If none of these conditions are met the value returned is the
            Jaccard index of the two strings:

               https://en.wikipedia.org/wiki/Jaccard_index

            :param path_a: The first path to compare.
            :type path_a: ``str``
            :param path_b: The second path to compare.
            :type path_b: ``str``
            :returns: A value 0.0..1.0 indicating the approximate similarity of
                      the paths.
            :rtype: ``float``
            """
            if not path_a or not path_b:
                return 0.0

            # Same path? (shouldn't happen for moves, but if it does let's return asap).
            if path_a == path_b:
                return 1.0

            # Same directory?
            dir_a = os.path.dirname(path_a)
            dir_b = os.path.dirname(path_b)
            if dir_a == dir_b:
                return 1.0

            # Quick wins first
            if path_a.startswith(path_b) or path_b.startswith(path_a):
                return 1.0
            if path_a.endswith(path_b) or path_b.endswith(path_a):
                return 1.0

            # Token overlap for compound names
            tokens_a = frozenset(_SIBLING_REGEX.split(path_a))
            tokens_b = frozenset(_SIBLING_REGEX.split(path_b))
            overlap = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
            return overlap

        if len(candidates) > 1:
            candidates.sort(
                key=lambda x: filename_similarity(orig_path, x[0]),
                reverse=True,
            )

        return candidates[0]

    def _detect_moves(
        self,
        diffs: List[FsDiffRecord],
        tree_a: Dict[str, FsEntry],
        tree_b: Dict[str, FsEntry],
        options: DiffOptions,
        term_control: Optional[TermControl] = None,
    ) -> List[FsDiffRecord]:
        """
        Detect file moves/renames by matching content hashes

        :param diffs: A list of file system diff records to inspect.
        :type diffs: ``List[FsDiffRecord]``
        :param tree_a: The first file system tree to compare: a dictionary of
                       path name -> file system entry mappings.
        :type tree_a: ``Dict[str, FsEntry]``
        :param tree_b: The second file system tree to compare: a dictionary of
                       path name -> file system entry mappings.
        :type tree_b: ``Dict[str, FsEntry]``
        :param options: Options to apply to the diff generation.
        :type options: ``DiffOptions``
        :param term_control: A ``TermControl`` instance to use for rendering color
                   output.
        :type term_control: ``TermControl``
        :returns: Updated list of diff records with moves detected.
        :rtype: ``List[FsDiffRecord]``
        """
        # Pre-index existing added/removed records so we only treat genuine
        # remove+add pairs as moves (and not simple copies).
        added_paths = {
            diff.path
            for diff in diffs
            if diff.diff_type == DiffType.ADDED
            and diff.new_entry
            and diff.new_entry.is_file
            and diff.new_entry.content_hash
        }
        removed_paths = {
            diff.path
            for diff in diffs
            if diff.diff_type == DiffType.REMOVED
            and diff.old_entry
            and diff.old_entry.is_file
            and diff.old_entry.content_hash
        }
        modified_paths = {
            diff.path
            for diff in diffs
            if diff.diff_type == DiffType.MODIFIED
            and diff.old_entry
            and diff.old_entry.is_file
            and diff.old_entry.content_hash
        }
        _log_debug_fsdiff(
            "Initialising move detection: "
            "added_paths=%s, removed_paths=%s, modified_paths=%s",
            ", ".join(sorted(added_paths)),
            ", ".join(sorted(removed_paths)),
            ", ".join(sorted(modified_paths)),
        )
        _log_debug_fsdiff_extra(
            "Diff records: %s", ",\n\n".join(str(diff) for diff in diffs)
        )

        # Map of paths to their corresponding FsDiffRecord list
        diff_map = defaultdict(tuple)
        for diff in diffs:
            diff_map[diff.path] += (diff,)

        # Set of diff records to be pruned as a result of move detection.
        to_prune = set()

        # Index destination files by content hash, ignoring entries without a hash.
        dest_hashes = defaultdict(list)
        for path, entry in tree_b.items():
            if entry.is_file and entry.content_hash:
                dest_hashes[entry.content_hash].append((path, entry))

        # Ensure each destination path is used as a move target at most once.
        used_dests = set()

        # Anything to do?
        if not tree_a:
            return diffs

        term_control = term_control or TermControl()
        progress = ProgressFactory.get_progress(
            "Detecting moves",
            quiet=options.quiet,
            term_control=term_control,
        )

        moves = 0  # Count of move records
        start_time = datetime.now()
        progress.start(len(tree_a))
        try:
            for i, (path, entry_a) in enumerate(tree_a.items()):
                _log_debug_fsdiff("Detecting moves for tree_a path '%s'", path)
                progress.progress(i, f"Checking moves for '{path}'")
                # Only consider files with a valid content hash for move detection.
                if not (entry_a.is_file and entry_a.content_hash):
                    continue

                if path in tree_b and entry_a.content_hash == tree_b[path].content_hash:
                    continue

                candidates = dest_hashes.get(entry_a.content_hash)
                if not candidates:
                    continue

                candidates = [c for c in candidates if c[0] not in used_dests]
                if not candidates:
                    continue

                _log_debug_fsdiff_extra(
                    "Checking candidate destinations: %s",
                    ", ".join(cand_path for cand_path, entry in candidates),
                )

                # If multiple possible move destination candidates exists (i.e.
                # content with the hash of entry_a now exists at multiple file
                # system paths) we use a "sibling proximity heuristic" to select
                # the best available candidate. This prefers moves that are in the
                # same parent directory or that otherwise constitute "more similar"
                # strings (using a cheap string similarity metric approximation).
                dest_path, entry_b = self._best_sibling_proximity(path, candidates)

                _log_debug_fsdiff("Selected candidate %s: %s", dest_path, entry_b)
                # Only treat as a move if we have the corresponding REMOVED/ADDED
                # records; otherwise this is more likely a copy/duplicate.
                if path not in (removed_paths | modified_paths):
                    continue
                if dest_path not in (added_paths | modified_paths):
                    continue
                if dest_path == path:
                    continue
                if dest_path in used_dests:
                    continue

                # Found a move: count it
                moves += 1

                #: Add to cache of used destinations
                used_dests.add(dest_path)

                diff_record = FsDiffRecord(path, DiffType.MOVED, entry_a, entry_b)
                changes = self.change_detector.detect_changes(entry_a, entry_b, options)
                for change in changes:
                    diff_record.add_change(change)

                prune = [
                    diff
                    for diff in diff_map[path] + diff_map[dest_path]
                    if self._is_move_diff(diff, path, dest_path)
                ]
                to_prune.update(prune)

                diff_record.moved_from = path
                diff_record.moved_to = dest_path
                diffs.append(diff_record)

        except KeyboardInterrupt:
            progress.cancel("Quit!")
            raise
        except SystemExit:
            progress.cancel("Exiting.")
            raise

        # Prune ADDED/REMOVED diff records for detected moves
        _log_debug_fsdiff("Pruning %d diff records for detected moves", len(to_prune))
        diffs = [diff for diff in diffs if diff not in to_prune]

        end_time = datetime.now()
        progress.end(f"Found {moves} moves in {end_time - start_time}")

        return diffs
