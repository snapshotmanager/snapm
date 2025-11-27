# Copyright Red Hat
#
# snapm/_fsdiff/options.py - Snapshot Manager fs diff options
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
File system diff options and categories.
"""
from typing import List, Optional
from enum import Enum


class DiffOptions:
    """
    File system comparison options.
    """

    def __init__(
        self,
        ignore_timestamps: bool = False,
        ignore_permissions: bool = False,
        ignore_ownership: bool = False,
        content_only: bool = False,
        include_system_dirs: bool = True,
        include_content_diffs: bool = True,
        max_file_size: int = 0,
        max_content_diff_size: int = 2**20,
        file_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ):
        """
        Initialise a new ``DiffOptions`` object.

        :param ignore_timestamps: ``True`` if timestamps should be ignored in
                                  comparisons or ``False`` otherwise.
        :type ignore_timestamps: ``bool``
        :param ignore_permissions: ``True`` if permissions should be ignored in
                                  comparisons or ``False`` otherwise.
        :type ignore_permissions: ``bool``
        :param ignore_ownership: ``True`` if ownership should be ignored in
                                  comparisons or ``False`` otherwise.
        :type ignore_ownership: ``bool``
        :param content_only: If ``True`` ignore timestamps, permissions,
                             ownership and other metadata and compare only file
                             content changes.
        :type content_only: ``bool``
        :param include_system_dirs: ``True`` if system directories should be
                                    included in the comparison.
        :type include_system_dirs: ``bool``
        :param include_content_diffs: ``True`` if the comparison should generate
                                     content diffs.
        :type include_content_diffs: ``bool``
        :param max_file_size: Skip files larger than this size. A value of zero
                              disables skipping large files.
        :type max_file_size: ``int``
        :param max_content_diff_size: A value in bytes that limits the maximum
                                      size of files to generate diffs for. Files
                                      exceeding this value will include only
                                      metadata changes and no detailed content
                                      diff will be generated.
        :type max_content_diff_size: ``int``
        :param file_patterns: A list of file patterns in shell glob notation to
                              include in the comparison.
        :type file_patterns: ``Optional[List[str]]``
        :param exclude_patterns: A list of file patterns in shell glob notation to
                                 exclude from the comparison.
        :type exclude_patterns: ``Optional[List[str]]``

        """
        self.ignore_timestamps = ignore_timestamps
        self.ignore_permissions = ignore_permissions
        self.ignore_ownership = ignore_ownership
        self.content_only = content_only
        self.include_system_dirs = include_system_dirs
        self.include_content_diffs = include_content_diffs
        self.max_file_size = max_file_size
        self.max_content_diff_size = max_content_diff_size
        self.file_patterns = file_patterns or []
        self.exclude_patterns = exclude_patterns or []

    def __str__(self) -> str:
        """
        Return a string representation of this ``DiffOptions`` object.

        :returns: A human readable string representing this instance.
        :rtype: ``str``
        """
        return (
            f"ignore_timestamps={self.ignore_timestamps}\n"
            f"ignore_permissions={self.ignore_permissions}\n"
            f"ignore_ownership={self.ignore_ownership}\n"
            f"content_only={self.content_only}\n"
            f"include_system_dirs={self.include_system_dirs}\n"
            f"include_content_diffs={self.include_content_diffs}\n"
            f"max_file_size={self.max_file_size}\n"
            f"max_content_diff_size={self.max_content_diff_size}\n"
            f"file_patterns={' '.join(self.file_patterns)}\n"
            f"exclude_patterns={' '.join(self.exclude_patterns)}\n"
        )


class DiffCategories(Enum):
    """
    Enum for categories of difference based on type or file system location.
    """

    CRITICAL_SYSTEM = "critical_system"  # /etc, /boot changes
    USER_DATA = "user_data"  # /home changes
    APPLICATION = "application"  # /usr, /opt changes
    TEMPORARY = "temporary"  # /tmp, /var/tmp
    LOG_FILES = "log_files"  # /var/log changes
    PACKAGE_MANAGEMENT = "package_mgmt"  # Package-related changes
