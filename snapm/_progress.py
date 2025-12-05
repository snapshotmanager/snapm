# Copyright Red Hat
#
# snapm/_progress.py - Snapshot Manager terminal progress indicator
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Terminal control and progress indicator
"""
from typing import List, Optional, TextIO
from abc import ABC, abstractmethod
import curses
import sys
import re

#: Default number of columns if not detected from terminal.
DEFAULT_COLUMNS = 80

#: Minimum width of a progress bar.
PROGRESS_MIN_WIDTH = 10

#: Default width of a progress bar as a fraction of the terminal size.
DEFAULT_WIDTH_FRAC = 0.5


class TermControl:
    """
    A class for portable terminal control and output.

    Uses the curses package to set up appropriate terminal control
    sequences for the current terminal.

    Inspired by and adapted from:

      https://code.activestate.com/recipes/475116-using-terminfo-for-portable-color-output-cursor-co/

      Copyright Edward Loper and released under the PSF license.

    `TermControl` defines a set of instance variables whose
    values are initialized to the control sequence necessary to
    perform a given action.  These can be simply included in normal
    output to the terminal:

        >>> term = TermControl()
        >>> print 'This is '+term.GREEN+'green'+term.NORMAL

    Alternatively, the `render()` method can used, which replaces
    '${action}' with the string required to perform 'action':

        >>> term = TermControl()
        >>> print term.render('This is ${GREEN}green${NORMAL}')

    If the terminal doesn't support a given action, then the value of
    the corresponding instance variable will be set to ''.  As a
    result, the above code will still work on terminals that do not
    support color, except that their output will not be colored.
    Also, this means that you can test whether the terminal supports a
    given action by simply testing the truth value of the
    corresponding instance variable:

        >>> term = TermControl()
        >>> if term.CLEAR_SCREEN:
        ...     print 'This terminal supports clearning the screen.'

    Finally, if the width and height of the terminal are known, then
    they will be stored in the `columns` and `lines` attributes.
    """

    # Cursor movement:
    BOL: str = ""  #: Move the cursor to the beginning of the line
    UP: str = ""  #: Move the cursor up one line
    DOWN: str = ""  #: Move the cursor down one line
    LEFT: str = ""  #: Move the cursor left one char
    RIGHT: str = ""  #: Move the cursor right one char

    # Deletion:
    CLEAR_SCREEN: str = ""  #: Clear the screen and move to home position
    CLEAR_EOL: str = ""  #: Clear to the end of the line.
    CLEAR_BOL: str = ""  #: Clear to the beginning of the line.
    CLEAR_EOS: str = ""  #: Clear to the end of the screen

    # Output modes:
    BOLD: str = ""  #: Turn on bold mode
    BLINK: str = ""  #: Turn on blink mode
    DIM: str = ""  #: Turn on half-bright mode
    REVERSE: str = ""  #: Turn on reverse-video mode
    NORMAL: str = ""  #: Turn off all modes

    # Terminal bell:
    BELL: str = ""  #: Ring the terminal bell

    # Cursor display:
    HIDE_CURSOR: str = ""  #: Make the cursor invisible
    SHOW_CURSOR: str = ""  #: Make the cursor visible

    # Foreground colors:
    BLACK: str = ""  #: Black foreground color
    BLUE: str = ""  #: Blue foreground color
    GREEN: str = ""  #: Green foreground color
    CYAN: str = ""  #: Cyan foreground color
    RED: str = ""  #: Red foreground color
    MAGENTA: str = ""  #: Magenta foreground color
    YELLOW: str = ""  #: Yellow foreground color
    WHITE: str = ""  #: White foreground color

    # Background colors:
    BG_BLACK: str = ""  #: Black background color
    BG_BLUE: str = ""  #: Blue background color
    BG_GREEN: str = ""  #: Green background color
    BG_CYAN: str = ""  #: Cyan background color
    BG_RED: str = ""  #: Red background color
    BG_MAGENTA: str = ""  #: Magenta background color
    BG_YELLOW: str = ""  #: Yellow background color
    BG_WHITE: str = ""  #: White background color

    # Terminal size:
    columns: Optional[int] = None  #: Terminal width
    lines: Optional[int] = None  #: Terminal height

    _STRING_CAPABILITIES: List[str] = (
        """
    BOL:cr UP:cuu1 DOWN:cud1 LEFT:cub1 RIGHT:cuf1
    CLEAR_SCREEN:clear CLEAR_EOL:el CLEAR_BOL:el1 CLEAR_EOS:ed BOLD:bold
    BLINK:blink DIM:dim REVERSE:rev UNDERLINE:smul NORMAL:sgr0 BELL:bel
    HIDE_CURSOR:civis SHOW_CURSOR:cnorm""".split()
    )
    _COLORS: List[str] = """BLACK BLUE GREEN CYAN RED MAGENTA YELLOW WHITE""".split()
    _ANSI_COLORS: List[str] = "BLACK RED GREEN YELLOW BLUE MAGENTA CYAN WHITE".split()

    def __init__(self, term_stream: Optional[TextIO] = None):
        """
        Initialize terminal capabilities and size information for the provided output stream.
        
        If `term_stream` is not a tty or terminal setup fails, the instance will be left with no terminal capabilities (attributes remain empty or defaulted). When capabilities are available this initializer sets:
        - `columns` and `lines` from the terminal size,
        - string capability attributes named by `_STRING_CAPABILITIES`,
        - foreground color attributes named by `_COLORS` and `_ANSI_COLORS`,
        - background color attributes prefixed with `BG_` for both `_COLORS` and `_ANSI_COLORS`.
        
        Parameters:
            term_stream (TextIO | None): Output stream to probe for terminal capabilities; defaults to stdout.
        """
        # Default to stdout
        if term_stream is None:
            term_stream = sys.stdout

        self.term_stream = term_stream

        # If the stream isn't a tty, then assume it has no capabilities.
        if not hasattr(term_stream, "isatty") or not term_stream.isatty():
            return

        # Check the terminal type.  If we fail, then assume that the
        # terminal has no capabilities.
        try:
            curses.setupterm()
        # Attempting to catch curses.error raises 'TypeError: catching classes
        # that do not inherit from BaseException is not allowed' even though
        # it claims to inherit from builtins.Exception:
        #
        #     Help on class error in module _curses:
        #     class error(builtins.Exception)
        except BaseException:  # pylint: disable=broad-exception-caught
            return  # pragma: no cover

        # Look up numeric capabilities.
        self.columns = curses.tigetnum("cols")
        self.lines = curses.tigetnum("lines")

        # Look up string capabilities.
        for capability in self._STRING_CAPABILITIES:
            (attr, cap_name) = capability.split(":")
            setattr(self, attr, self._tigetstr(cap_name) or "")

        # Colors
        set_fg = self._tigetstr("setf")
        if set_fg:
            set_fg = set_fg.encode("utf8")
            for i, color in enumerate(self._COLORS):
                setattr(self, color, curses.tparm(set_fg, i).decode("utf8") or "")
        set_fg_ansi = self._tigetstr("setaf")
        if set_fg_ansi:
            set_fg_ansi = set_fg_ansi.encode("utf8")
            for i, color in enumerate(self._ANSI_COLORS):
                setattr(self, color, curses.tparm(set_fg_ansi, i).decode("utf8") or "")
        set_bg = self._tigetstr("setb")
        if set_bg:
            set_bg = set_bg.encode("utf8")
            for i, color in enumerate(self._COLORS):
                setattr(
                    self, "BG_" + color, curses.tparm(set_bg, i).decode("utf8") or ""
                )  # pragma: no cover
        set_bg_ansi = self._tigetstr("setab")
        if set_bg_ansi:
            set_bg_ansi = set_bg_ansi.encode("utf8")
            for i, color in enumerate(self._ANSI_COLORS):
                setattr(
                    self,
                    "BG_" + color,
                    curses.tparm(set_bg_ansi, i).decode("utf8") or "",
                )

    def _tigetstr(self, cap_name):
        # String capabilities can include "delays" of the form "$<2>".
        # For any modern terminal, we should be able to just ignore
        # these, so strip them out.
        cap = curses.tigetstr(cap_name)
        cap = cap.decode(encoding="utf8") if cap else ""
        return cap.split("$", maxsplit=1)[0]

    def render(self, template):
        """
        Replace each $-substitutions in the given template string with
        the corresponding terminal control string (if it's defined) or
        '' (if it's not).
        """
        return re.sub(r"\$\$|\${\w+}", self._render_sub, template)

    def _render_sub(self, match):
        s = match.group()
        if s == "$$":
            return "$"
        return getattr(self, s[2:-1], "")


class ProgressBase(ABC):
    """
    An abstract progress reporting class.
    """

    FIXED = -1

    def __init__(self):
        """
        Initialize base attributes used by progress implementations.
        
        Sets default state for lifecycle and rendering configuration:
        - total: number of work items (0 means not started).
        - header: optional title shown with the progress display.
        - term: optional terminal control capabilities.
        - stream: optional output stream for rendering.
        - width: configured progress bar width (-1 indicates unspecified).
        """
        self.total: int = 0
        self.header: Optional[str] = None
        self.term: Optional[TermControl] = None
        self.stream: Optional[TextIO] = None
        self.width: int = -1

    def _calculate_width(
        self, width: Optional[int] = None, width_frac: Optional[float] = None
    ) -> int:
        """
        Compute the number of character cells to allocate for the progress bar body.
        
        Parameters:
            width (Optional[int]): Explicit width in characters to use for the bar body. Mutually exclusive with `width_frac`.
            width_frac (Optional[float]): Fraction of the available terminal space to use (range 0..1). Mutually exclusive with `width`.
        
        Returns:
            int: Calculated width in characters for the progress bar body.
        
        Raises:
            ValueError: If the subclass FIXED value is not initialized (negative), if `header` is not set, or if both `width` and `width_frac` are provided.
        """
        if self.FIXED < 0:
            raise ValueError(
                f"{self.__class__.__name__}: self.FIXED must be initialised "
                "before calling self._calculate_width()"
            )

        if not hasattr(self, "header") or self.header is None:
            raise ValueError(
                f"{self.__class__.__name__}: self.header must be initialised "
                "before calling self._calculate_width()"
            )

        if width is not None and width_frac is not None:
            raise ValueError("width and width_frac are mutually exclusive")

        if hasattr(self, "term") and hasattr(self.term, "columns"):
            columns = self.term.columns
        else:
            columns = DEFAULT_COLUMNS

        if width is None:
            width_frac = width_frac or DEFAULT_WIDTH_FRAC
            fixed = self.FIXED + len(self.header)
            width = round((columns - fixed) * width_frac)
            # Ensure a reasonable minimum width for the bar body.
            width = max(PROGRESS_MIN_WIDTH, width)

        return width

    def start(self, total: int):
        """
        Begin a progress run using the specified total number of work items.
        
        Parameters:
            total (int): Total number of work items to complete; must be greater than zero.
        
        Raises:
            ValueError: If `total` is less than or equal to zero.
        """
        if total <= 0:
            raise ValueError("total must be positive.")

        self.total = total

        self._do_start()

    @abstractmethod
    def _do_start(self):
        """
        Hook invoked when a progress run begins; subclasses should implement startup behavior.
        
        Called by ProgressBase.start(...) after the total has been validated and stored. Implementations may perform any initialization or emit initial output for the progress indicator.
        """

    def _check_in_progress(self, done: int):
        """
        Validate that progress has been started and that `done` is within the valid range.
        
        Parameters:
            done (int): Number of completed items.
        
        Raises:
            ValueError: If progress has not been started (total == 0), if `done` is less than 0,
                        or if `done` is greater than the recorded total.
        """
        theclass = self.__class__.__name__
        if self.total == 0:
            raise ValueError(f"{theclass}.progress() called before start()")

        if done < 0:
            raise ValueError(f"{theclass}.progress() done cannot be negative.")

        if done > self.total:
            raise ValueError(f"{theclass}.progress() done cannot be > total.")

    def progress(self, done: int, message: Optional[str] = None):
        """
        Advance the progress indicator to the specified completed count.
        
        Validates that `done` is within the current progress range and updates the progress display with an optional message.
        
        Parameters:
            done (int): The number of completed items to report.
            message (Optional[str]): Optional text to include with the progress output.
        """
        self._check_in_progress(done)
        self._do_progress(done, message)

    @abstractmethod
    def _do_progress(self, done: int, message: Optional[str] = None):
        """
        Hook for subclasses to update the progress display for the given completed count and optional message.
        
        Parameters:
            done (int): Number of items completed; must be between 0 and the configured total.
            message (Optional[str]): Optional text to include with the progress update.
        """

    def end(self, message: Optional[str] = None):
        """
        End the current progress run, finalize the display, and reset internal state.
        
        Validates that progress is active, updates progress to the stored total (with no inline message), calls the subclass-specific end handler with the optional completion message, and resets the stored total to 0.
        
        Parameters:
            message (Optional[str]): Optional completion message forwarded to the progress end handler; implementations may display it.
        """
        self._check_in_progress(self.total)
        self.progress(self.total, "")
        self._do_end(message)
        self.total = 0

    @abstractmethod
    def _do_end(self, message: Optional[str] = None):
        """
        Perform final end-of-progress handling for the progress instance.
        
        Subclasses should finalize any output/state for a completed progress run and may display the optional completion message.
        
        Parameters:
            message (Optional[str]): An optional completion message to display or record.
        """


class Progress(ProgressBase):
    """
    A 2-line Unicode or ASCII progress bar, which looks like:

        Header: 20% [███████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░]
                           progress message

    or:

        Header: 20% [===========----------------------------------]
                           progress message

    The progress bar is colored, if the terminal supports color
    output and adjusts to the width of the terminal.
    """

    BAR = (
        "${BOLD}${CYAN}%s${NORMAL}: %3d%% "
        "${GREEN}[${BOLD}%s%s${NORMAL}${GREEN}]${NORMAL}\n"
    )  #: Progress bar format string

    FIXED = 9  #: Length of fixed characters in BAR.

    def __init__(
        self,
        header,
        term_stream: Optional[TextIO] = None,
        width: Optional[int] = None,
        width_frac: Optional[float] = None,
        no_clear: bool = False,
        tc: Optional[TermControl] = None,
    ):
        """
        Create a two-line terminal progress renderer with the given header and sizing.
        
        Parameters:
        	header (str): Header text displayed above the progress bar.
        	term_stream (Optional[TextIO]): Output stream for rendering; defaults to stdout. Ignored if `tc` is provided.
        	width (Optional[int]): Exact character width for the progress bar body. Mutually exclusive with `width_frac`.
        	width_frac (Optional[float]): Fraction of terminal width to use for the bar (0.0–1.0). Mutually exclusive with `width`.
        	no_clear (bool): When True, avoid clearing the previously rendered bar on completion.
        	tc (Optional[TermControl]): Preinitialized TermControl to use for terminal capabilities; overrides `term_stream` when set.
        
        Raises:
        	ValueError: If the terminal does not provide required control sequences (clear-to-eol, cursor-up, and beginning-of-line).
        
        Notes:
        	Chooses Unicode block characters for the filled/empty bar when the output stream encoding supports them; falls back to ASCII characters otherwise.
        """
        super().__init__()

        if tc is not None:
            term_stream = tc.term_stream

        self.header: Optional[str] = header
        self.term: Optional[TermControl] = tc or TermControl(term_stream=term_stream)
        self.stream: Optional[TextIO] = term_stream or sys.stdout

        if not (self.term.CLEAR_EOL and self.term.UP and self.term.BOL):
            raise ValueError("Terminal does not support required control characters.")

        self.width: int = self._calculate_width(width=width, width_frac=width_frac)
        self.no_clear = no_clear
        self.pbar: Optional[str] = None

        encoding = getattr(self.stream, "encoding", None)
        if not encoding:
            # Stream has no usable encoding; fall back to ASCII bar.
            self.did = "="
            self.todo = "-"
        else:
            try:
                "█░".encode(encoding)
                self.did = "█"
                self.todo = "░"
            except UnicodeEncodeError:
                self.did = "="
                self.todo = "-"

    def _do_start(self):
        """
        Render and output the initial progress bar header and empty bar to the configured stream.
        
        Sets `self.pbar` to the rendered BAR template, writes the header with 0% and an empty bar to the stream, clears to the end of the line, and flushes the stream if supported.
        """
        self.pbar = self.term.render(self.BAR)

        print(
            self.term.BOL
            + (self.pbar % (self.header, 0, "", self.todo * (self.width - 10)))
            + self.term.CLEAR_EOL,
            file=self.stream,
            end="",
        )
        if hasattr(self.stream, "flush"):
            self.stream.flush()

    def _do_progress(self, done: int, message: Optional[str] = None):
        """
        Update the on-screen two-line progress bar to reflect the given completed count and optional message.
        
        Parameters:
            done (int): Number of completed items.
            message (Optional[str]): Optional text to display centered in the progress area.
        """
        message = message or ""

        percent = float(done) / float(self.total)
        n = int((self.width - 10) * percent)

        print(
            self.term.BOL
            + self.term.UP
            + self.term.CLEAR_EOL
            + (
                self.pbar
                % (
                    self.header,
                    percent * 100,
                    self.did * n,
                    self.todo * (self.width - 10 - n),
                )
            )
            + self.term.CLEAR_EOL
            + message.center(self.width),
            file=self.stream,
            end="",
        )
        if hasattr(self.stream, "flush"):
            self.stream.flush()

    def _do_end(self, message: Optional[str] = None):
        """
        Finalize the progress display and clear the in-progress bar.
        
        Parameters:
            message (Optional[str]): Optional completion message to print after clearing the progress display.
        """
        if not self.no_clear:
            print(self.term.BOL + self.term.UP + self.term.CLEAR_EOL, file=self.stream)
            print(
                self.term.BOL
                + self.term.CLEAR_EOL
                + self.term.UP
                + self.term.CLEAR_EOL,
                file=self.stream,
                end="",
            )
        else:
            print(self.term.CLEAR_BOL + self.term.BOL, file=self.stream, end="")
        if message:
            print(message, file=self.stream)
        self.total = 0
        if hasattr(self.stream, "flush"):
            self.stream.flush()


class SimpleProgress(ProgressBase):
    """
    A simple progress bar that does not rely on terminal capabilities.
    """

    BAR = "%s: %3d%% [%s%s] (%s)"  #: Progress bar format string
    DID = "="  #: Bar character for completed work.
    TODO = "-"  #: Bar character for uncompleted work.
    FIXED = 12  #: Length of fixed characters in BAR.

    def __init__(
        self,
        header,
        term_stream: Optional[TextIO] = None,
        width: Optional[int] = None,
        width_frac: Optional[float] = None,
    ):
        """
        Create a SimpleProgress configured to render a single-line ASCII progress bar.
        
        Parameters:
        	header (str): Text displayed before the progress bar.
        	width (int): Explicit bar width in characters. Mutually exclusive with `width_frac`.
        	width_frac (float): Fraction of terminal width to use for the bar (0.0–1.0). Mutually exclusive with `width`.
        """
        super().__init__()
        self.header: Optional[str] = header
        self.stream: Optional[str] = term_stream or sys.stdout
        self.width: int = self._calculate_width(width=width, width_frac=width_frac)

    def _do_start(self):
        """
        No-op start hook for SimpleProgress that performs no initialization or output.
        
        This method is called by the progress lifecycle when starting but intentionally does nothing for SimpleProgress.
        """
        return

    def _do_progress(self, done: int, message: Optional[str] = None):
        """
        Update the simple, capability-agnostic progress bar to reflect the given completion and optional message.
        
        Prints a single-line ASCII progress bar that includes the header, percentage complete, filled and empty segments, and the provided message; flushes the output stream if possible.
        
        Parameters:
            done (int): Number of completed items; used to compute percentage of self.total.
            message (Optional[str]): Optional text to display alongside the progress bar.
        """
        message = message or ""

        percent = float(done) / float(self.total)
        n = int((self.width - 10) * percent)

        print(
            self.BAR
            % (
                self.header,
                percent * 100,
                self.DID * n,
                self.TODO * (self.width - 10 - n),
                message,
            ),
            file=self.stream,
        )
        if hasattr(self.stream, "flush"):
            self.stream.flush()

    def _do_end(self, message: Optional[str] = None):
        """
        Finalize the progress display and optionally print a completion message.
        
        Parameters:
            message (Optional[str]): Optional completion message to print after progress ends.
        """
        if message:
            print(message, file=self.stream)

        if hasattr(self.stream, "flush"):
            self.stream.flush()


class NullProgress(ProgressBase):
    """
    A progress class that produces no output.
    """

    def _do_start(self):
        """
        No-op start hook for NullProgress that performs no output or state change.
        
        This method exists to satisfy the ProgressBase lifecycle but intentionally does nothing.
        """
        return  # pragma: no cover

    def _do_progress(self, done: int, _message: Optional[str] = None):
        """
        No-op progress update for NullProgress.
        
        This method intentionally does nothing; both `done` and `_message` are ignored.
        """
        return  # pragma: no cover

    def _do_end(self, _message: Optional[str] = None):
        """
        No-op end handler for NullProgress.
        
        Parameters:
            _message (Optional[str]): Optional completion message that is ignored.
        """
        return  # pragma: no cover


class ProgressFactory:
    """
    A factory for constructing progress objects.
    """

    @staticmethod
    def get_progress(
        header: str,
        quiet: bool = False,
        term_stream: Optional[TextIO] = None,
        term_control: Optional[TermControl] = None,
        width: Optional[int] = None,
        width_frac: Optional[float] = None,
        no_clear: bool = False,
    ) -> ProgressBase:
        """
        Return an appropriate ProgressBase instance configured for the environment and options.
        
        Parameters:
            header (str): Text shown as the progress header.
            quiet (bool): If True, return a NullProgress that emits no output.
            term_stream (Optional[TextIO]): Output stream to use; defaults to sys.stdout when omitted.
            term_control (Optional[TermControl]): Optional TermControl to use for terminal-capable progress.
            width (Optional[int]): Fixed bar width in characters. Mutually exclusive with `width_frac`.
            width_frac (Optional[float]): Fraction of terminal width to use for the bar (0.0–1.0). Mutually exclusive with `width`.
            no_clear (bool): When True, do not erase the in-progress bar on end for terminal-capable progress implementations.
        
        Returns:
            ProgressBase: A progress implementation chosen by context:
                - NullProgress when `quiet` is True,
                - SimpleProgress when `term_stream` is not a TTY,
                - Progress when a TTY is available (using `term_control`, `width`/`width_frac`, and `no_clear` as provided).
        """
        if quiet:
            return NullProgress()

        term_stream = term_stream or sys.stdout
        if not hasattr(term_stream, "isatty") or not term_stream.isatty():
            return SimpleProgress(
                header, term_stream, width=width, width_frac=width_frac
            )

        return Progress(
            header,
            term_stream,
            width=width,
            width_frac=width_frac,
            no_clear=no_clear,
            tc=term_control,
        )


__all__ = [
    "Progress",
    "ProgressFactory",
    "NullProgress",
    "SimpleProgress",
    "TermControl",
]