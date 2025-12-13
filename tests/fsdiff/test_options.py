# Copyright Red Hat
#
# tests/fsdiff/test_options.py - DiffOptions tests.
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest

from snapm.fsdiff.options import DiffOptions


class TestDiffOptions(unittest.TestCase):
    def test_DiffOptions__str__(self):
        opts = DiffOptions(ignore_timestamps=True, file_patterns=["*.txt"])
        s = str(opts)
        self.assertIn("ignore_timestamps=True", s)
        self.assertIn("file_patterns=*.txt", s)
