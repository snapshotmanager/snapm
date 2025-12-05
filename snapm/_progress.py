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
        Create a `TermControl` and initialize its attributes with appropriate
        values for the current terminal.  `term_stream` is the stream that will
        be used for terminal output; if this stream is not a tty, then the
        terminal is assumed to be a dumb terminal (i.e., have no capabilities).
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
        except Exception:  # pylint: disable=broad-exception-caught
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
        Initialise a ``ProgressBase`` child instance.
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
        Calculate the width for the progress bar using values
        defined by child classes. The ``header`` and (optionally, for classes
        that use it) ``term`` members must be initialised before calling this
        method.

        :param width: An optional width value in characters. If specified
                      the progress bar will occupy this width. Cannot be used
                      with ``width_frac``.
        :type width: ``int``
        :param width_frac: An optional width fraction between [0..1]. If
                           specified the terminal width is multiplied by this
                           value and rounded to the nearest integer to
                           determine the width of the progress bar. Cannot be
                           used with ``width``.
        :type width_frac: ``Optional[float]``
        :returns: The calculated progress bar width in characters.
        :rtype: ``int``
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
        Start reporting progress on this ``ProgressBase`` object.

        :param total: The total number of expected progress items.
        :type total: ``int``
        """
        if total <= 0:
            raise ValueError("total must be positive.")

        self.total = total

        self._do_start()

    @abstractmethod
    def _do_start(self):
        """
        Start reporting progress on this ``ProgressBase`` object. Implements
        subclass specific start behaviour.

        :param total: The total number of expected progress items.
        :type total: ``int``
        """

    def _check_in_progress(self, done: int):
        """
        Validate whether this ``ProgressBase`` instance has been started, and
        whether ``done`` falls within the permissible range.

        :param done: The number of completed progress items.
        :type done: ``int``
        :raises: ``ValueError`` if progress has not been started, has already
                 ended or ``done`` falls outside the range [0..total].
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
        Report progress on this ``ProgressBase`` instance.

        :param done: The number of completed progress items.
        :type done: ``int``
        :param message: An optional progress message.
        :type message: ``Optional[str]``
        """
        self._check_in_progress(done)
        self._do_progress(done, message)

    @abstractmethod
    def _do_progress(self, done: int, message: Optional[str] = None):
        """
        Report progress on this ``ProgressBase`` child instance. Implements
        subclass specific progress update behaviour.

        :param done: The number of completed progress items.
        :type done: ``int``
        :param message: An optional progress message.
        :type message: ``Optional[str]``
        """

    def end(self, message: Optional[str] = None):
        """
        End progress reporting on this ``Progress`` instance.

        :param message: An optional completion message.
        :type message: ``Optional[str]``
        """
        self._check_in_progress(self.total)
        self.progress(self.total, "")
        self._do_end(message)
        self.total = 0

    @abstractmethod
    def _do_end(self, message: Optional[str] = None):
        """
        End progress reporting on this ``ProgressBase`` child instance.
        Implements subclass specific progress update behaviour.


        :param message: An optional completion message.
        :type message: ``Optional[str]``
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
        Initialise a new ``Progress`` object.

        :param term_stream: The terminal stream to write to.
        :type term_stream: ``TextIO``
        :param width: An optional width value in characters. If specified
                      the progress bar will occupy this width. Cannot be used
                      with ``width_frac``.
        :type width: ``int``
        :param width_frac: An optional width fraction between [0..1]. If
                           specified the terminal width is multiplied by this
                           value and rounded to the nearest integer to
                           determine the width of the progress bar. Cannot be
                           used with ``width``.
        :type width_frac: ``Optional[float]``
        :param tc: An optional ``TermControl`` object already initialised with
                               a ``term_stream`` value. If this argument is set
                               it will override any ``term_stream`` argument.
        :type tc: ``Optional[TermControl]``
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
        Start reporting progress on this ``Progress`` object.

        :param total: The total number of expected progress items.
        :type total: ``int``
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
        Report progress on this ``Progress`` instance.

        :param done: The number of completed progress items.
        :type done: ``int``
        :param message: An optional progress message.
        :type message: ``Optional[str]``
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
        End progress reporting on this ``Progress`` instance.

        :param message: An optional completion message.
        :type message: ``Optional[str]``
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
        Initialise a new ``SimpleProgress`` object.

        :param term_stream: The terminal stream to write to.
        :type term_stream: ``TextIO``
        :param width: An optional width value in characters. If specified
                      the progress bar will occupy this width. Cannot be used
                      with ``width_frac``.
        :type width: ``int``
        :param width_frac: An optional width fraction between [0..1]. If
                           specified the terminal width is multiplied by this
                           value and rounded to the nearest integer to
                           determine the width of the progress bar. Cannot be
                           used with ``width``.
        :type width_frac: ``Optional[float]``
        """
        super().__init__()
        self.header: Optional[str] = header
        self.stream: Optional[str] = term_stream or sys.stdout
        self.width: int = self._calculate_width(width=width, width_frac=width_frac)

    def _do_start(self):
        """
        Start reporting progress on this ``SimpleProgress`` object.

        :param total: The total number of expected progress items.
        :type total: ``int``
        """
        return

    def _do_progress(self, done: int, message: Optional[str] = None):
        """
        Report progress on this ``SimpleProgress`` instance.

        :param done: The number of completed progress items.
        :type done: ``int``
        :param message: An optional progress message.
        :type message: ``Optional[str]``
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
        End progress reporting on this ``SimpleProgress`` instance.

        :param message: An optional completion message.
        :type message: ``Optional[str]``
        """
        if message:
            print(message, file=self.stream)

        if hasattr(self.stream, "flush"):
            self.stream.flush()


class QuietProgress(ProgressBase):
    """
    A progress class that produces no output.
    """

    def _do_start(self):
        """
        Start reporting progress on this ``QuietProgress`` object.

        :param total: The total number of expected progress items.
        :type total: ``int``
        """
        return  # pragma: no cover

    def _do_progress(self, done: int, _message: Optional[str] = None):
        """
        Report progress on this ``QuietProgress`` instance.

        :param done: The number of completed progress items.
        :type done: ``int``
        :param _message: An optional progress message (unused).
        :type _message: ``Optional[str]``
        """
        return  # pragma: no cover

    def _do_end(self, _message: Optional[str] = None):
        """
        End progress reporting on this ``QuietProgress`` instance.

        :param _message: An optional completion message (unused).
        :type _message: ``Optional[str]``
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
        Factory method to construct progress objects derived from
        ``ProgressBase``.

        :param header: The progress report header.
        :type header: ``str``
        :param quiet: Suppress all output.
        :type quiet: ``bool``
        :param term_stream: An optional ``TextIO`` output object.
                            Defaults to ``sys.stdout`` if unspecified.
        :type term_stream: ``Optional[TextIO]``
        :param term_control: An optional ``TermControl`` object to use
                             for the progress report.
        :type term_control: ``Optional[TermControl]``
        :param width: An optional width value in characters. If specified
                      the progress bar will occupy this width. Cannot be used
                      with ``width_frac``.
        :type width: ``int``
        :param width_frac: An optional width fraction between [0..1]. If
                           specified the terminal width is multiplied by this
                           value and rounded to the nearest integer to
                           determine the width of the progress bar. Cannot be
                           used with ``width``.
        :type width_frac: ``Optional[float]``
        :param no_clear: For progress indicators that clear and re-draw the
                         content on progress, do not erase the progress bar
                         when ``ProgressBase.end()`` is called, leaving it
                         on the terminal for the user to refer to. Ignored
                         by ``ProgressBase`` child classes that do not use
                         ``TerminalControl``.
        :type no_clear: ``bool``
        """
        if quiet:
            return QuietProgress()

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
    "QuietProgress",
    "SimpleProgress",
    "TermControl",
]
