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
from typing import Any, Dict, Iterator, List, Optional
from collections import defaultdict
from datetime import datetime
from enum import Enum
import logging
import json

from snapm import SNAPM_SUBSYSTEM_FSDIFF
from snapm.progress import TermControl

from .changes import ChangeDetector, ChangeType, FileChange
from .contentdiff import ContentDiff, ContentDifferManager
from .options import DiffOptions
from .treewalk import FsEntry

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error

ENGINE_LOG_ME_HARDER = False


def _log_debug_fsdiff(msg, *args, **kwargs):
    """A wrapper for fsdiff subsystem debug logs."""
    _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_FSDIFF}, **kwargs)


def _log_debug_fsdiff_extra(msg, *args, **kwargs):
    """A wrapper for fsdiff subsystem debug logs."""
    if ENGINE_LOG_ME_HARDER:  # pragma: no cover
        _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_FSDIFF}, **kwargs)


class DiffType(Enum):
    """
    Enum for different difference types.
    """

    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    MOVED = "moved"
    TYPE_CHANGED = "type_changed"


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
        changes = (
            f"  changes: {', '.join(str(chg) for chg in self.changes)}\n"
            if self.changes
            else ""
        )

        moved_from = f"moved_from: {self.moved_from}\n" if self.moved_from else ""
        moved_to = f"moved_to: {self.moved_to}\n" if self.moved_to else ""

        content_diff = (
            f"  content_diff: {self.content_diff}\n"
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
            f"  old_entry:\n{self.old_entry if self.old_entry else ''}\n"
            f"  new_entry:\n{self.new_entry if self.new_entry else ''}\n"
            f"{changes}"  # no newline (embedded if set)
            f"{content_diff}"  # no newline (embedded if set)
            f"{'  ' + moved_from if moved_from else ''}"  # no newline (embedded if set)
            f"{'  ' + moved_to if moved_to else ''}"  # no newline (embedded if set)
            f"  file_path: {self.file_path}\n"
            f"  file_type: {self.file_type}\n"
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

    if tc:
        diff_lines = [
            (
                (tc.RED + _rstrip_nl(line) + tc.WHITE)
                if line.startswith("-")
                else (
                    (tc.GREEN + _rstrip_nl(line) + tc.WHITE)
                    if line.startswith("+")
                    else _rstrip_nl(line)
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


class FsDiffResults:
    """Container for filesystem diff results with formatting methods."""

    def __init__(self, records: List[FsDiffRecord]):
        self._records = records

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
        Return the total number of changes in thie ``FsDiffResults` instance:
        equvalent to ``len(self)``.

        :returns: Count of changes.
        :rtype: ``int``
        """
        return len(self)

    @property
    def content_changes(self) -> int:
        """
        Return the number of content changes in thie ``FsDiffResults` instance.

        :returns: Count of changes with content diff.
        :rtype: ``int``
        """
        return len([r for r in self._records if r.has_content_diff])

    @property
    def added(self) -> List[FsDiffRecord]:
        """
        Return added changes in thie ``FsDiffResults` instance.

        :returns: Changes with ``DiffType.ADDED`` type.
        :rtype: ``int``
        """
        return [r for r in self._records if r.diff_type == DiffType.ADDED]

    @property
    def removed(self) -> List[FsDiffRecord]:
        """
        Return removed changes in thie ``FsDiffResults` instance.

        :returns: Changes with ``DiffType.REMOVED`` type.
        :rtype: ``int``
        """
        return [r for r in self._records if r.diff_type == DiffType.REMOVED]

    @property
    def modified(self) -> List[FsDiffRecord]:
        """
        Return modified changes in thie ``FsDiffResults` instance.

        :returns: Changes with ``DiffType.MODIFIED`` type.
        :rtype: ``int``
        """
        return [r for r in self._records if r.diff_type == DiffType.MODIFIED]

    @property
    def moved(self) -> List[FsDiffRecord]:
        """
        Return moved changes in thie ``FsDiffResults` instance.

        :returns: Changes with ``DiffType.MOVED`` type.
        :rtype: ``int``
        """
        return [r for r in self._records if r.diff_type == DiffType.MOVED]

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
                f"\n  Summary: {record.content_diff_summary}"
                if record.content_diff_summary
                else ""
            )

            change_descs = ", ".join(chg.description for chg in record.changes)
            description = f"\n  Description: {change_descs}" if change_descs else ""

            sep = "" if first else "\n"
            out += (
                f"{sep}"
                f"Path: {record.path}\n  DiffType: {record.diff_type.value}"
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

    def diff(self, color: str = "auto") -> str:
        """
        Return unified diff representation of content changes for this
        instance.

        :returns: unified diff string description of file system changes.
        :rtype: ``str``
        """
        tc = (
            TermControl()
            if color == "auto"
            else TermControl(force_ansi=True) if color == "always" else None
        )
        content_diffs = [r for r in self._records if r.has_content_diff]
        diffs = [render_unified_diff(diff, tc) for diff in content_diffs]
        return "\n".join(diffs)

    # def tree(self) -> str:  # Coming later!


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

    def _effective_changes(
        self, changes: List[FileChange], options: DiffOptions
    ) -> List[FileChange]:
        """
        Elide ignored change types in ``changes``.

        :param changes: The list of changes to examine.
        :type changes: ``List[FileChange]``
        :param options: Effective diff options.
        :type options: ``DiffOptions``
        :returns: Changes pruned according to options.
        :rtype: ``List[FileChange]``
        """
        return (
            [c for c in changes if c.change_type == ChangeType.CONTENT]
            if options.content_only
            else changes
        )

    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    def compute_diff(
        self,
        tree_a: Dict[str, FsEntry],
        tree_b: Dict[str, FsEntry],
        options: "DiffOptions" = None,
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
        :returns: A list of file system diff records.
        :rtype: ``List[FsDiffRecord]``
        """
        if options is None:
            options = DiffOptions()

        diffs = []
        all_paths = set(tree_a.keys()) | set(tree_b.keys())
        _log_debug("Starting compute_diff with %d paths", len(all_paths))

        if options.ignore_timestamps:
            _log_debug("Ignoring timestamp changes")
        if options.ignore_permissions:
            _log_debug("Ignoring permission changes")
        if options.ignore_ownership:
            _log_debug("Ignoring ownership changes")
        if options.content_only:
            _log_debug("Checking content changes only")

        # pylint: disable=too-many-nested-blocks
        for path in sorted(all_paths):
            entry_a = tree_a.get(path)
            entry_b = tree_b.get(path)
            _log_debug_fsdiff(
                "Comparing path '%s' (A:%s // B:%s)", path, entry_a, entry_b
            )
            if entry_a is None:
                # File added in tree_b
                diff_record = FsDiffRecord(path, DiffType.ADDED, new_entry=entry_b)
                changes = self.change_detector.detect_added(entry_b, options)

                # Optionally restrict to content-only changes.
                effective_changes = self._effective_changes(changes, options)

                for change in effective_changes:
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
                diff_record = FsDiffRecord(path, DiffType.REMOVED, old_entry=entry_a)

                changes = self.change_detector.detect_removed(entry_a, options)

                # Optionally restrict to content-only changes.
                effective_changes = self._effective_changes(changes, options)

                for change in effective_changes:
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
                changes = self.change_detector.detect_changes(entry_a, entry_b, options)

                # Optionally restrict to content-only changes.
                effective_changes = self._effective_changes(changes, options)

                # If there are no effective changes (e.g. only metadata changes in
                # content-only mode), skip this path entirely.
                if not effective_changes:
                    continue

                diff_record = FsDiffRecord(path, DiffType.MODIFIED, entry_a, entry_b)
                for change in effective_changes:
                    diff_record.add_change(change)

                has_content_change = any(
                    change.change_type == ChangeType.CONTENT
                    for change in effective_changes
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

        # Detect moves/renames
        diffs = self._detect_moves(diffs, tree_a, tree_b, options)

        return FsDiffResults(diffs)

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

    def _detect_moves(
        self,
        diffs: List[FsDiffRecord],
        tree_a: Dict[str, FsEntry],
        tree_b: Dict[str, FsEntry],
        options: DiffOptions,
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
        changed_paths = {
            diff.path
            for diff in diffs
            if diff.diff_type == DiffType.MODIFIED
            and diff.old_entry
            and diff.old_entry.is_file
            and diff.old_entry.content_hash
        }
        _log_debug_fsdiff(
            "Initialising move detection: "
            "added_paths=%s, removed_paths=%s, changed_paths=%s",
            ", ".join(sorted(added_paths)),
            ", ".join(sorted(removed_paths)),
            ", ".join(sorted(changed_paths)),
        )
        _log_debug_fsdiff_extra(
            "Diff records: %s", ",\n\n".join(str(diff) for diff in diffs)
        )

        # Map of paths to their corresponding FsDiffRecord list
        diff_map = defaultdict(list)
        for diff in diffs:
            diff_map[diff.path].append(diff)

        # Set of diff records to be pruned as a result of move detection.
        to_prune = set()

        # Index destination files by content hash, ignoring entries without a hash.
        dest_hashes = defaultdict(list)
        for path, entry in tree_b.items():
            if entry.is_file and entry.content_hash:
                dest_hashes[entry.content_hash].append((path, entry))

        # Ensure each destination path is used as a move target at most once.
        used_dests = set()

        for path, entry_a in tree_a.items():
            _log_debug_fsdiff("Detecting moves for tree_a path '%s'", path)
            # Only consider files with a valid content hash for move detection.
            if not (entry_a.is_file and entry_a.content_hash):
                continue

            candidates = dest_hashes.get(entry_a.content_hash)
            if not candidates:
                continue

            _log_debug_fsdiff_extra(
                "Checking candidate destinations: %s",
                ", ".join(cand_path for cand_path, entry in candidates),
            )
            # If multiple possible move destination candidates exists (i.e.
            # content with the hash of entry_a now exists at multiple file
            # system paths) we arbitrarily choose the first entry to be the
            # "move destination". This could be enhanced in future with more
            # complex rules, e.g. preferring a dest that is in the same
            # directory, mount point, etc.
            dest_path, entry_b = candidates[0]

            _log_debug_fsdiff("Selected candidate %s: %s", dest_path, entry_b)
            # Only treat as a move if we have the corresponding REMOVED/ADDED
            # records; otherwise this is more likely a copy/duplicate.
            if path not in (removed_paths | changed_paths):
                continue
            if dest_path not in (added_paths | changed_paths):
                continue
            if dest_path == path:
                continue
            if dest_path in used_dests:
                continue

            used_dests.add(dest_path)

            diff_record = FsDiffRecord(path, DiffType.MOVED, entry_a, entry_b)
            changes = self.change_detector.detect_changes(entry_a, entry_b, options)
            effective_changes = self._effective_changes(changes, options)
            for change in effective_changes:
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

        # Prune ADDED/REMOVED diff records for detected moves
        _log_debug_fsdiff("Pruning %d diff records for detected moves", len(to_prune))
        diffs = [diff for diff in diffs if diff not in to_prune]

        return diffs
