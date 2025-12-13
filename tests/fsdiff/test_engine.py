# Copyright Red Hat
#
# tests/fsdiff/test_engine.py - Difference engine core tests.
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import MagicMock

from snapm.fsdiff.changes import ChangeType, FileChange
from snapm.fsdiff.contentdiff import ContentDiff
from snapm.fsdiff.engine import DiffEngine, DiffType, FsDiffRecord
from snapm.fsdiff.options import DiffOptions

from ._util import make_entry

class TestDiffEngine(unittest.TestCase):
    def setUp(self):
        self.engine = DiffEngine()
        self.opts = DiffOptions()

    def test_FsDiffRecord__str__(self):
        entry_a = make_entry("/a")
        entry_b = make_entry("/b")
        rec = FsDiffRecord("/a", DiffType.MODIFIED, entry_a, entry_b)
        rec.set_content_diff(ContentDiff("unified"))
        s = str(rec)
        self.assertIn("diff_type: modified", s)
        self.assertIn("metadata_changed:", s)
        self.assertNotIn("content_diff_summary:", s)

    def test_simple_add_remove(self):
        # Tree A: /file1
        # Tree B: /file2
        tree_a = {"/file1": make_entry("/file1", content_hash="h1")}
        tree_b = {"/file2": make_entry("/file2", content_hash="h2")}

        diffs = self.engine.compute_diff(tree_a, tree_b, self.opts)

        # Expect 1 Removed (/file1), 1 Added (/file2)
        self.assertEqual(len(diffs), 2)
        types = sorted([d.diff_type for d in diffs], key=lambda x: x.value)
        self.assertEqual(types, [DiffType.ADDED, DiffType.REMOVED])

    def test_detect_rename(self):
        # A file moves from /old to /new with same hash
        common_hash = "abc123hash"
        tree_a = {"/old": make_entry("/old", content_hash=common_hash)}
        tree_b = {"/new": make_entry("/new", content_hash=common_hash)}

        diffs = self.engine.compute_diff(tree_a, tree_b, self.opts)

        self.assertEqual(len(diffs), 1)
        rec = diffs[0]
        self.assertEqual(rec.diff_type, DiffType.MOVED)
        self.assertEqual(rec.moved_from, "/old")
        self.assertEqual(rec.moved_to, "/new")

    def test_detect_dir_move(self):
        # Directory /old is moved to /new with unchanged content.
        tree_a = {
            "/old": make_entry("/old", is_dir=True),
            "/old/a": make_entry("/old/a", content_hash="Ha"),
            "/old/b": make_entry("/old/b", content_hash="Hb"),
            "/old/c": make_entry("/old/c", content_hash="Hc"),
        }
        tree_b = {
            "/new": make_entry("/new", is_dir=True),
            "/new/a": make_entry("/new/a", content_hash="Ha"),
            "/new/b": make_entry("/new/b", content_hash="Hb"),
            "/new/c": make_entry("/new/c", content_hash="Hc"),
        }

        diffs = self.engine.compute_diff(tree_a, tree_b, self.opts)

        for diff in diffs:
            if diff.path == "/old":
                self.assertEqual(diff.diff_type, DiffType.REMOVED)
            elif diff.path == "/new":
                self.assertEqual(diff.diff_type, DiffType.ADDED)
            else:
                self.assertEqual(diff.diff_type, DiffType.MOVED)

        # Expect: 1 REMOVED dir, 1 ADDED dir, 3 MOVED files = 5 diffs
        self.assertEqual(len(diffs), 5)

    def test_copy_is_not_move(self):
        # Copy: /orig exists in A. /orig AND /copy exist in B.
        h = "hashX"
        tree_a = {
            "/orig": make_entry("/orig", content_hash=h)
        }
        tree_b = {
            "/orig": make_entry("/orig", content_hash=h),
            "/copy": make_entry("/copy", content_hash=h)
        }

        diffs = self.engine.compute_diff(tree_a, tree_b, self.opts)

        # Should be just ADDED /copy. /orig is unchanged.
        # It should NOT be detected as a move because /orig was not removed.
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].diff_type, DiffType.ADDED)
        self.assertEqual(diffs[0].path, "/copy")

    def test_type_change(self):
        # /path is a file in A, directory in B
        tree_a = {"/path": make_entry("/path", is_dir=False, content_hash="h1")}
        tree_b = {"/path": make_entry("/path", is_dir=True)}

        diffs = self.engine.compute_diff(tree_a, tree_b, self.opts)

        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].diff_type, DiffType.TYPE_CHANGED)

    def test_swap_edge_case(self):
        # File A moves to B, File B moves to A. (Hard case for move detectors)
        # /f1 (h1) -> /f2 (h1)
        # /f2 (h2) -> /f1 (h2)
        tree_a = {
            "/f1": make_entry("/f1", content_hash="h1"),
            "/f2": make_entry("/f2", content_hash="h2")
        }
        tree_b = {
            "/f1": make_entry("/f1", content_hash="h2"),
            "/f2": make_entry("/f2", content_hash="h1")
        }

        # Generate diffs for test trees
        diffs = self.engine.compute_diff(tree_a, tree_b, self.opts)

        # Ideally, this is 2 changes.
        # If move detection is perfect, it sees two moves.
        # If strict content hash mapping is used, it should work.
        for diff in diffs:
            self.assertNotEqual(diff.diff_type, DiffType.ADDED)
            self.assertNotEqual(diff.diff_type, DiffType.REMOVED)
            self.assertIn(
                diff.diff_type,
                (DiffType.MOVED, DiffType.MODIFIED)
            )

        # Two MOVED, two MODIFIED
        self.assertEqual(len(diffs), 4)

        moves = [d for d in diffs if d.diff_type == DiffType.MOVED]
        self.assertEqual(len(moves), 2)

    def test_move_detection_edge_cases(self):
        """
        Cover specific branches in _detect_moves in engine.py.

        Case 1: File modified in place (Same path, content hash matches a
        file in B)

        This covers "if dest_path == path: continue" in _detect_moves.

        We need a file that is MODIFIED (so it's in changed_paths):
        If /a(h1) -> /a(h1) (metadata change), it is in changed_paths.
        The move detector sees /a(h1) in A, looks up h1 in B, finds /a.
        dest_path (/a) == path (/a), so it continues (NOT a move).

        Case 2: File Removed, but no candidate in B
        This covers "if not candidates: continue"
        """
        entry_a = make_entry("/a", content_hash="h1", mode=0o644)
        entry_b = make_entry("/a", content_hash="h1", mode=0o755)
        tree_a = {"/a": entry_a}
        tree_b = {"/a": entry_b}

        diffs = self.engine.compute_diff(tree_a, tree_b, self.opts)
        # Verify it remains a MODIFIED record, not MOVED
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].diff_type, DiffType.MODIFIED)

        entry_a2 = make_entry("/rm", content_hash="h_deleted")
        tree_a2 = {"/rm": entry_a2}
        tree_b2 = {} # Empty

        diffs2 = self.engine.compute_diff(tree_a2, tree_b2, self.opts)
        self.assertEqual(len(diffs2), 1)
        self.assertEqual(diffs2[0].diff_type, DiffType.REMOVED)

    def test_max_content_diff_size_limit(self):
        self.opts.max_content_diff_size = 100
        # Create entries larger than limit
        entry_a = make_entry("/a", size=200, content_hash="h1")
        entry_b = make_entry("/a", size=200, content_hash="h2")
        tree_a = {"/a": entry_a}
        tree_b = {"/a": entry_b}

        diffs = self.engine.compute_diff(tree_a, tree_b, self.opts)

        self.assertEqual(len(diffs), 1)
        # Should have detected modification
        self.assertEqual(diffs[0].diff_type, DiffType.MODIFIED)
        # But content diff should be None because of size limit
        self.assertFalse(diffs[0].has_content_diff)

    def test_engine_content_diff_limits(self):
        """Cover within_limit logic branch in engine.py."""
        engine = DiffEngine()
        # Set max size to 0 (disabled) -> Should generate diff
        opts = DiffOptions(max_content_diff_size=0)

        entry_a = make_entry("/a", content_hash="h1", size=1000)
        entry_b = make_entry("/a", content_hash="h2", size=2000)
        tree_a = {"/a": entry_a}
        tree_b = {"/a": entry_b}

        # We need to mock content_differ to verify it was called
        # Mock with a valid ContentDiff return value
        mock_diff = ContentDiff("unified", summary="test diff")
        engine.content_differ = MagicMock()
        engine.content_differ.generate_content_diff.return_value = mock_diff

        diffs = engine.compute_diff(tree_a, tree_b, opts)
        engine.content_differ.generate_content_diff.assert_called()
        # Verify the diff was actually set
        self.assertTrue(diffs[0].has_content_diff)
        self.assertIsNotNone(diffs[0].content_diff)

    def test_change_summary_generation(self):
        # Cover get_change_summary for different types
        # 1. Added
        rec = FsDiffRecord("/new", DiffType.ADDED, new_entry=make_entry("/new"))
        self.assertIn("Added text/plain", rec.get_change_summary())

        # 2. Type Changed
        rec = FsDiffRecord("/change", DiffType.TYPE_CHANGED, 
                          make_entry("/change", is_dir=True), 
                          make_entry("/change", is_dir=False))
        self.assertIn("directory to file", rec.get_change_summary())

        # 3. Modified (Metadata only)
        rec = FsDiffRecord("/mod", DiffType.MODIFIED, make_entry("/mod"), make_entry("/mod"))
        rec.add_change(FileChange(ChangeType.PERMISSIONS))
        self.assertIn("Changed: permissions", rec.get_change_summary())

        # 4. Moved
        rec = FsDiffRecord("/a", DiffType.MOVED, make_entry("/a"), make_entry("/b"))
        rec.moved_from = "/a"
        rec.moved_to = "/b"
        self.assertIn("Moved from /a to /b", rec.get_change_summary())
