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
from typing import List, Optional
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
            f"change_type: {self.change_type.value}, "
            f"old_value: {self.old_value}, "
            f"new_value: {self.new_value}, "
            f"description: {self.description}"
        )


class ChangeDetector:
    """
    Class to detect and classify changes in ``FsEntry`` objects.
    """

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
            return changes

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

        return changes
