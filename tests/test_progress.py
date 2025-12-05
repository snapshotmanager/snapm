# Copyright Red Hat
#
# tests/test_progress.py - Progress and TermControl tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
import sys
from unittest.mock import MagicMock, patch
from io import StringIO
import curses

from snapm._progress import (
    TermControl,
    ProgressBase,
    Progress,
    SimpleProgress,
    QuietProgress,
    ProgressFactory,
)


class TestTermControl(unittest.TestCase):
    def test_term_control_default_stdout(self):
        """Test TermControl when stream is None"""
        tc = TermControl()
        self.assertIsNotNone(tc)

    def test_term_control_no_tty(self):
        """Test TermControl when stream is not a TTY."""
        mock_stream = MagicMock()
        mock_stream.isatty.return_value = False

        tc = TermControl(term_stream=mock_stream)

        # attributes should be empty strings
        self.assertEqual(tc.BOL, "")
        self.assertEqual(tc.GREEN, "")
        self.assertIsNone(tc.columns)

    def test_term_control_curses_error(self):
        """Test TermControl handles curses setup errors gracefully."""
        mock_stream = MagicMock()
        mock_stream.isatty.return_value = True

        with patch("snapm._progress.curses") as mock_curses:
            mock_curses.setupterm.side_effect = curses.error("Curses error")

            tc = TermControl(term_stream=mock_stream)
            self.assertEqual(tc.BOL, "")

    def test_term_control_init_success(self):
        """Test successful TermControl initialization with mocked curses."""
        mock_stream = MagicMock()
        mock_stream.isatty.return_value = True

        with patch("snapm._progress.curses") as mock_curses:
            # Mock capabilities
            mock_curses.tigetnum.side_effect = lambda x: 80 if x == "cols" else 24
            # Return bytes for capability strings
            mock_curses.tigetstr.side_effect = lambda x: (
                b"seq" if x in ["cr", "setf"] else None
            )
            # Return bytes for parameterized strings (colors)
            mock_curses.tparm.return_value = b"\x1b[32m"

            tc = TermControl(term_stream=mock_stream)

            self.assertEqual(tc.columns, 80)
            self.assertEqual(tc.lines, 24)
            self.assertEqual(tc.BOL, "seq")
            # Check color initialization logic (depends on tparm)
            self.assertEqual(tc.BLACK, "\x1b[32m")

    def test_term_control_render(self):
        """Test the render method replaces placeholders."""
        tc = TermControl(term_stream=MagicMock())
        # Manually set attributes for testing
        tc.GREEN = "<G>"
        tc.NORMAL = "<N>"

        text = "This is ${GREEN}green${NORMAL}"
        rendered = tc.render(text)
        self.assertEqual(rendered, "This is <G>green<N>")

        # Test escaped $
        self.assertEqual(tc.render("Money$$"), "Money$")


class TestProgressBase(unittest.TestCase):
    def test_bad_child_no_FIXED(self):
        class BadProgress(ProgressBase):
            FIXED = -1

            def __init__(self):
                self.header = "Header"
                self.width = self._calculate_width(width=20)

            def _do_start(self, _total: int):
                pass

            def _do_progress(self, _done: int, _message: str):
                pass

            def _do_end(self, _message: str):
                pass

        with self.assertRaisesRegex(ValueError, r"self\.FIXED must be"):
            bp = BadProgress()

    def test_bad_child_no_header(self):
        class BadProgress(ProgressBase):
            FIXED = 1

            def __init__(self):
                self.width = self._calculate_width(width=20)

            def _do_start(self, _total: int):
                pass

            def _do_progress(self, _done: int, _message: str):
                pass

            def _do_end(self, _message: str):
                pass

        with self.assertRaisesRegex(ValueError, r"self\.header must be"):
            bp = BadProgress()


