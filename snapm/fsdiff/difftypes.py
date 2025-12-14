# Copyright Red Hat
#
# snapm/fsdiff/difftypes.py - Snapshot Manager fs diff types
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
File system diff engine
"""
from enum import Enum


class DiffType(Enum):
    """
    Enum for different difference types.
    """

    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    MOVED = "moved"
    TYPE_CHANGED = "type_changed"
