# Copyright Red Hat
#
# tests/fsdiff/test_engine.py - Difference engine core tests.
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime
from typing import TextIO
from math import floor
import json

from snapm.fsdiff.changes import ChangeType, FileChange
from snapm.fsdiff.contentdiff import ContentDiff
from snapm.fsdiff.engine import FsDiffResults, DiffEngine, DiffType, FsDiffRecord
from snapm.fsdiff.options import DiffOptions
from snapm.progress import TermControl

from ._util import make_entry

timestamp = floor(datetime.now().timestamp())


class TestFsDiffResults(unittest.TestCase):
    def setUp(self):
        self.entry_a = make_entry("/a", content_hash="h1", size=100)
        self.entry_b = make_entry("/a", content_hash="h2", size=200)

        # Modified record with content diff
        self.rec_mod = FsDiffRecord("/a", DiffType.MODIFIED, self.entry_a, self.entry_b)
        self.rec_mod.set_content_diff(ContentDiff("unified", summary="diff", old_content="foo", new_content="bar"))
        self.rec_mod.content_diff.diff_data = ["header", "header", "-foo", "+bar"]

        # Added record
        self.rec_add = FsDiffRecord("/b", DiffType.ADDED, new_entry=self.entry_b)

        # Removed record
        self.rec_rm = FsDiffRecord("/c", DiffType.REMOVED, old_entry=self.entry_a)

        self.results = FsDiffResults([self.rec_mod, self.rec_add, self.rec_rm], DiffOptions(), timestamp)

    def test_list_interface(self):
        """Test iteration, len, and getitem."""
        self.assertEqual(len(self.results), 3)
        self.assertEqual(self.results[0], self.rec_mod)
        self.assertEqual(list(self.results), [self.rec_mod, self.rec_add, self.rec_rm])

    def test_summary_properties(self):
        """Test aggregation properties."""
        self.assertEqual(self.results.total_changes, 3)
        # Only rec_mod has a content diff set in setUp
        self.assertEqual(self.results.content_changes, 1)

        self.assertEqual(len(self.results.added), 1)
        self.assertEqual(self.results.added[0].path, "/b")

        self.assertEqual(len(self.results.removed), 1)
        self.assertEqual(self.results.removed[0].path, "/c")

        self.assertEqual(len(self.results.modified), 1)
        self.assertEqual(self.results.modified[0].path, "/a")

        self.assertEqual(len(self.results.moved), 0)

    def test_output_formats(self):
        """Test short, full, paths, and json output formats."""
        # Paths
        self.assertEqual(self.results.paths(), ["/a", "/b", "/c"])

        # Short
        short = self.results.short()
        self.assertIn("Path: /a", short)
        self.assertIn("diff_type: modified", short)
        self.assertIn("content_diff_summary: diff", short)

        # Full
        full = self.results.full()
        self.assertIn("content_diff_summary: diff", full)

        # JSON
        json_out = self.results.json()
        data = json.loads(json_out)
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0]["path"], "/a")
        self.assertEqual(data[0]["diff_type"], "modified")

    def test_diff_rendering(self):
        """Test unified diff generation."""
        # Standard diff
        diff = self.results.diff(color="never")
        self.assertIn("diff a/a b/a", diff)
        self.assertIn("--- a/a", diff)
        self.assertIn("+++ b/a", diff)
        self.assertIn("-foo", diff)
        self.assertIn("+bar", diff)

        # Added file diff (simulated content diff on added record)
        self.rec_add.set_content_diff(ContentDiff("unified"))
        self.rec_add.content_diff.diff_data = ["h", "h", "+new"]

        diff_add = self.results.diff(color="never")
        self.assertIn("new file mode", diff_add)
        self.assertIn("--- /dev/null", diff_add)

        # Deleted file diff
        self.rec_rm.set_content_diff(ContentDiff("unified"))
        self.rec_rm.content_diff.diff_data = ["h", "h", "-old"]

        diff_rm = self.results.diff(color="never")
        self.assertIn("deleted file mode", diff_rm)
        self.assertIn("+++ /dev/null", diff_rm)

    def test_diff_rendering_color(self):
        """Test color codes in diff output."""
        term_control = MagicMock(spec=TermControl)
        term_control.term_stream = MagicMock(spec=TextIO)
        term_control.term_stream.encoding = "utf8"
        term_control.columns = 80

        term_control.BLACK = "<BLACK>"  #: Black foreground color
        term_control.BLUE = "<BLUE>"  #: Blue foreground color
        term_control.GREEN = "<GREEN>"  #: Green foreground color
        term_control.CYAN = "<CYAN>"  #: Cyan foreground color
        term_control.RED = "<RED>"  #: Red foreground color
        term_control.MAGENTA = "<MAGENTA>"  #: Magenta foreground color
        term_control.YELLOW = "<YELLOW>"  #: Yellow foreground color
        term_control.WHITE = "<WHITE>"  #: White foreground color
        term_control.NORMAL = "<NORMAL>"  #: Reset all properties

        diff = self.results.diff(term_control=term_control)

        # Check for ANSI escape code (ESC[)
        self.assertIn("<RED>", diff)
        self.assertIn("<GREEN>", diff)
        self.assertIn("<WHITE>", diff)

    def test_diff_stat_logic(self):
        """Test the render_diff_stat function directly for formatting/counting."""
        from snapm.fsdiff.engine import render_diff_stat

        # Create records with specific content diff data
        # Record 1: Short path, 1 insertion, 1 deletion
        rec1 = FsDiffRecord("/short", DiffType.MODIFIED)
        rec1.set_content_diff(ContentDiff("unified"))
        rec1.content_diff.diff_data = ["---", "+++", "-old", "+new"]

        # Record 2: Long path, 2 insertions
        rec2 = FsDiffRecord("/longer/path/file", DiffType.MODIFIED)
        rec2.set_content_diff(ContentDiff("unified"))
        rec2.content_diff.diff_data = ["---", "+++", "+line1", "+line2"]

        # Mock TermControl for predictable plain text output
        tc = MagicMock(spec=TermControl)
        tc.GREEN = ""
        tc.RED = ""
        tc.NORMAL = ""

        # Render
        diffstat = render_diff_stat([rec1, rec2], tc)

        # Check alignment (longest path dictates column width)
        # /short should be padded to match /longer/path/file
        # Note: render_diff_stat lstrips os.sep, so "/short" -> "short"
        self.assertIn(" short            |    2", diffstat)
        self.assertIn(" longer/path/file |    2", diffstat)

        # Check visual graph characters (plain text versions)
        self.assertIn("+", diffstat)
        self.assertIn("-", diffstat)

        # Check trailer summary
        self.assertIn("2 files changed, 3 insertions(+), 1 deletions(-)", diffstat)

    def test_results_summary_basic(self):
        """Test FsDiffResults.summary() without diffstat."""
        # Using self.results from setUp (3 records: 1 mod, 1 add, 1 rm)
        summary = self.results.summary(color="never")

        self.assertIn("Total changes:     3", summary)
        # Check category counts
        self.assertIn("added:     1", summary)
        self.assertIn("removed:   1", summary)
        self.assertIn("modified:  1", summary)
        # Only self.rec_mod has a content diff set in setUp
        self.assertIn("withdiff:  1", summary)

        # Ensure diffstat is NOT present
        self.assertNotIn("|", summary)
        self.assertNotIn("insertions(+)", summary)

    def test_results_summary_with_diffstat(self):
        """Test FsDiffResults.summary() including diffstat."""
        summary = self.results.summary(diffstat=True, color="never")

        self.assertIn("Total changes:     3", summary)
        # Check for diffstat presence (rec_mod path is /a)
        self.assertIn(" a |    2", summary)
        self.assertIn("1 file changed", summary)

    def test_diff_with_diffstat(self):
        """Test FsDiffResults.diff() with diffstat enabled."""
        diff_output = self.results.diff(diffstat=True, color="never")

        # Should contain the stat header
        self.assertIn(" a |    2", diff_output)
        self.assertIn("1 file changed", diff_output)

        # Should also contain the actual diff content
        self.assertIn("diff a/a b/a", diff_output)
        self.assertIn("-foo", diff_output)
        self.assertIn("+bar", diff_output)

    def test_summary_color_codes(self):
        """Test that summary applies color codes when requested."""
        term_control = MagicMock(spec=TermControl)
        term_control.term_stream = MagicMock(spec=TextIO)
        term_control.term_stream.encoding = "utf8"
        term_control.columns = 80

        term_control.BLACK = "<BLACK>"  #: Black foreground color
        term_control.BLUE = "<BLUE>"  #: Blue foreground color
        term_control.GREEN = "<GREEN>"  #: Green foreground color
        term_control.CYAN = "<CYAN>"  #: Cyan foreground color
        term_control.RED = "<RED>"  #: Red foreground color
        term_control.MAGENTA = "<MAGENTA>"  #: Magenta foreground color
        term_control.YELLOW = "<YELLOW>"  #: Yellow foreground color
        term_control.WHITE = "<WHITE>"  #: White foreground color
        term_control.NORMAL = "<NORMAL>"  #: Reset all properties

        summary = self.results.summary(term_control=term_control)

        # Check for ANSI Escape (Start of color)
        self.assertIn("<GREEN>", summary)
        self.assertIn("<RED>", summary)
        self.assertIn("<NORMAL>", summary)


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

    def test_compute_diff_limits_added_removed(self):
        opts = DiffOptions(include_content_diffs=True, max_content_diff_size=10)
        entry_lg = make_entry("/large", size=100)
        tree_a = {}
        tree_b = {"/large": entry_lg}
        diffs = self.engine.compute_diff(tree_a, tree_b, opts)
        self.assertFalse(diffs[0].has_content_diff)

    def test_detect_moves_collision(self):
        entry_src = make_entry("/src", content_hash="h1")
        entry_dst1 = make_entry("/dst1", content_hash="h1")
        entry_dst2 = make_entry("/dst2", content_hash="h1")
        tree_a = {"/src": entry_src}
        tree_b = {"/dst1": entry_dst1, "/dst2": entry_dst2}
        diffs = self.engine.compute_diff(tree_a, tree_b, self.opts)
        moves = [d for d in diffs if d.diff_type == DiffType.MOVED]
        self.assertEqual(len(moves), 1)

    def test_include_content_diffs_false(self):
        opts = DiffOptions(include_content_diffs=False)
        entry_a = make_entry("/a", content_hash="h1")
        entry_b = make_entry("/a", content_hash="h2")
        diffs = self.engine.compute_diff({"/a": entry_a}, {"/a": entry_b}, opts)
        self.assertEqual(diffs[0].diff_type, DiffType.MODIFIED)
        self.assertFalse(diffs[0].has_content_diff)

    def test_interrupt_compute_diff(self):
        entry_a = make_entry("/a", content_hash="h1")
        entry_b = make_entry("/a", content_hash="h2")
        with patch("snapm.fsdiff.engine.ProgressFactory.get_progress") as mock_prog:
            mock_prog.return_value.start.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                self.engine.compute_diff({"/a": entry_a}, {"/a": entry_b}, self.opts)

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

        We need a file that is MODIFIED (so it's in modified_paths):
        If /a(h1) -> /a(h1) (metadata change), it is in modified_paths.
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
        opts = DiffOptions(max_content_diff_size=100)
        # Create entries larger than limit
        entry_a = make_entry("/a", size=200, content_hash="h1")
        entry_b = make_entry("/a", size=200, content_hash="h2")
        tree_a = {"/a": entry_a}
        tree_b = {"/a": entry_b}

        diffs = self.engine.compute_diff(tree_a, tree_b, opts)

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

    def test_FsDiffRecord_serialization(self):
        """Test to_dict and json methods of FsDiffRecord."""
        entry_a = make_entry("/a")
        entry_b = make_entry("/b")
        rec = FsDiffRecord("/a", DiffType.MODIFIED, entry_a, entry_b)
        rec.add_change(FileChange(ChangeType.CONTENT, "h1", "h2"))
        rec.moved_from = "/old_a"

        data = rec.to_dict()
        self.assertEqual(data["path"], "/a")
        self.assertEqual(data["diff_type"], "modified")
        self.assertEqual(data["changes"][0]["change_type"], "content")
        self.assertEqual(data["moved_from"], "/old_a")

        json_str = rec.json()
        self.assertIn('"diff_type": "modified"', json_str)

    def test_compute_diff_added_removed_details(self):
        """Verify that detect_added/removed logic populates changes correctly."""
        # Tree B has new file
        tree_a = {}
        tree_b = {"/new": make_entry("/new", content_hash="h1", mode=0o755)}

        diffs = self.engine.compute_diff(tree_a, tree_b, self.opts)
        rec = diffs[0]

        self.assertEqual(rec.diff_type, DiffType.ADDED)
        # Should have changes from detect_added (content, permissions, etc)
        change_types = [c.change_type for c in rec.changes]
        self.assertIn(ChangeType.CONTENT, change_types)
        self.assertIn(ChangeType.PERMISSIONS, change_types)


    def test_render_unified_diff_modes(self):
        """Test rendering of new/deleted file modes."""
        from snapm.fsdiff.engine import render_unified_diff

        # Added file
        rec_add = FsDiffRecord("/new", DiffType.ADDED, new_entry=make_entry("/new", mode=0o755))
        rec_add.set_content_diff(ContentDiff("unified"))
        rec_add.content_diff.diff_data = ["header", "header", "+content"]

        diff = render_unified_diff(rec_add, None)
        self.assertIn("new file mode 0o100755", diff)
        self.assertIn("--- /dev/null", diff)

        # Deleted file
        rec_rm = FsDiffRecord("/old", DiffType.REMOVED, old_entry=make_entry("/old", mode=0o644))
        rec_rm.set_content_diff(ContentDiff("unified"))
        rec_rm.content_diff.diff_data = ["header", "header", "-content"]

        diff = render_unified_diff(rec_rm, None)
        self.assertIn("deleted file mode 0o100644", diff)
        self.assertIn("+++ /dev/null", diff)

    def test_FsDiffRecord_str_full(self):
        """Test __str__ with all optional fields populated."""
        entry = make_entry("/path")
        rec = FsDiffRecord("/path", DiffType.MOVED, entry, entry)
        rec.moved_from = "/old"
        rec.moved_to = "/path"
        rec.content_diff_summary = "diff summary"

        s = str(rec)
        self.assertIn("moved_from: /old", s)
        self.assertIn("moved_to: /path", s)
        self.assertIn("content_diff_summary: diff summary", s)