class TestProgress(unittest.TestCase):
    def setUp(self):
        # Create a mock TermControl with necessary capabilities
        self.mock_tc = MagicMock(spec=TermControl)
        self.mock_tc.CLEAR_EOL = "<CE>"
        self.mock_tc.UP = "<UP>"
        self.mock_tc.BOL = "<BOL>"
        self.mock_tc.columns = 100
        self.mock_tc.render.side_effect = lambda x: x  # pass through
        self.mock_tc.term_stream = MagicMock()
        self.mock_tc.term_stream.encoding = "ascii"

    def test_init_valid(self):
        """Test valid initialization of Progress."""
        p = Progress("Header", tc=self.mock_tc)
        self.assertGreater(p.width, 10)

    def test_init_mutually_exclusive_width(self):
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            Progress("H", width=10, width_frac=0.5, tc=MagicMock())

    def test_init_missing_capabilities(self):
        """Test error when terminal lacks capabilities."""
        bad_tc = MagicMock(spec=TermControl)
        bad_tc.term_stream = MagicMock()
        bad_tc.term_stream.encoding = "ascii"
        bad_tc.CLEAR_EOL = ""  # Missing capability

        with self.assertRaisesRegex(ValueError, "Terminal does not support"):
            Progress("H", tc=bad_tc)

    def test_unicode_fallback(self):
        """Test fallback characters on encoding error."""
        mock_stream = MagicMock()
        # Mock encoding to ascii to trigger UnicodeEncodeError on '█'
        mock_stream.encoding = "ascii"

        p = Progress("H", term_stream=mock_stream, tc=self.mock_tc)
        self.assertEqual(p.did, "=")
        self.assertEqual(p.todo, "-")

    def test_lifecycle(self):
        """Test start, progress, and end flow."""
        mock_tc = MagicMock(spec=TermControl)
        mock_tc.CLEAR_EOL = "<CE>"
        mock_tc.UP = "<UP>"
        mock_tc.BOL = "<BOL>"
        mock_tc.columns = 100
        mock_tc.render.side_effect = lambda x: x  # pass through
        mock_tc.term_stream = StringIO()

        p = Progress("Test", tc=mock_tc, width=20)

        # 1. Start
        p.start(total=10)
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<BOL>", output)
        self.assertIn("Test", output)

        # 2. Progress
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.progress(5, "Halfway")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<UP>", output)
        self.assertIn("Halfway", output)

        # 3. End
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.end("Done!")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("Done!", output)
        self.assertEqual(p.total, 0)

    def test_lifecycle_no_clear(self):
        """Test start, progress, and end flow."""
        mock_tc = MagicMock(spec=TermControl)
        mock_tc.CLEAR_EOL = "<CE>"
        mock_tc.UP = "<UP>"
        mock_tc.BOL = "<BOL>"
        mock_tc.columns = 100
        mock_tc.render.side_effect = lambda x: x  # pass through
        mock_tc.term_stream = StringIO()

        p = Progress("Test", tc=mock_tc, width=20, no_clear=True)

        # 1. Start
        p.start(total=10)
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<BOL>", output)
        self.assertIn("Test", output)

        # 2. Progress
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.progress(5, "Halfway")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<UP>", output)
        self.assertIn("Halfway", output)

        # 3. End
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.end("Done!")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("Test", output)
        self.assertIn("Done!", output)
        self.assertEqual(len(output.splitlines()), 2)
        self.assertEqual(p.total, 0)

    def test_start_non_positive_raises(self):
        p = Progress("H", tc=self.mock_tc)

        with self.assertRaisesRegex(ValueError, "must be positive"):
            p.start(0)

        with self.assertRaisesRegex(ValueError, "must be positive"):
            p.start(-1)

    def test_progress_before_start_raises(self):
        p = Progress("H", tc=self.mock_tc)

        with self.assertRaisesRegex(ValueError, "called before start"):
            p.progress(1)

    def test_progress_negative_raises(self):
        p = Progress("H", tc=self.mock_tc)

        p.start(10)
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            p.progress(-1)

    def test_end_before_start_raises(self):
        p = Progress("H", tc=self.mock_tc)

        with self.assertRaisesRegex(ValueError, "called before start"):
            p.end("2BadMice!")

    def test_progress_after_end_raises(self):
        p = Progress("P", tc=self.mock_tc)

        p.start(10)
        p.progress(10, "Stuff")
        p.end("Done!")

        with self.assertRaisesRegex(ValueError, "called before start"):
            p.progress(1, "More Stuff!")

    def test_done_greater_than_total_raises(self):
        p = Progress("H", tc=self.mock_tc)

        p.start(10)
        with self.assertRaisesRegex(ValueError, "cannot be > total"):
            p.progress(11)


