# Copyright Red Hat
#
# snapm/fsdiff/changes.py - Snapshot Manager fs diff change detection
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
File system entry change detection and classification.
"""
from typing import Dict, List, Optional
from enum import Enum
import logging
import stat

from snapm import SNAPM_SUBSYSTEM_FSDIFF

from .options import DiffOptions
from .treewalk import FsEntry

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


def _log_debug_fsdiff(msg, *args, **kwargs):
    """A wrapper for fsdiff subsystem debug logs."""
    _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_FSDIFF}, **kwargs)


class ChangeType(Enum):
    """
    Enum representing different types of file system entry change.
    """

    CONTENT = "content"
    PERMISSIONS = "permissions"
    OWNERSHIP = "ownership"
    TIMESTAMPS = "timestamps"
    XATTRS = "extended_attributes"
    SYMLINK_TARGET = "symlink_target"


class FileChange:
    """
    Representation of a file system entry change.
    """

    def __init__(
        self,
        change_type: ChangeType,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        description: str = "",
    ):
        """
        Initialise a new ``FileChange`` object.

        :param change_type: The type of change.
        :type change_type: ``ChangeType``
        :param old_value: The original value.
        :type old_value: ``Optional[str]``
        :param new_value: The changed value.
        :type new_value: ``Optional[str]``
        :param description: A string description of the change.
        :type description: ``str``
        """
        self.change_type = change_type
        self.old_value = old_value
        self.new_value = new_value
        self.description = description

    def __str__(self) -> str:
        """
        Return a string representation of this ``FileChange`` object.

        :returns: A human readable string representation of this instance.
        :rtype: str
        """
        return (
            f"    change_type: {self.change_type.value}\n"
            f"      old_value: {self.old_value}\n"
            f"      new_value: {self.new_value}\n"
            f"      description: {self.description}"
        )

    def to_dict(self) -> Dict[str, Optional[str]]:
        """
        Convert this ``FileChange`` object into a dictionary representation
        suitable for encoding as JSON.

        :returns: A dictionary mapping this instance's keys to values.
        :rtype: ``Dict[str, Optional[str]]``
        """
        return {
            "change_type": self.change_type.value,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "description": self.description,
        }


class ChangeDetector:
    """
    Class to detect and classify changes in ``FsEntry`` objects.
    """

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

    # pylint: disable=too-many-branches
    def detect_added(
        self,
        new_entry: FsEntry,
        options: DiffOptions,
    ) -> List[FileChange]:
        """
        Detect all changes for a newly added file.

        :param new_entry: The updated file system entry.
        :type new_entry: ``FsEntry``
        :param options: Change detection options.
        :type options: ``DiffOptions``
        :returns: A list of changes to the file system entry.
        :rtype: ``List[FileChange]``
        """
        changes = []

        _log_debug_fsdiff("Detecting changes for added file B:%s", new_entry.path)

        # If content_only is set, skip all metadata comparisons
        if options.content_only:
            # Content changes (for regular files)
            if new_entry.is_file:
                _log_debug_fsdiff(
                    "Detected content-only change (added %s)",
                    f"{new_entry.content_hash[0:16] if new_entry.content_hash else ''}",
                )
                changes.append(
                    FileChange(
                        ChangeType.CONTENT,
                        "",
                        new_entry.content_hash,
                        "content hash changed",
                    )
                )
            return self._effective_changes(changes, options)

        # Content changes (for regular files)
        if new_entry.is_file:
            _log_debug_fsdiff(
                "Detected content change (added %s)",
                f"{new_entry.content_hash[0:16] if new_entry.content_hash else ''}",
            )
            changes.append(
                FileChange(
                    ChangeType.CONTENT,
                    "",
                    new_entry.content_hash,
                    "content hash changed",
                )
            )

        # Permission changes
        if not options.ignore_permissions:
            new_perms = stat.S_IMODE(new_entry.mode)
            _log_debug_fsdiff(
                "Detected mode change (added 0o%o)",
                new_perms,
            )
            changes.append(
                FileChange(
                    ChangeType.PERMISSIONS,
                    "0o0",
                    oct(new_perms),
                    f"mode changed from 0o0 to {oct(new_perms)}",
                )
            )

        # Ownership changes
        if not options.ignore_ownership:
            _log_debug_fsdiff(
                "Detected ownership change (added %d:%d)",
                new_entry.uid,
                new_entry.gid,
            )
            changes.append(
                FileChange(
                    ChangeType.OWNERSHIP,
                    "none:none",
                    f"{new_entry.uid}:{new_entry.gid}",
                    "owner changed",
                )
            )

        # Symlink target changes
        if new_entry.is_symlink:
            _log_debug_fsdiff(
                "Detected symlink target change (added '%s')",
                new_entry.symlink_target,
            )
            changes.append(
                FileChange(
                    ChangeType.SYMLINK_TARGET,
                    "",
                    new_entry.symlink_target,
                    "symlink target changed",
                )
            )

        # Timestamp changes
        if not options.ignore_timestamps:
            _log_debug_fsdiff(
                "Detected mtime change (added '%.6f')",
                new_entry.mtime,
            )
            changes.append(
                FileChange(
                    ChangeType.TIMESTAMPS,
                    "",
                    str(new_entry.mtime),
                    "modification time changed",
                )
            )

        # Extended attribute changes
        if new_entry.xattrs:
            _log_debug_fsdiff(
                "Detected xattr change (added '%s')",
                ", ".join(f"{k}:{v}" for k, v in new_entry.xattrs.items()),
            )
            changes.append(
                FileChange(
                    ChangeType.XATTRS,
                    "",
                    str(new_entry.xattrs),
                    "extended attributes changed",
                )
            )

        return self._effective_changes(changes, options)

    # pylint: disable=too-many-branches
    def detect_removed(
        self,
        old_entry: FsEntry,
        options: DiffOptions,
    ) -> List[FileChange]:
        """
        Detect all changes for a removed file.

        :param old_entry: The updated file system entry.
        :type old_entry: ``FsEntry``
        :param options: Change detection options.
        :type options: ``DiffOptions``
        :returns: A list of changes to the file system entry.
        :rtype: ``List[FileChange]``
        """
        changes = []

        _log_debug_fsdiff("Detecting changes for removed file B:%s", old_entry.path)

        # If content_only is set, skip all metadata comparisons
        if options.content_only:
            # Content changes (for regular files)
            if old_entry.is_file:
                _log_debug_fsdiff(
                    "Detected content-only change (removed %s)",
                    f"{old_entry.content_hash[0:16] if old_entry.content_hash else ''}",
                )
                changes.append(
                    FileChange(
                        ChangeType.CONTENT,
                        old_entry.content_hash,
                        "",
                        "content hash changed",
                    )
                )
            return self._effective_changes(changes, options)

        # Content changes (for regular files)
        if old_entry.is_file:
            _log_debug_fsdiff(
                "Detected content change (removed %s)",
                f"{old_entry.content_hash[0:16] if old_entry.content_hash else ''}",
            )
            changes.append(
                FileChange(
                    ChangeType.CONTENT,
                    old_entry.content_hash,
                    "",
                    "content hash changed",
                )
            )

        # Permission changes
        if not options.ignore_permissions:
            old_perms = stat.S_IMODE(old_entry.mode)
            _log_debug_fsdiff(
                "Detected mode change (removed 0o%o)",
                old_perms,
            )
            changes.append(
                FileChange(
                    ChangeType.PERMISSIONS,
                    oct(old_perms),
                    "0o0",
                    f"mode changed from {oct(old_perms)} to 0o0",
                )
            )

        # Ownership changes
        if not options.ignore_ownership:
            _log_debug_fsdiff(
                "Detected ownership change (removed %d:%d)",
                old_entry.uid,
                old_entry.gid,
            )
            changes.append(
                FileChange(
                    ChangeType.OWNERSHIP,
                    f"{old_entry.uid}:{old_entry.gid}",
                    "none:none",
                    "owner changed",
                )
            )

        # Symlink target changes
        if old_entry.is_symlink:
            _log_debug_fsdiff(
                "Detected symlink target change (removed '%s')",
                old_entry.symlink_target,
            )
            changes.append(
                FileChange(
                    ChangeType.SYMLINK_TARGET,
                    old_entry.symlink_target,
                    "",
                    "symlink target changed",
                )
            )

        # Timestamp changes
        if not options.ignore_timestamps:
            _log_debug_fsdiff(
                "Detected mtime change (removed '%.6f')",
                old_entry.mtime,
            )
            changes.append(
                FileChange(
                    ChangeType.TIMESTAMPS,
                    str(old_entry.mtime),
                    "",
                    "modification time changed",
                )
            )

        # Extended attribute changes
        if old_entry.xattrs:
            _log_debug_fsdiff(
                "Detected xattr change (removed '%s')",
                ", ".join(f"{k}:{v}" for k, v in old_entry.xattrs.items()),
            )
            changes.append(
                FileChange(
                    ChangeType.XATTRS,
                    str(old_entry.xattrs),
                    "",
                    "extended attributes changed",
                )
            )

        return self._effective_changes(changes, options)

    # pylint: disable=too-many-branches
    def detect_changes(
        self,
        old_entry: FsEntry,
        new_entry: FsEntry,
        options: DiffOptions,
    ) -> List[FileChange]:
        """
        Detect all changes between two file system entries.

        :param old_entry: The original file system entry.
        :type old_entry: ``FsEntry``
        :param new_entry: The updated file system entry.
        :type new_entry: ``FsEntry``
        :param options: Change detection options.
        :type options: ``DiffOptions``
        :returns: A list of changes to the file system entry.
        :rtype: ``List[FileChange]``
        """
        changes = []

        _log_debug_fsdiff(
            "Detecting changes for A:%s // B:%s", old_entry.path, new_entry.path
        )

        # If content_only is set, skip all metadata comparisons
        if options.content_only:
            # Content changes (for regular files)
            if old_entry.is_file and new_entry.is_file:
                if old_entry.content_hash != new_entry.content_hash:
                    _log_debug_fsdiff(
                        "Detected content-only change (%s != %s)",
                        f"{old_entry.content_hash[0:16] if old_entry.content_hash else ''}",
                        f"{new_entry.content_hash[0:16] if new_entry.content_hash else ''}",
                    )
                    changes.append(
                        FileChange(
                            ChangeType.CONTENT,
                            old_entry.content_hash,
                            new_entry.content_hash,
                            "content hash changed",
                        )
                    )
            return self._effective_changes(changes, options)

        # Content changes (for regular files)
        if old_entry.is_file and new_entry.is_file:
            if old_entry.content_hash != new_entry.content_hash:
                _log_debug_fsdiff(
                    "Detected content change (%s != %s)",
                    f"{old_entry.content_hash[0:16] if old_entry.content_hash else ''}",
                    f"{new_entry.content_hash[0:16] if new_entry.content_hash else ''}",
                )
                changes.append(
                    FileChange(
                        ChangeType.CONTENT,
                        old_entry.content_hash,
                        new_entry.content_hash,
                        "content hash changed",
                    )
                )

        # Permission changes
        if not options.ignore_permissions:
            old_perms = stat.S_IMODE(old_entry.mode)
            new_perms = stat.S_IMODE(new_entry.mode)
            if old_perms != new_perms:
                _log_debug_fsdiff(
                    "Detected mode change (0o%o != 0o%o)",
                    old_perms,
                    new_perms,
                )
                changes.append(
                    FileChange(
                        ChangeType.PERMISSIONS,
                        oct(old_perms),
                        oct(new_perms),
                        f"mode changed from {oct(old_perms)} to {oct(new_perms)}",
                    )
                )

        # Ownership changes
        if not options.ignore_ownership:
            if old_entry.uid != new_entry.uid or old_entry.gid != new_entry.gid:
                _log_debug_fsdiff(
                    "Detected ownership change (%d:%d != %d:%d)",
                    old_entry.uid,
                    old_entry.gid,
                    new_entry.uid,
                    new_entry.gid,
                )
                changes.append(
                    FileChange(
                        ChangeType.OWNERSHIP,
                        f"{old_entry.uid}:{old_entry.gid}",
                        f"{new_entry.uid}:{new_entry.gid}",
                        "owner changed",
                    )
                )

        # Symlink target changes
        if old_entry.is_symlink and new_entry.is_symlink:
            if old_entry.symlink_target != new_entry.symlink_target:
                _log_debug_fsdiff(
                    "Detected symlink target change ('%s' != '%s')",
                    old_entry.symlink_target,
                    new_entry.symlink_target,
                )
                changes.append(
                    FileChange(
                        ChangeType.SYMLINK_TARGET,
                        old_entry.symlink_target,
                        new_entry.symlink_target,
                        "symlink target changed",
                    )
                )

        # Timestamp changes
        if not options.ignore_timestamps:
            if old_entry.mtime != new_entry.mtime:
                _log_debug_fsdiff(
                    "Detected mtime change ('%d' != '%d')",
                    old_entry.mtime,
                    new_entry.mtime,
                )
                changes.append(
                    FileChange(
                        ChangeType.TIMESTAMPS,
                        str(old_entry.mtime),
                        str(new_entry.mtime),
                        "modification time changed",
                    )
                )

        # Extended attribute changes
        if old_entry.xattrs != new_entry.xattrs:
            _log_debug_fsdiff(
                "Detected xattr change ('%s' != '%s')",
                ", ".join(f"{k}:{v}" for k, v in old_entry.xattrs.items()),
                ", ".join(f"{k}:{v}" for k, v in new_entry.xattrs.items()),
            )
            changes.append(
                FileChange(
                    ChangeType.XATTRS,
                    str(old_entry.xattrs),
                    str(new_entry.xattrs),
                    "extended attributes changed",
                )
            )

        return self._effective_changes(changes, options)
