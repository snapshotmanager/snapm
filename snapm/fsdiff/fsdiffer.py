# Copyright Red Hat
#
# snapm/fsdiff/fsdiffer.py - Snapshot Manager file system differ
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Top-level fsdiff interface.
"""
from typing import Optional, TYPE_CHECKING

from snapm.progress import ProgressBase, TermControl

from .engine import DiffEngine, FsDiffResults
from .options import DiffOptions
from .treewalk import TreeWalker

if TYPE_CHECKING:
    from snapm.manager import Manager
    from snapm.manager._mounts import Mount


class FsDiffer:
    """
    Top-level interface for generating file system comparisons.
    """

    def __init__(
        self,
        manager: "Manager",
        options: Optional[DiffOptions] = None,
        progress: Optional[ProgressBase] = None,
        term_control: Optional[TermControl] = None,
    ):
        options = options or DiffOptions()
        self.manager: "Manager" = manager
        self.options: DiffOptions = options
        self.tree_walker: TreeWalker = TreeWalker(options)
        self.diff_engine: DiffEngine = DiffEngine()
        self._progress: Optional[ProgressBase] = progress
        self._term_control: Optional[TermControl] = term_control

    def compare_roots(self, mount_a: "Mount", mount_b: "Mount") -> FsDiffResults:
        """
        Compare two mounted snapshot sets and return list of diff records
        """
        strip_prefix_a = "" if mount_a.root == "/" else mount_a.root
        tree_a = self.tree_walker.walk_tree(
            mount_a,
            strip_prefix=strip_prefix_a,
            quiet=self.options.quiet,
            progress=self._progress,
            term_control=self._term_control,
        )

        strip_prefix_b = "" if mount_b.root == "/" else mount_b.root
        tree_b = self.tree_walker.walk_tree(
            mount_b,
            strip_prefix=strip_prefix_b,
            quiet=self.options.quiet,
            progress=self._progress,
            term_control=self._term_control,
        )

        return self.diff_engine.compute_diff(tree_a, tree_b, self.options)

    def compare_snapshots(self, snapshot_a: str, snapshot_b: str) -> FsDiffResults:
        """Compare two snapshots by name/timestamp"""
        raise NotImplementedError(
            "compare_snapshots not yet implemented"
        )  # pragma: no cover

    def diff_against_live(self, snapshot_mount: "Mount") -> FsDiffResults:
        """Compare snapshot against current live filesystem"""
        raise NotImplementedError(
            "diff_against_live not yet implemented"
        )  # pragma: no cover
