# Copyright Red Hat
#
# tests/fsdiff/test_contentdiff.py - Content-aware differ tests.
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import patch, mock_open, MagicMock
import tempfile
import json
import os
from pathlib import Path

from snapm.fsdiff.contentdiff import (
    ContentDiff,
    ContentDifferManager,
    TextContentDiffer,
)
from snapm.fsdiff.filetypes import FileTypeInfo, FileTypeCategory

from ._util import make_entry

class TestContentDiff(unittest.TestCase):
    def setUp(self):
        self.manager = ContentDifferManager()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

    def _create_file(self, name, content, binary=False):
        path = Path(self.tmp_dir.name) / name
        mode = "wb" if binary else "w"
        encoding = None if binary else "utf-8"
        with open(path, mode, encoding=encoding) as f:
            f.write(content)
        return path

    def test_ContentDiff__str__(self):
        # Case 1: with diff data
        cd = ContentDiff("unified", old_content="abc", new_content="def")
        cd.diff_data = ["-abc", "+def"]
        self.assertIn("diff_type: unified", str(cd))
        self.assertIn("diff_data: <2 items>", str(cd))

        # Case 2: no data
        cd2 = ContentDiff("binary")
        self.assertIn("diff_data: <none>", str(cd2))

    def test_text_diff(self):
        old_path = self._create_file("old.txt", "line1\nline2\n")
        new_path = self._create_file("new.txt", "line1\nline2 modified\n")

        old_entry = make_entry(str(old_path))
        new_entry = make_entry(str(new_path))

        diff = self.manager.generate_content_diff(old_path, new_path, old_entry, new_entry)

        self.assertIsNotNone(diff)
        self.assertEqual(diff.diff_type, "unified")
        self.assertTrue(diff.has_changes)
        # Check that the diff output contains the modified line
        diff_text = "".join(diff.diff_data)
        self.assertIn("+line2 modified", diff_text)
        self.assertIn("-line2", diff_text)

    def test_json_diff_structure(self):
        # Valid JSON comparison
        old_json = {"a": 1, "b": 2}
        new_json = {"a": 1, "b": 3, "c": 4}

        old_path = self._create_file("old.json", json.dumps(old_json))
        new_path = self._create_file("new.json", json.dumps(new_json))

        # Force mimetype to ensure JsonContentDiffer is selected
        info = FileTypeInfo("application/json", "JSON", FileTypeCategory.CONFIG)
        old_entry = make_entry(str(old_path), mime_type="application/json")
        new_entry = make_entry(str(new_path), mime_type="application/json")
        old_entry.file_type_info = info
        new_entry.file_type_info = info

        diff = self.manager.generate_content_diff(old_path, new_path, old_entry, new_entry)

        self.assertEqual(diff.diff_type, "json")
        # Ensure pretty printing occurred (multi-line)
        self.assertGreater(len(diff.diff_data), 1)

    def test_json_fallback_on_invalid_syntax(self):
        # "bad.json" isn't valid JSON, but extension suggests it.
        # Should fall back to text diff.
        old_path = self._create_file("old.json", "{ invalid_json }")
        new_path = self._create_file("new.json", "{ invalid_json_changed }")

        info = FileTypeInfo("application/json", "JSON", FileTypeCategory.CONFIG)
        old_entry = make_entry(str(old_path))
        new_entry = make_entry(str(new_path))
        new_entry.file_type_info = info

        diff = self.manager.generate_content_diff(old_path, new_path, old_entry, new_entry)

        # It attempts JSON, fails, falls back to text (which returns 'unified')
        self.assertEqual(diff.diff_type, "unified")
        self.assertTrue(diff.has_changes)

    def test_binary_diff(self):
        old_path = self._create_file("old.bin", b"\x00\x01", binary=True)
        new_path = self._create_file("new.bin", b"\x00\x02", binary=True)

        info = FileTypeInfo("application/octet-stream", "Data", FileTypeCategory.BINARY)
        old_entry = make_entry(str(old_path), content_hash="h1", mime_type="application/octet-stream")
        new_entry = make_entry(str(new_path), content_hash="h2", mime_type="application/octet-stream")
        new_entry.file_type_info = info

        diff = self.manager.generate_content_diff(old_path, new_path, old_entry, new_entry)

        self.assertEqual(diff.diff_type, "binary")
        self.assertTrue(diff.has_changes)
        self.assertIn("changed", diff.summary)

    def test_text_diff_read_error(self):
        old_entry = make_entry("/old.txt")
        new_entry = make_entry("/new.txt")

        with patch("pathlib.Path.exists", autospec=True) as mock_exists:
            mock_exists.return_value = True
            # Mock open to raise OSError
            with patch("builtins.open", side_effect=OSError("Read error")):
                diff = self.manager.generate_content_diff(
                    Path("/old.txt"), Path("/new.txt"), old_entry, new_entry
                )

        self.assertFalse(diff.has_changes)
        self.assertIn("Error reading text", diff.summary)
        self.assertIn("Read error", diff.error_message)

    def test_json_diff_read_error_fallback(self):
        info = FileTypeInfo("application/json", "JSON", FileTypeCategory.CONFIG)
        old_entry = make_entry("/old.json")
        new_entry = make_entry("/new.json")
        new_entry.file_type_info = info

        with patch("pathlib.Path.exists", autospec=True) as mock_exists:
            # Mock json.load to raise ValueError (simulating corrupt JSON)
            # We need to mock open as well to return something valid-ish for the fallback text read
            with patch("json.load", side_effect=ValueError("Bad JSON")):
                with patch("builtins.open", mock_open(read_data="bad data")):
                    diff = self.manager.generate_content_diff(
                        Path("/old.json"), Path("/new.json"), old_entry, new_entry
                    )

        self.assertEqual(diff.diff_type, "unified")

    def test_text_diff_encodings(self):
        """Cover encoding logic in TextContentDiffer."""
        # snapm.fsdiff/contentdiff.py: encoding branches
        differ = TextContentDiffer()
        old_entry = make_entry("/a.txt")
        new_entry = make_entry("/b.txt")

        # Case 1: No FileTypeInfo -> defaults to utf-8
        old_entry.file_type_info = None
        new_entry.file_type_info = None

        path_a = Path("/a")
        path_b = Path("/b")

        with patch("pathlib.Path.exists", autospec=True) as mock_exists:
            mock_exists.return_value = True
            with patch("builtins.open", mock_open(read_data="content")):
                diff = differ.generate_diff(path_a, path_b, old_entry, new_entry)
                self.assertFalse(diff.has_changes)

        # Case 2: Explicit encoding in FileTypeInfo
        info = FileTypeInfo("text/plain", "Text", FileTypeCategory.TEXT, encoding="latin-1")
        new_entry.file_type_info = info

        with patch("pathlib.Path.exists", autospec=True) as mock_exists:
            mock_exists.return_value = True
            with patch("builtins.open", mock_open(read_data="content")) as m:
                diff2 = differ.generate_diff(Path("/a"), Path("/b"), old_entry, new_entry)
                # Verify open was called with encoding
                m.assert_called_with(Path("/b"), "r", encoding="latin-1", errors="replace")

        # Verify the diff result
        self.assertFalse(diff2.has_changes)  # Same content
