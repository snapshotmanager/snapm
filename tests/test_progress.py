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
from datetime import datetime, timedelta
from io import StringIO
import curses

from snapm.progress import (
    NullProgress,
    NullThrobber,
    Progress,
    ProgressBase,
    ProgressFactory,
    SimpleProgress,
    SimpleThrobber,
    TermControl,
    ThrobberBase,
    Throbber,
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

        with patch("snapm.progress.curses") as mock_curses:
            mock_curses.setupterm.side_effect = curses.error("Curses error")

            tc = TermControl(term_stream=mock_stream)
            self.assertEqual(tc.BOL, "")

    def test_term_control_init_success(self):
        """Test successful TermControl initialization with mocked curses."""
        mock_stream = MagicMock()
        mock_stream.isatty.return_value = True

        with patch("snapm.progress.curses") as mock_curses:
            # Mock capabilities
            mock_curses.tigetnum.side_effect = lambda x: 80 if x == "cols" else 24
            # Return bytes for capability strings
            mock_curses.tigetstr.side_effect = lambda x: (
                b"seq" if x in ["cr", "setf"] else None
            )
            # Return bytes for parameterized strings (colors)
            # Mock a single color value for sanity-checking.
            mock_curses.tparm.return_value = b"\x1b[30m"

            tc = TermControl(term_stream=mock_stream)

            self.assertEqual(tc.columns, 80)
            self.assertEqual(tc.lines, 24)
            self.assertEqual(tc.BOL, "seq")
            # Check color initialization logic (depends on tparm)
            self.assertEqual(tc.BLACK, "\x1b[30m")

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

    def test_term_control_init_keyboard_interrupt(self):
        """Test that KeyboardInterrupt in setupterm is re-raised."""
        mock_stream = MagicMock()
        mock_stream.isatty.return_value = True

        with patch("snapm.progress.curses") as mock_curses:
            mock_curses.setupterm.side_effect = KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                TermControl(term_stream=mock_stream)


class TestFlushGuard(unittest.TestCase):
    def test_flush_guard_broken_pipe(self):
        """Test BrokenPipeError handling in flush guard (Lines 265-272)."""
        from snapm.progress import _flush_with_broken_pipe_guard

        mock_stream = MagicMock()
        mock_stream.flush.side_effect = BrokenPipeError()
        # Mock fileno to ensure os.dup2 path is taken
        mock_stream.fileno.return_value = 10

        with patch("snapm.progress.os") as mock_os:
            mock_os.open.return_value = 999
            mock_os.devnull = "/dev/null"
            mock_os.O_WRONLY = 1

            # Should raise the error after attempting to redirect to devnull
            with self.assertRaises(SystemExit):
                _flush_with_broken_pipe_guard(mock_stream)

            # Verify recovery logic
            mock_os.open.assert_called_with("/dev/null", 1)
            mock_os.dup2.assert_called_with(999, 10)
            mock_os.close.assert_called_with(999)

    def test_flush_guard_no_flush_attr(self):
        """Test flush guard with stream lacking flush method."""
        from snapm.progress import _flush_with_broken_pipe_guard
        mock_stream = MagicMock()
        del mock_stream.flush
        # Should not raise
        _flush_with_broken_pipe_guard(mock_stream)


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
            BadProgress()

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
            BadProgress()


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
        mock_tc.CLEAR_BOL = "<CB>"
        mock_tc.UP = "<UP>"
        mock_tc.BOL = "<BOL>"
        mock_tc.HIDE_CURSOR="<HIDE_CURSOR>"
        mock_tc.SHOW_CURSOR="<SHOW_CURSOR>"
        mock_tc.columns = 100
        mock_tc.render.side_effect = lambda x: x  # pass through
        mock_tc.term_stream = StringIO()

        p = Progress("Test", tc=mock_tc, width=20)

        # 1. Start
        p.start(total=10)
        output = mock_tc.term_stream.getvalue()
        self.assertEqual(output, "")

        # 2. Progress: first update
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.progress(5, "Halfway")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<HIDE_CURSOR>", output)
        self.assertIn("<BOL>", output)
        self.assertIn("Test", output)
        self.assertIn("Halfway", output)

        # 3. Progress: second update
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.progress(7, "Over Halfway")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<BOL>", output)
        self.assertIn("Test", output)
        self.assertIn("<UP>", output)
        self.assertIn("Over Halfway", output)

        # 4. Progress: very long message: test budget truncation
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.progress(9, "Nearly Done " + 100 * "X")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<BOL>", output)
        self.assertIn("Test", output)
        self.assertIn("<UP>", output)
        self.assertIn("Nearly Done", output)
        self.assertIn("XXX", output)
        self.assertIn("...", output)

        # 5. End
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.end("Done!")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<SHOW_CURSOR>", output)
        self.assertIn("Done!", output)
        self.assertEqual(p.total, 0)

    def test_lifecycle_no_clear(self):
        """Test lifecycle with no_clear=True preserves final bar."""
        mock_tc = MagicMock(spec=TermControl)
        mock_tc.CLEAR_EOL = "<CE>"
        mock_tc.CLEAR_BOL = "<CB>"
        mock_tc.UP = "<UP>"
        mock_tc.BOL = "<BOL>"
        mock_tc.HIDE_CURSOR="<HIDE_CURSOR>"
        mock_tc.SHOW_CURSOR="<SHOW_CURSOR>"
        mock_tc.columns = 100
        mock_tc.render.side_effect = lambda x: x  # pass through
        mock_tc.term_stream = StringIO()

        p = Progress("Test", tc=mock_tc, width=20, no_clear=True)

        # 1. Start
        p.start(total=10)
        output = mock_tc.term_stream.getvalue()
        self.assertEqual(output, "")

        # 2. Progress: first update
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.progress(5, "Halfway")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<HIDE_CURSOR>", output)
        self.assertIn("<BOL>", output)
        self.assertIn("Test", output)
        self.assertIn("Halfway", output)

        # 3. Progress: second update
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.progress(7, "Over Halfway")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<BOL>", output)
        self.assertIn("Test", output)
        self.assertIn("<UP>", output)
        self.assertIn("Over Halfway", output)

        # 4. Progress: very long message: test budget truncation
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.progress(9, "Nearly Done " + 100 * "X")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<BOL>", output)
        self.assertIn("Test", output)
        self.assertIn("<UP>", output)
        self.assertIn("Nearly Done", output)
        self.assertIn("XXX", output)
        self.assertIn("...", output)

        # 5. End
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.end("Done!")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<SHOW_CURSOR>", output)
        self.assertIn("Test", output)
        self.assertIn("Done!", output)
        self.assertIn(": 100% ", output)
        self.assertEqual(len(output.splitlines()), 3)
        self.assertEqual(p.total, 0)

    def test_lifecycle_cancel(self):
        """Test start, progress, and cancel flow."""
        mock_tc = MagicMock(spec=TermControl)
        mock_tc.CLEAR_EOL = "<CE>"
        mock_tc.CLEAR_BOL = "<CB>"
        mock_tc.UP = "<UP>"
        mock_tc.BOL = "<BOL>"
        mock_tc.HIDE_CURSOR="<HIDE_CURSOR>"
        mock_tc.SHOW_CURSOR="<SHOW_CURSOR>"
        mock_tc.columns = 100
        mock_tc.render.side_effect = lambda x: x  # pass through
        mock_tc.term_stream = StringIO()

        p = Progress("Test", tc=mock_tc, width=20)

        # 1. Start
        p.start(total=10)
        output = mock_tc.term_stream.getvalue()
        self.assertEqual(output, "")

        # 2. Progress: first update
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.progress(5, "Halfway")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<HIDE_CURSOR>", output)
        self.assertIn("<BOL>", output)
        self.assertIn("Test", output)
        self.assertIn("Halfway", output)

        # 3. Cancel while in progres
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        p.cancel("Quit!")
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<SHOW_CURSOR>", output)
        self.assertIn("Quit!", output)

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

    def test_cancel_before_start_raises(self):
        p = Progress("H", tc=self.mock_tc)

        with self.assertRaisesRegex(ValueError, r"Progress.cancel\(\) called before start\(\)"):
            p.cancel("BadQuit!")

    def test_end_before_start_raises(self):
        p = Progress("H", tc=self.mock_tc)

        with self.assertRaisesRegex(ValueError, r"Progress.end\(\) called before start\(\)"):
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
            "Simple:  50% [=========================-------------------------] (working)",
            output,
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


class TestNullProgress(unittest.TestCase):
    def test_lifecycle(self):
        np = NullProgress()
        np.start(10)
        np.progress(5)  # Should produce no error and no output
        np.end()

    def test_start_non_positive_raises(self):
        p = NullProgress()

        with self.assertRaisesRegex(ValueError, "must be positive"):
            p.start(0)

        with self.assertRaisesRegex(ValueError, "must be positive"):
            p.start(-1)

    def test_progress_negative_raises(self):
        np = NullProgress()

        np.start(100)
        with self.assertRaises(ValueError):
            np.progress(-1, "working")

    def test_end_before_start_raises(self):
        np = NullProgress()

        with self.assertRaisesRegex(ValueError, "called before start"):
            np.end("2BadMice!")

    def test_progress_after_end_raises(self):
        np = NullProgress()

        np.start(10)
        np.progress(10, "Stuff")
        np.end("Done!")

        with self.assertRaisesRegex(ValueError, "called before start"):
            np.progress(1, "More Stuff!")

    def test_progress_before_start_raises(self):
        np = NullProgress()
        with self.assertRaises(ValueError):
            np.progress(1)  # Not started

    def test_done_greater_than_total_raises(self):
        np = NullProgress()

        np.start(10)
        with self.assertRaisesRegex(ValueError, "cannot be > total"):
            np.progress(11)


class TestProgressFactory(unittest.TestCase):
    def test_get_progress_quiet(self):
        p = ProgressFactory.get_progress("H", quiet=True)
        self.assertIsInstance(p, NullProgress)

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
        with patch("snapm.progress.TermControl") as mock_tc_cls:
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

    def test_progress_factory_missing_isatty_attr(self):
        """Test factory with stream completely missing isatty attribute (Line 1010)."""
        class DumbStream:
            pass

        # This hits the 'not hasattr' branch of the OR condition
        p = ProgressFactory.get_progress("H", term_stream=DumbStream())
        self.assertIsInstance(p, SimpleProgress)


class TestThrobber(unittest.TestCase):
    def setUp(self):
        # Create a mock TermControl with necessary capabilities
        self.mock_tc = MagicMock(spec=TermControl)
        self.mock_tc.HIDE_CURSOR = "<HIDE>"
        self.mock_tc.SHOW_CURSOR = "<SHOW>"
        self.mock_tc.LEFT = "<LEFT>"
        self.mock_tc.CLEAR_EOL = "<CE>"
        self.mock_tc.GREEN = "<G>"
        self.mock_tc.NORMAL = "<N>"
        self.mock_tc.term_stream = MagicMock()
        self.mock_tc.term_stream.encoding = "ascii"
        self.mock_tc.render.side_effect = lambda x: x

    def test_init_defaults(self):
        """Test Throbber initialization and default frame selection."""
        self.mock_tc.term_stream.encoding = "utf-8"
        # Test Unicode frames
        t = Throbber("H", tc=self.mock_tc)
        self.assertIn("⠁", t.frames)

        # Test ASCII fallback
        self.mock_tc.term_stream.encoding = "ascii"
        t_ascii = Throbber("H", tc=self.mock_tc)
        self.assertIn("-", t_ascii.frames)
        self.assertNotIn("⠁", t_ascii.frames)

    def test_init_style_bouncing_ball(self):
        """Test Throbber initialization and bouncingball frame selection."""
        self.mock_tc.term_stream.encoding = "utf-8"
        # Test Unicode frames
        t = Throbber("H", style="bouncingball", tc=self.mock_tc)
        self.assertIn("[●     ]", t.frames)

    @patch("snapm.progress.datetime")
    def test_lifecycle_flow(self, mock_dt):
        """Test the start -> throb -> end lifecycle with output verification."""
        # Create a mock TermControl with necessary capabilities
        mock_tc = MagicMock(spec=TermControl)
        mock_tc.HIDE_CURSOR = "<HIDE>"
        mock_tc.SHOW_CURSOR = "<SHOW>"
        mock_tc.LEFT = "<LEFT>"
        mock_tc.RIGHT = "<RIGHT>"
        mock_tc.BOL = "<BOL>"
        mock_tc.UP = "<UP>"
        mock_tc.CLEAR_EOL = "<CE>"
        mock_tc.GREEN = "<G>"
        mock_tc.NORMAL = "<N>"
        mock_tc.term_stream = StringIO()
        mock_tc.render.side_effect = lambda x: x

        t = Throbber("Working", tc=mock_tc)

        # Setup time: Start at T0
        start_time = datetime(2024, 1, 1, 12, 0, 0)
        # Sequence of times returned by datetime.now():
        # 1. start() -> sets _last
        # 2. start() -> calls throb() -> checks time (no update)
        # 3. explicit throb() call -> checks time (update triggers)
        mock_dt.now.side_effect = [
            start_time,
            start_time + timedelta(microseconds=100001),
            start_time + timedelta(microseconds=200001)
        ]

        # 1. Start
        t.start()
        output = mock_tc.term_stream.getvalue()
        self.assertIn("<HIDE>Working:", output)
        self.assertIn(t.frames[0], output)
        self.assertTrue(t.started)

        # 2. Throb (Time advances, frame updates)
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        t.throb()
        output = mock_tc.term_stream.getvalue()
        # Expect: Begin, Up, Clear Line, Color, Frame char, Normal
        self.assertIn("<BOL><UP><CE>", output)
        self.assertIn("<G>", output)
        self.assertIn(t.frames[1], output)

        # 3. End
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)
        t.end("Done!")
        output = mock_tc.term_stream.getvalue()
        # Expect: Left move, Clear Line, Show Cursor, Message
        # <UP><RIGHT><RIGHT><RIGHT><RIGHT><RIGHT><RIGHT><RIGHT><RIGHT><RIGHT><CE><SHOW>
        self.assertIn("<UP>" + (len(t.header) + 2) * "<RIGHT>" + "<CE>", output)
        self.assertIn("<SHOW>", output)
        self.assertIn("Done!", output)
        self.assertFalse(t.started)

    def test_no_clear_behavior(self):
        """Test that no_clear=True prevents clearing the spinner on end."""
        # Create a mock TermControl with necessary capabilities
        mock_tc = MagicMock(spec=TermControl)
        mock_tc.HIDE_CURSOR = "<HIDE>"
        mock_tc.SHOW_CURSOR = "<SHOW>"
        mock_tc.RIGHT = "<RIGHT>"
        mock_tc.UP = "<UP>"
        mock_tc.CLEAR_EOL = "<CE>"
        mock_tc.GREEN = "<G>"
        mock_tc.NORMAL = "<N>"
        mock_tc.term_stream = StringIO()
        mock_tc.render.side_effect = lambda x: x

        t = Throbber("H", tc=mock_tc, no_clear=True)

        # Manually set state to simulate having updated at least once
        t.first_update = False

        t._do_end("Finished")
        output = mock_tc.term_stream.getvalue()

        # Should NOT clear the line (no <LEFT><CE>)
        self.assertNotIn("<LEFT><CE>", output)
        # Should still show cursor
        self.assertIn("<SHOW>", output)

    def test_validation(self):
        """Test state validation (throb before start)."""
        t = Throbber("H", tc=self.mock_tc)
        with self.assertRaisesRegex(ValueError, "called before start"):
            t.throb()

    def test_throbber_end_no_message(self):
        """Test ending a throbber without a message (Line 952)."""
        mock_tc = MagicMock(spec=TermControl)
        mock_tc.term_stream = StringIO()
        mock_tc.render.side_effect = lambda x: x
        # Mock required caps to enter the logic block
        mock_tc.UP = "<UP>"
        mock_tc.RIGHT = "<R>"
        mock_tc.CLEAR_EOL = "<CE>"
        mock_tc.SHOW_CURSOR = "<SHOW>"

        t = Throbber("H", tc=mock_tc)
        t.start()
        # Force first_update=False to trigger the clearing logic
        t.first_update = False

        # Reset stream to capture only end output
        mock_tc.term_stream.truncate(0)
        mock_tc.term_stream.seek(0)

        # Call with None to test the ternary 'else ""' branch
        t.end(None)

        output = mock_tc.term_stream.getvalue()
        # Should contain clearing logic
        self.assertIn("<UP>" + (len(t.header) + 2) * "<R>" + "<CE>", output)
        self.assertIn("<SHOW>", output)
        # Should NOT contain "None" string
        self.assertNotIn("None", output)

    def test_end_before_start_raises(self):
        t = Throbber("H")

        with self.assertRaisesRegex(ValueError, r"Throbber.end\(\) called before start\(\)"):
            t.end("BadQuit!")

    def test_throbber_invalid_state(self):
        """Test throb() raises if started=True but _last is None (Line 791)."""
        t = Throbber("H")
        t.started = True
        t._last = None # Explicitly corrupt state

        with self.assertRaisesRegex(ValueError, "invalid throbber state"):
            t.throb()


