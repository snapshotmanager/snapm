# Copyright Red Hat
#
# tests/fsdiff/test_fsdiffer.py - FsDiffer tests.
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import patch, MagicMock

from snapm import SnapmError

from snapm.fsdiff.engine import DiffEngine, FsDiffResults
from snapm.fsdiff.fsdiffer import FsDiffer
from snapm.fsdiff.treewalk import TreeWalker


class MockManager:
    pass


class TestFsDiffer(unittest.TestCase):
    def test_FsDiffer(self):
        m = MockManager()
        fsd = FsDiffer(m, cache=False)
        self.assertIsNotNone(fsd.manager)
        self.assertIsNotNone(fsd.tree_walker)
        self.assertIsNotNone(fsd.diff_engine)
        self.assertIsInstance(fsd.manager, MockManager)
        self.assertIs(fsd.manager, m)
        self.assertIsInstance(fsd.tree_walker, TreeWalker)
        self.assertIsInstance(fsd.diff_engine, DiffEngine)

    @patch("snapm.fsdiff.fsdiffer.load_cache")
    def test_compare_roots_cache_hit(self, mock_load):
        """Test that compare_roots returns cached result if found."""
        m = MockManager()
        fsd = FsDiffer(m, cache=True)
        fsd.tree_walker = MagicMock()

        mock_result = MagicMock(spec=FsDiffResults)
        mock_load.return_value = mock_result

        mount_a = MagicMock()
        mount_b = MagicMock()

        res = fsd.compare_roots(mount_a, mount_b)
        self.assertEqual(res, mock_result)
        fsd.tree_walker.walk_tree.assert_not_called()

    @patch("snapm.fsdiff.fsdiffer.load_cache", side_effect=SnapmError("Cache error"))
    @patch("snapm.fsdiff.fsdiffer.save_cache")
    def test_compare_roots_cache_miss_saves(self, mock_save, mock_load):
        """Test that compare_roots computes and saves on cache miss/error."""
        m = MockManager()
        fsd = FsDiffer(m, cache=True)

        mount_a = MagicMock()
        mount_a.root = "/"
        mount_b = MagicMock()
        mount_b.root = "/mnt/b"

        # Mock tree walker and engine to avoid real work
        fsd.tree_walker = MagicMock()
        fsd.tree_walker.walk_tree.return_value = {}
        fsd.diff_engine = MagicMock()
        expected_res = MagicMock(spec=FsDiffResults)
        fsd.diff_engine.compute_diff.return_value = expected_res

        res = fsd.compare_roots(mount_a, mount_b)

        self.assertEqual(res, expected_res)
        mock_load.assert_called_with(
            mount_a,
            mount_b,
            fsd.options,
            expires=fsd.cache_expires,
            quiet=False,
            term_control=fsd._term_control,
        )
        mock_save.assert_called_with(
            mount_a, mount_b, expected_res, quiet=False, term_control=fsd._term_control
        )
