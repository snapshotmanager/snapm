# Copyright Red Hat
#
# snapm/fsdiff/__init__.py - Snapshot Manager fs differ package
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
File system diff package.

Provides snapshot-set comparison facilities including tree walking, change
detection, content diffing, and move detection. The main entry points are
``FsDiffer`` and ``DiffOptions``.
"""
from .engine import FsDiffRecord, FsDiffResults
from .fsdiffer import FsDiffer
from .options import DiffOptions
from .tree import DiffTree

__all__ = [
    "DiffOptions",
    "DiffTree",
    "FsDiffer",
    "FsDiffRecord",
    "FsDiffResults",
]
