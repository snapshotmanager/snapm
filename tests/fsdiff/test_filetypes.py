# Copyright Red Hat
#
# tests/fsdiff/test_filetypes.py - Magic based file type detection tests.
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import magic

from snapm.fsdiff.filetypes import FileTypeDetector, FileTypeCategory, FileTypeInfo

class TestFileTypeDetector(unittest.TestCase):
    def setUp(self):
        self.detector = FileTypeDetector()

    def test_FileTypeInfo(self):
        fti = FileTypeInfo("text/plain", "ASCII text", FileTypeCategory.TEXT, "utf-8")
        self.assertIn("MIME type: text/plain", str(fti))
        self.assertIn("Category: text", str(fti))

    @patch("snapm.fsdiff.filetypes.magic.detect_from_filename")
    def test_categorize_log_file(self, mock_magic):
        # magic might say plain text, but filename says .log
        mock_res = MagicMock()
        mock_res.mime_type = "text/plain"
        mock_res.name = "ASCII text"
        mock_res.encoding = "us-ascii"
        mock_magic.return_value = mock_res

        path = Path("/var/log/syslog")
        info = self.detector.detect_file_type(path)

        self.assertEqual(info.category, FileTypeCategory.LOG)

    @patch("snapm.fsdiff.filetypes.magic.detect_from_filename")
    def test_categorize_json_config(self, mock_magic):
        mock_res = MagicMock()
        mock_res.mime_type = "application/json"
        mock_res.name = "JSON data"
        mock_res.encoding = "us-ascii"
        mock_magic.return_value = mock_res

        path = Path("/etc/app/config.json")
        info = self.detector.detect_file_type(path)

        self.assertEqual(info.category, FileTypeCategory.CONFIG)

    def test_categorize_path_based_overrides(self):
        # Test logic in _categorize_file without mocking magic entirely
        # (unit testing the helper method directly if possible, or via public API)

        # .conf file -> CONFIG
        cat = self.detector._categorize_file("text/plain", Path("foo.conf"))
        self.assertEqual(cat, FileTypeCategory.CONFIG)

        # /etc/ file -> CONFIG
        cat = self.detector._categorize_file("text/plain", Path("/etc/passwd"))
        self.assertEqual(cat, FileTypeCategory.CONFIG)

        # .db file -> DATABASE
        cat = self.detector._categorize_file("application/octet-stream", Path("test.db"))
        self.assertEqual(cat, FileTypeCategory.DATABASE)

    # c9s magic does not have magic.error
    @unittest.skipIf(not hasattr(magic, "error"), "magic does not have magic.error")
    @patch("snapm.fsdiff.filetypes.magic.detect_from_filename")
    def test_detect_file_type_error(self, mock_magic):
        # Simulate magic library error
        mock_magic.side_effect = magic.error("magic failed")

        path = Path("/broken")
        info = self.detector.detect_file_type(path)

        self.assertEqual(info.category, FileTypeCategory.UNKNOWN)
        self.assertEqual(info.mime_type, "application/octet-stream")

    def test_categorize_fallback(self):
        # Force a mimetype that doesn't match any rules
        cat = self.detector._categorize_file("application/x-strange-thing", Path("file.xyz"))
        self.assertEqual(cat, FileTypeCategory.BINARY)
