# Copyright Red Hat
#
# snapm/fsdiff/tree.py - Snapshot Manager fs diff tree renderer
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
File system diff tree rendering
"""
from typing import Dict, Optional, TYPE_CHECKING
import logging

from ..progress import TermControl
from .difftypes import DiffType

if TYPE_CHECKING:
    from .engine import FsDiffRecord, FsDiffResults

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


class TreeNode:
    """Internal class representing difference tree nodes."""

    def __init__(self, name: str, record: Optional["FsDiffRecord"] = None, to_or_from: Optional[str] = None):
        """
        Initialise a new ``TreeNode`` with the specified name and diff record.

        :param name: The name of this node.
        :type name: ``str``
        :param record: The diff record for this node.
        :type record: ``Optional["FsDiffRecord"]``
        """
        self.name: str = name
        self.record: "Optional[FsDiffRecord]" = record  # None for intermediate dirs
        self.children: Dict[str, TreeNode] = {}
        self.to_or_from = to_or_from


class DiffTree:
    """Top level interface for rendering difference trees"""

    def __init__(self, root: TreeNode, term_control: Optional[TermControl] = None):
        """
        Initialise a new ``DiffTree` object.

        :param root: The root node for this ``DiffTree`.
        :type root: ``TreeNode``
        :param term_control: An optional ``TermControl`` instance to use for formatting.
        :type term_control: ``Optional[TermControl]``
        """
        if root is None:
            raise ValueError("Root node is undefined")

        self.root: TreeNode = root
        self.term_control: TermControl = term_control or TermControl()
        self._rendered: Optional[str] = None

    def get_change_marker(self, record: Optional["FsDiffRecord"], node: TreeNode) -> str:
        """
        Return color-coded change mark for difference record.

        :param record: The file syste diff record to generate a marker for.
        :type record: ``Optional["FsDiffRecord"]``
        :returns: Change marker with embedded color codes.
        :rtype: ``str``
        """
        if not record:
            return ""

        marker_map = {
            DiffType.ADDED: (self.term_control.GREEN, "[+]"),
            DiffType.REMOVED: (self.term_control.RED, "[-]"),
            DiffType.MODIFIED: (self.term_control.YELLOW, "[*]"),
        }

        if record.diff_type == DiffType.MOVED:
            color = self.term_control.CYAN
            if node.to_or_from == "from":
                marker = "[<]"
            elif node.to_or_from == "to":
                marker = "[>]"
            else:
                raise ValueError(
                    "Illegal move direction for DiffType.MOVED: "
                    + str(node.to_or_from)
                )
        else:
            color, marker = marker_map.get(record.diff_type, ("", ""))

        return f"{color}{marker}{self.term_control.NORMAL}"

    def render(
        self, node: Optional[TreeNode] = None, prefix: str = "", is_last: bool = True
    ) -> Optional[str]:
        """
        Recursively render tree with proper box-drawing characters.

        :param node: The node to begin rendering from.
        :type node: ``Optional[TreeNode]``
        :param prefix: The prefix sting for this node.
        :type prefix: ``str``
        :param is_last: ``True`` if this node is the last child of its parent.
        :type is_last: ``bool``
        """
        node = node or self.root
        if node is None:
            raise ValueError("Render node is undefined")

        if self._rendered is None and node == self.root:
            self._rendered = ""
        elif self._rendered is not None and node == self.root:
            raise RuntimeError("Cannot restart rendering while render in progress")

        # Print current node
        marker = self.get_change_marker(node.record, node)  # [+], [-], [*], etc.
        connector = "└── " if is_last else "├── "
        self._rendered += f"{prefix}{connector}{marker} {node.name}" + "\n"

        # Calculate prefix for children
        extension = "    " if is_last else "│   "
        child_prefix = prefix + extension

        # Recurse to children
        children = sorted(node.children.values(), key=lambda n: n.name)
        for i, child in enumerate(children):
            child_is_last = i == len(children) - 1
            self.render(node=child, prefix=child_prefix, is_last=child_is_last)

        if node == self.root:
            rendered = self._rendered
            self._rendered = None
            return rendered.rstrip()
        return None

    @staticmethod
    def build_tree(
        results: "FsDiffResults", term_control: Optional[TermControl] = None
    ) -> "DiffTree":
        """
        Build a new ``DiffTree` object from a ``"FsDiffResults"`` instance
        containing file system difference records.

        :param results: The file system diff results for this ``DiffTree`.
        :type results: ``"FsDiffResults"``
        :param term_control: An optional ``TermControl`` instance to use for formatting.
        :type term_control: ``Optional[TermControl]``
        :returns: A new ``DiffTree`` instance reflecting ``results``.
        :rtype: ``DiffTree``
        """
        root = TreeNode("/")
        for record in results:
            parts = record.path.strip("/").split("/")
            node = root
            for part in parts[:-1]:
                if part not in node.children:
                    node.children[part] = TreeNode(part)
                node = node.children[part]
            # Leaf node with actual record
            if record.diff_type != DiffType.MOVED:
                node.children[parts[-1]] = TreeNode(parts[-1], record)
            else:
                # Create two nodes for a move
                moved_from = record.moved_from.strip("/").split("/")[-1]
                moved_to = record.moved_to.strip("/").split("/")[-1]
                node.children[moved_from] = TreeNode(moved_from, record, to_or_from="from")
                node.children[moved_to] = TreeNode(moved_to, record, to_or_from="to")

        return DiffTree(root, term_control=term_control)


__all__ = [
    "DiffTree",
]
