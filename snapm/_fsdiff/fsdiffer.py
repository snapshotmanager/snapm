# Copyright Red Hat
#
# snapm/_fsdiff/fsdiffer.py - Snapshot Manager file system differ
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Top-level fsdiff interface.
"""
from typing import List, TYPE_CHECKING

from .engine import DiffEngine, FsDiffRecord
from .options import DiffOptions
from .treewalk import TreeWalker

if TYPE_CHECKING:
    from snapm.manager import Manager
    from snapm.manager._mounts import Mount


class FsDiffer:
    """
    Top-level interface for generating file system comparisons.
    """

    def __init__(self, manager: "Manager"):
        self.manager = manager
        self.tree_walker = TreeWalker()
        self.diff_engine = DiffEngine()

    def compare_roots(
        self, mount_a: "Mount", mount_b: "Mount", options: DiffOptions = None
    ) -> List[FsDiffRecord]:
        """Compare two mounted snapshot sets and return list of diff records"""
        raise NotImplementedError(
            "compare_roots not yet implemented"
        )  # pragma: no cover

    def compare_snapshots(self, snapshot_a: str, snapshot_b: str) -> List[FsDiffRecord]:
        """Compare two snapshots by name/timestamp"""
        raise NotImplementedError(
            "compare_snapshots not yet implemented"
        )  # pragma: no cover

    def diff_against_live(self, snapshot_mount: "Mount") -> List[FsDiffRecord]:
        """Compare snapshot against current live filesystem"""
        raise NotImplementedError(
            "diff_against_live not yet implemented"
        )  # pragma: no cover
