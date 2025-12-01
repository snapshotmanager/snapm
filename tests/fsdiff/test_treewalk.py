# Copyright Red Hat
#
# tests/fsdiff/test_treewalk.py - FsEntry tests.
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
import stat

from snapm._fsdiff.filetypes import FileTypeInfo, FileTypeCategory
from snapm._fsdiff.treewalk import FsEntry, TreeWalker

from ._util import make_entry

class TestTreeWalker(unittest.TestCase):
    def test_TreeWalker(self):
        """Test TreeWalker.__init__()"""
        walker = TreeWalker()
        self.assertEqual(walker.hash_algorithm, "sha256")
        self.assertIn("/proc/*", walker.exclude_patterns)


class TestFsEntry(unittest.TestCase):
    def test_FsEntry__str__(self):
        """Cover FsEntry.__str__ and xattrs"""
        entry = make_entry("/foo")
        entry.xattrs = {"user.comment": "test"}
        entry.symlink_target = "/target"
        s = str(entry)
        self.assertIn("extended_attributes", s)
        self.assertIn("user.comment=test", s)
        self.assertIn("symlink_target", s)

    def test_special_file_block(self):
        """Test FsEntry properties for special file types (block)."""
        # Block Device
        entry = make_entry("/dev/sda", mode=stat.S_IFBLK | 0o660)
        self.assertTrue(entry.is_block)
        self.assertEqual(entry.type_desc, "block device")
        
    def test_special_file_char(self):
        """Test FsEntry properties for special file types (char)."""
        # Char Device
        entry = make_entry("/dev/null", mode=stat.S_IFCHR | 0o666)
        self.assertTrue(entry.is_char)
        self.assertEqual(entry.type_desc, "char device")
        
    def test_special_file_pipe(self):
        """Test FsEntry properties for special file types (pipe)."""
        # FIFO (Named Pipe)
        entry = make_entry("/tmp/pipe", mode=stat.S_IFIFO | 0o600)
        self.assertTrue(entry.is_fifo)
        self.assertEqual(entry.type_desc, "FIFO")
        
    def test_special_file_sock(self):
        """Test FsEntry properties for special file types (sock)."""
        # Socket
        entry = make_entry("/tmp/sock", mode=stat.S_IFSOCK | 0o600)
        self.assertTrue(entry.is_sock)
        self.assertEqual(entry.type_desc, "socket")
        
    def test_special_file_unknown(self):
        """Test FsEntry properties for special file types (unknown)."""
        # Fallback "other" (unlikely in standard POSIX, but good for logic coverage)
        # Creating an entry with 0 mode (no type bits)
        entry = make_entry("/unknown", mode=0)
        # Reset mode to 0 explicitly as make_entry might default it
        entry.mode = 0
        self.assertEqual(entry.type_desc, "other")

    def test_is_text_like(self):
        """Test is_text_like logic."""
        # 1. Not a file -> False
        entry = make_entry("/dir", is_dir=True)
        self.assertFalse(entry.is_text_like)
        
        # 2. File without type info -> False (default assumption)
        entry = make_entry("/file")
        entry.file_type_info = None
        self.assertFalse(entry.is_text_like)
        
        # 3. File with text type info -> True
        entry = make_entry("/file.txt")
        entry.file_type_info = FileTypeInfo("text/plain", "Text", FileTypeCategory.TEXT)
        self.assertTrue(entry.is_text_like)

    def test_fs_entry_type_desc_standard(self):
        """Cover type_desc branches for standard types in treewalk.py."""
        # snapm/_fsdiff/treewalk.py: standard type_desc branches
        # File
        entry_file = make_entry("/file", mode=stat.S_IFREG)
        self.assertEqual(entry_file.type_desc, "file")
        
        # Directory
        entry_dir = make_entry("/dir", is_dir=True)
        self.assertEqual(entry_dir.type_desc, "directory")
        
        # Symlink
        entry_link = make_entry("/link", is_symlink=True)
        self.assertEqual(entry_link.type_desc, "symbolic link")