class TestSimpleProgress(unittest.TestCase):
    def test_flow(self):
        mock_stream = StringIO()
        sp = SimpleProgress("Simple", term_stream=mock_stream, width=50)

        sp.start(100)
        sp.progress(50, "working")

        output = mock_stream.getvalue()
        self.assertIn(
            "Simple:  50% [====================--------------------] (working)", output
        )

        sp.end("Finished")
        self.assertIn("Finished", mock_stream.getvalue())

    def test_start_non_positive_raises(self):
        sp = SimpleProgress("S")

        with self.assertRaisesRegex(ValueError, "must be positive"):
            sp.start(0)

        with self.assertRaisesRegex(ValueError, "must be positive"):
            sp.start(-1)

    def test_progress_negative_raises(self):
        sp = SimpleProgress("S")

        sp.start(100)
        with self.assertRaises(ValueError):
            sp.progress(-1, "working")

    def test_end_before_start_raises(self):
        sp = SimpleProgress("S")

        with self.assertRaisesRegex(ValueError, "called before start"):
            sp.end("2BadMice!")

    def test_progress_after_end_raises(self):
        sp = SimpleProgress("S")

        sp.start(10)
        sp.progress(10, "Stuff")
        sp.end("Done!")

        with self.assertRaisesRegex(ValueError, "called before start"):
            sp.progress(1, "More Stuff!")

    def test_progress_before_start_raises(self):
        sp = SimpleProgress("S")
        with self.assertRaises(ValueError):
            sp.progress(1)  # Not started

    def test_done_greater_than_total_raises(self):
        sp = SimpleProgress("S")

        sp.start(10)
        with self.assertRaisesRegex(ValueError, "cannot be > total"):
            sp.progress(11)


class TestQuietProgress(unittest.TestCase):
    def test_lifecycle(self):
        qp = QuietProgress()
        qp.start(10)
        qp.progress(5)  # Should produce no error and no output
        qp.end()

    def test_start_non_positive_raises(self):
        p = QuietProgress()

        with self.assertRaisesRegex(ValueError, "must be positive"):
            p.start(0)

        with self.assertRaisesRegex(ValueError, "must be positive"):
            p.start(-1)

    def test_progress_negative_raises(self):
        qp = QuietProgress()

        qp.start(100)
        with self.assertRaises(ValueError):
            qp.progress(-1, "working")

    def test_end_before_start_raises(self):
        qp = QuietProgress()

        with self.assertRaisesRegex(ValueError, "called before start"):
            qp.end("2BadMice!")

    def test_progress_after_end_raises(self):
        qp = QuietProgress()

        qp.start(10)
        qp.progress(10, "Stuff")
        qp.end("Done!")

        with self.assertRaisesRegex(ValueError, "called before start"):
            qp.progress(1, "More Stuff!")

    def test_progress_before_start_raises(self):
        qp = QuietProgress()
        with self.assertRaises(ValueError):
            qp.progress(1)  # Not started

    def test_done_greater_than_total_raises(self):
        qp = QuietProgress()

        qp.start(10)
        with self.assertRaisesRegex(ValueError, "cannot be > total"):
            qp.progress(11)


class TestProgressFactory(unittest.TestCase):
    def test_get_progress_quiet(self):
        p = ProgressFactory.get_progress("H", quiet=True)
        self.assertIsInstance(p, QuietProgress)

    def test_get_progress_simple(self):
        # Mock stream as non-tty
        mock_stream = MagicMock()
        mock_stream.isatty.return_value = False

        p = ProgressFactory.get_progress("H", term_stream=mock_stream)
        self.assertIsInstance(p, SimpleProgress)

    def test_get_progress_fancy(self):
        # Mock stream as tty
        mock_stream = MagicMock()
        mock_stream.isatty.return_value = True
        mock_stream.encoding = "utf8"

        # Mock TermControl inside factory to avoid real curses checks
        with patch("snapm._progress.TermControl") as mock_tc_cls:
            # Ensure the mock instance has required capabilities
            mock_instance = mock_tc_cls.return_value
            mock_instance.CLEAR_EOL = "yes"
            mock_instance.UP = "yes"
            mock_instance.BOL = "yes"
            mock_instance.columns = 80

            p = ProgressFactory.get_progress("H", term_stream=mock_stream)
            self.assertIsInstance(p, Progress)

    def test_args_validation(self):
        with self.assertRaises(ValueError):
            ProgressFactory.get_progress("H", width=10, width_frac=0.5)