class TestSimpleThrobber(unittest.TestCase):
    @patch("snapm.progress.datetime")
    def test_simple_flow(self, mock_dt):
        stream = StringIO()
        st = SimpleThrobber("Loading", term_stream=stream)

        # Setup time sequence
        start_time = datetime(2024, 1, 1, 12, 0, 0)
        mock_dt.now.side_effect = [
            start_time,
            start_time + timedelta(microseconds=100001),
            start_time + timedelta(microseconds=200001),
        ]

        # Start
        st.start()
        self.assertIn("Loading: ", stream.getvalue())

        # Throb
        st.throb()
        # SimpleThrobber appends dots/frames without clearing
        self.assertIn(".", stream.getvalue())

        # End
        st.end("Done")
        self.assertIn("Done\n", stream.getvalue())


class TestNullThrobber(unittest.TestCase):
    def test_silent_operation(self):
        nt = NullThrobber("H")
        # Should not raise errors
        nt.start()
        nt.throb()
        nt.end("Msg")
        # No stream output to check since it doesn't hold one,
        # but we verified it runs without crashing.


class TestThrobberFactory(unittest.TestCase):
    def setUp(self):
        # Create a mock TermControl with necessary capabilities
        self.mock_tc = MagicMock(spec=TermControl)
        self.mock_tc.HIDE_CURSOR = "<HIDE>"
        self.mock_tc.SHOW_CURSOR = "<SHOW>"
        self.mock_tc.LEFT = "<LEFT>"
        self.mock_tc.CLEAR_EOL = "<CE>"
        self.mock_tc.GREEN = "<G>"
        self.mock_tc.NORMAL = "<N>"
        self.mock_tc.term_stream = MagicMock()
        self.mock_tc.term_stream.isatty.return_value = True
        self.mock_tc.term_stream.encoding = "utf8"
        self.mock_tc.render.side_effect = lambda x: x


    def test_get_throbber_styles(self):
        xstyles = sorted(list(Throbber.STYLES.keys()))
        styles = sorted(ProgressFactory.get_throbber_styles())
        self.assertEqual(styles, xstyles)

    def test_get_throbber_quiet(self):
        t = ProgressFactory.get_throbber("H", quiet=True)
        self.assertIsInstance(t, NullThrobber)

    def test_get_throbber_simple(self):
        # Mock stream as non-tty
        mock_stream = MagicMock()
        mock_stream.isatty.return_value = False

        t = ProgressFactory.get_throbber("H", term_stream=mock_stream)
        self.assertIsInstance(t, SimpleThrobber)

    def test_get_throbber_fancy(self):
        t = ProgressFactory.get_throbber("H", term_control=self.mock_tc)
        self.assertIsInstance(t, Throbber)

    def test_get_throbber_params(self):
        """Test passing no_clear and TermControl to factory."""
        t = ProgressFactory.get_throbber("H", term_control=self.mock_tc, no_clear=True)

        self.assertIsInstance(t, Throbber)
        self.assertEqual(t.term, self.mock_tc)
        self.assertTrue(t.no_clear)

    def test_get_throbber_unknown_style(self):
        with self.assertRaises(ValueError):
            ProgressFactory.get_throbber("Foo", style="quux", term_control=self.mock_tc)

    def test_throbber_factory_missing_isatty_attr(self):
        """Test factory with stream completely missing isatty attribute (Line 1010)."""
        class DumbStream:
            pass
        # This hits the 'not hasattr' branch of the OR condition
        t = ProgressFactory.get_throbber("H", term_stream=DumbStream())
        self.assertIsInstance(t, SimpleThrobber)
