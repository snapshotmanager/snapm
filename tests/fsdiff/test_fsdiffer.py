# Copyright Red Hat
#
# tests/fsdiff/test_fsdiffer.py - FsDiffer tests.
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest

from snapm._fsdiff.engine import DiffEngine
from snapm._fsdiff.fsdiffer import FsDiffer
from snapm._fsdiff.treewalk import TreeWalker


class MockManager:
    pass


class TestFsDiffer(unittest.TestCase):
    def test_FsDiffer(self):
        m = MockManager()
        fsd = FsDiffer(m)
        self.assertIsNotNone(fsd.manager)
        self.assertIsNotNone(fsd.tree_walker)
        self.assertIsNotNone(fsd.diff_engine)
        self.assertIsInstance(fsd.manager, MockManager)
        self.assertIs(fsd.manager, m)
        self.assertIsInstance(fsd.tree_walker, TreeWalker)
        self.assertIsInstance(fsd.diff_engine, DiffEngine)
