# Copyright Red Hat
#
# snapm/progress.py - Snapshot Manager terminal progress indicator
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Terminal control and progress indicator
"""
from typing import ClassVar, Dict, List, Optional, TextIO, Union
from datetime import datetime, timedelta
from abc import ABC, abstractmethod
import curses
import sys
import os
import re

from snapm import register_progress, unregister_progress

#: Default number of columns if not detected from terminal.
DEFAULT_COLUMNS = 80

#: Minimum width of a progress bar.
PROGRESS_MIN_WIDTH = 10

#: Minimum budget to reserve for status messages
MIN_BUDGET = 10

#: Default width of a progress bar as a fraction of the terminal size.
DEFAULT_WIDTH_FRAC = 0.5

#: Default frames-per-second for Throbber classes
DEFAULT_THROBBER_FPS = 10

#: Microseconds per second
_USECS_PER_SEC = 1000000


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
    _LEGACY_COLORS: List[str] = (
        """BLACK BLUE GREEN CYAN RED MAGENTA YELLOW WHITE""".split()
    )
    _ANSI_COLORS: List[str] = "BLACK RED GREEN YELLOW BLUE MAGENTA CYAN WHITE".split()

    def _init_colors(self):
        """
        Initialize terminal color codes.
        """
        set_fg = self._tigetstr("setf")
        if set_fg:
            set_fg = set_fg.encode("utf8")
            for i, color in enumerate(self._LEGACY_COLORS):
                setattr(self, color, curses.tparm(set_fg, i).decode("utf8") or "")
        set_fg_ansi = self._tigetstr("setaf")
        if set_fg_ansi:
            set_fg_ansi = set_fg_ansi.encode("utf8")
            for i, color in enumerate(self._ANSI_COLORS):
                setattr(self, color, curses.tparm(set_fg_ansi, i).decode("utf8") or "")
        set_bg = self._tigetstr("setb")
        if set_bg:
            set_bg = set_bg.encode("utf8")
            for i, color in enumerate(self._LEGACY_COLORS):
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

    def __init__(self, term_stream: Optional[TextIO] = None):
        """
        Initialize terminal capabilities and size information.

        If the output stream is not a tty or terminal setup fails,
        the instance will have no terminal capabilities (all control
        attributes remain empty strings or None).

        :param term_stream: Output stream to probe for capabilities.
        :type term_stream: ``Optional[TextIO]``
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
        except BaseException as err:  # pylint: disable=broad-exception-caught
            # Preserve normal interruption/termination semantics.
            if isinstance(err, (KeyboardInterrupt, SystemExit)):  # pragma: no cover
                raise
            return  # pragma: no cover

        # Look up numeric capabilities.
        self.columns = curses.tigetnum("cols")
        self.lines = curses.tigetnum("lines")

        # Look up string capabilities.
        for capability in self._STRING_CAPABILITIES:
            (attr, cap_name) = capability.split(":")
            setattr(self, attr, self._tigetstr(cap_name) or "")

        # Colors
        self._init_colors()

    def _tigetstr(self, cap_name):
        # String capabilities can include "delays" of the form "$<2>".
        # For any modern terminal, we should be able to just ignore
        # these, so strip them out.
        cap = curses.tigetstr(cap_name)
        cap = cap.decode(encoding="utf8") if cap else ""
        return cap.split("$", maxsplit=1)[0]

    def render(self, template):
        """
        Replace each $-substitution with the corresponding control.

        Replace each $-substitution in the template string with the
        corresponding terminal control string (if defined) or an
        empty string (if not defined).

        :param template: Template string containing ${NAME} patterns.
        :type template: ``str``
        :returns: Rendered string with substitutions applied.
        :rtype: ``str``
        """
        return re.sub(r"\$\$|\${\w+}", self._render_sub, template)

    def _render_sub(self, match):
        s = match.group()
        if s == "$$":
            return "$"
        return getattr(self, s[2:-1], "")


def _flush_with_broken_pipe_guard(stream: TextIO) -> None:
    """
    Handle ``BrokenPipeError`` when attempting to flush output streams.

    :param stream: The stream to flush.
    :type stream: TextIO
    """
    if stream is None or not hasattr(stream, "flush"):
        return
    try:
        stream.flush()
    except BrokenPipeError as err:
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            if hasattr(stream, "fileno"):
                os.dup2(devnull, stream.fileno())
        finally:
            os.close(devnull)
        raise SystemExit() from err


class ProgressBase(ABC):
    """
    An abstract progress reporting class.
    """

    FIXED = -1

    def __init__(self, register: bool = True):
        """
        Initialize base progress state.

        Sets default state for lifecycle and rendering configuration.

        :param register: Register this ``ProgressBase`` for log callbacks.
        :type register: ``bool``
        """
        self.total: int = 0
        self.header: Optional[str] = None
        self.term: Optional[TermControl] = None
        self.stream: Optional[TextIO] = None
        self.width: int = -1
        self.first_update: bool = True
        self.registered: bool = False
        self.register: bool = register

    def reset_position(self):
        """Mark progress bar as displaced by external output."""
        self.first_update = True

    def _calculate_width(
        self, width: Optional[int] = None, width_frac: Optional[float] = None
    ) -> int:
        """
        Calculate the width for the progress bar using values defined by
        child classes. The ``header`` and (optionally, for classes that use it)
        ``term`` members must be initialised before calling this method.

        :type term: ``Optional[TermControl]``
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
        :raises ``ValueError``: If FIXED is negative, header is unset,
                                or both width and width_frac are specified.
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
        Begin a progress run with the specified ``total``.

        :param total: The total number of expected progress items.
        :type total: ``int``
        """
        if total <= 0:
            raise ValueError("total must be positive.")

        self.total = total

        if self.register:
            register_progress(self)

        self._do_start()

    @abstractmethod
    def _do_start(self):
        """
        Hook invoked when progress begins.

        Subclasses implement startup behavior such as rendering an
        initial progress display.
        """

    def _check_in_progress(self, done: int, step: str):
        """
        Validate that progress is active and ``done`` is in range.

        :param done: The number of completed progress items.
        :type done: ``int``
        :param step: The progress step (method name) that is active.
        :type step: ``str``
        :raises ``ValueError``: If progress has not started, if done is
                                negative, or if done exceeds total.
        """
        theclass = self.__class__.__name__
        if self.total == 0:
            raise ValueError(f"{theclass}.{step}() called before start()")

        if done < 0:
            raise ValueError(f"{theclass}.{step}() done cannot be negative.")

        if done > self.total:
            raise ValueError(f"{theclass}.{step}() done cannot be > total.")

    def progress(self, done: int, message: Optional[str] = None):
        """
        Advance the progress indicator to the specified ``done`` count.

        :param done: The number of completed progress items.
        :type done: ``int``
        :param message: An optional progress message.
        :type message: ``Optional[str]``
        """
        self._check_in_progress(done, "progress")
        self._do_progress(done, message)

    @abstractmethod
    def _do_progress(self, done: int, message: Optional[str] = None):
        """
        Hook for subclasses to update the progress display.

        :param done: The number of completed progress items.
        :type done: ``int``
        :param message: An optional progress message.
        :type message: ``Optional[str]``
        """

    def end(self, message: Optional[str] = None):
        """
        End the progress run and finalize the display.

        :param message: An optional completion message.
        :type message: ``Optional[str]``
        """
        self._check_in_progress(self.total, "end")
        self.progress(self.total, "")
        self._do_end(message)
        self.total = 0
        if self.registered:
            unregister_progress(self)

    @abstractmethod
    def _do_end(self, message: Optional[str] = None):
        """
        Perform final end-of-progress handling.

        This hook is called for both normal completion (via ``end()``)
        and error termination (via ``cancel()``/``_do_cancel()``).

        :param message: An optional completion message.
        :type message: ``Optional[str]``
        """

    def cancel(self, message: Optional[str] = None):
        """
        End the progress run with error and finalize the display.

        :param message: An optional error message.
        :type message: ``Optional[str]``
        """
        self._check_in_progress(self.total, "cancel")
        self._do_cancel(message=message)
        self.total = 0
        if self.registered:
            unregister_progress(self)

    def _do_cancel(self, message: Optional[str] = None):
        """
        Perform error case final end-of-progress handling.

        :param message: An optional error message.
        :type message: ``Optional[str]``
        """
        self._do_end(message=message)


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
        register: bool = True,
        term_stream: Optional[TextIO] = None,
        width: Optional[int] = None,
        width_frac: Optional[float] = None,
        no_clear: bool = False,
        tc: Optional[TermControl] = None,
    ):
        """
        Initialise a two-line terminal progress renderer.

        :param header: The progress header to display.
        :type header: ``str``
        :param register: Register this ``Progress`` for log callbacks.
        :type register: ``bool``
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
        :param no_clear: For progress indicators that clear and re-draw the
                         content on progress, do not erase the progress bar
                         when ``ProgressBase.end()`` is called, leaving it
                         on the terminal for the user to refer to. Ignored
                         by ``ProgressBase`` child classes that do not use
                         ``TerminalControl``.
        :type no_clear: ``bool``
        :param tc: An optional ``TermControl`` object already initialised with
                               a ``term_stream`` value. If this argument is set
                               it will override any ``term_stream`` argument.
        :type tc: ``Optional[TermControl]``
        :raises ValueError: If terminal lacks required capabilities.
        """
        super().__init__(register=register)

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
        self.first_update: bool = False
        columns = self.term.columns or DEFAULT_COLUMNS
        self.budget: int = max(MIN_BUDGET, columns - 10)

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
        Prepare rendering state for the progress bar.

        Sets up the rendered bar template and marks the progress as ready
        for its first update.
        """
        self.pbar = self.term.render(self.BAR)
        self.first_update = True

    def _do_progress(self, done: int, message: Optional[str] = None):
        """
        Update the two-line progress bar.

        :param done: The number of completed progress items.
        :type done: ``int``
        :param message: An optional progress message.
        :type message: ``Optional[str]``
        """
        message = message or ""
        percent = float(done) / float(self.total)
        n = int((self.width - 10) * percent)

        if self.first_update:
            prefix = self.term.HIDE_CURSOR + self.term.BOL
            self.first_update = False
        else:
            prefix = 2 * (self.term.BOL + self.term.UP + self.term.CLEAR_EOL)

        if len(message) > self.budget:
            message = message[0 : self.budget - 3] + "..."

        print(
            prefix
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
            + message.center(self.width)
            + "\n",
            file=self.stream,
            end="",
        )
        _flush_with_broken_pipe_guard(self.stream)

    def _do_end(self, message: Optional[str] = None):
        """
        End progress reporting on this ``Progress`` instance.

        :param message: An optional completion message.
        :type message: ``Optional[str]``
        """
        if not self.no_clear:
            print(
                2 * (self.term.BOL + self.term.UP + self.term.CLEAR_EOL),
                file=self.stream,
            )
            print(
                (
                    self.term.BOL
                    + self.term.CLEAR_EOL
                    + self.term.UP
                    + self.term.CLEAR_EOL
                    + self.term.SHOW_CURSOR
                ),
                file=self.stream,
                end="",
            )
        else:
            print(
                (
                    self.term.UP
                    + self.term.CLEAR_BOL
                    + self.term.BOL
                    + self.term.CLEAR_BOL
                    + self.term.BOL
                    + self.term.SHOW_CURSOR
                ),
                file=self.stream,
                end="",
            )
        if message:
            print(message, file=self.stream)
        _flush_with_broken_pipe_guard(self.stream)


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
        register: bool = True,
        term_stream: Optional[TextIO] = None,
        width: Optional[int] = None,
        width_frac: Optional[float] = None,
    ):
        """
        Initialise a new ``SimpleProgress`` object.

        :param header: The progress header to display.
        :type header: ``str``
        :param register: Register this ``SimpleProgress`` for log callbacks.
        :type register: ``bool``
        :param term_stream: The terminal stream to write to.
        :type term_stream: ``Optional[TextIO]``
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
        super().__init__(register=register)
        self.header: Optional[str] = header
        self.stream: Optional[TextIO] = term_stream or sys.stdout
        self.width: int = self._calculate_width(width=width, width_frac=width_frac)

    def _do_start(self):
        """
        Start reporting progress on this ``SimpleProgress`` object.

        No-op for ``SimpleProgress`` instances.
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
        n = int(self.width * percent)

        print(
            self.BAR
            % (
                self.header,
                percent * 100,
                self.DID * n,
                self.TODO * (self.width - n),
                message,
            ),
            file=self.stream,
        )
        _flush_with_broken_pipe_guard(self.stream)

    def _do_end(self, message: Optional[str] = None):
        """
        End progress reporting on this ``SimpleProgress`` instance.

        :param message: An optional completion message.
        :type message: ``Optional[str]``
        """
        if message:
            print(message, file=self.stream)

        _flush_with_broken_pipe_guard(self.stream)


class NullProgress(ProgressBase):
    """
    A progress class that produces no output.
    """

    def _do_start(self):
        """
        Start reporting progress on this ``NullProgress`` object.

        No-op for ``NullProgress`` instances.
        """
        return  # pragma: no cover

    def _do_progress(self, _done: int, _message: Optional[str] = None):
        """
        Report progress on this ``NullProgress`` instance.

        No-op for ``NullProgress`` instances.

        :param _done: The number of completed progress items.
        :type _done: ``int``
        :param _message: An optional progress message (unused).
        :type _message: ``Optional[str]``
        """
        return  # pragma: no cover

    def _do_end(self, _message: Optional[str] = None):
        """
        End progress reporting on this ``NullProgress`` instance.

        No-op for ``NullProgress`` instances.

        :param _message: An optional completion message (unused).
        :type _message: ``Optional[str]``
        """
        return  # pragma: no cover


class ThrobberBase(ABC):
    """
    An abstract busy indicator class. Unlike ``ProgressBase`` classes
    the throbber reports progress of a time consuming task where the
    total number of items is unknown (for instance, generating tree
    walk lists).
    """

    def __init__(self, header, register: bool = True):
        """
        Initialize base throbber state.

        Sets default state for lifecycle and rendering configuration.

        :param register: Register this ``ThrobberBase`` for log callbacks.
        :type register: ``bool``
        """
        self.header: str = header
        self.frames: str = r"."
        self.term: Optional[TermControl] = None
        self.stream: Optional[TextIO] = None
        self.started: bool = False
        self.first_update: bool = True
        self.nr_frames: int = len(self.frames)
        self.fps: int = DEFAULT_THROBBER_FPS
        self._frame_index: int = 0
        self._interval_us: int = round((1.0 / self.fps) * _USECS_PER_SEC)
        self._last: Optional[datetime] = None
        self.registered: bool = False
        self.register: bool = register

    def reset_position(self):
        """Mark throbber as displaced by external output."""
        self.first_update = True

    def start(self):
        """
        Begin a throbber run.
        """
        self.started = True
        self._last = datetime.now() - timedelta(microseconds=self._interval_us)

        if self.register:
            register_progress(self)

        self._do_start()
        self.throb()

    def _do_start(self):
        """
        Hook invoked when throbber begins.

        Subclasses implement startup behavior such as rendering an
        initial throbber display.
        """
        print(f"{self.header}: ..", end="", file=self.stream)

    def _check_started(self, step: str):
        """
        Validate that throbber is active.

        :param step: The throbber step (method name) that is active.
        :type step: ``str``
        :raises ``ValueError``: If throbber has not started, or if the throbber
                                has started but ``self._last`` is ``None``.
        """
        theclass = self.__class__.__name__
        if not self.started:
            raise ValueError(f"{theclass}.{step}() called before start()")
        if self._last is None:
            raise ValueError(
                f"{theclass}.{step}() invalid throbber state:"
                "self.started=True but self._last=None"
            )

    def throb(self):
        """
        Maintain liveness for this throbber and output frame if required.
        """
        self._check_started("throb")
        now = datetime.now()
        if (now - self._last).total_seconds() * _USECS_PER_SEC >= self._interval_us:
            self._do_throb()
            _flush_with_broken_pipe_guard(self.stream)
            self._last = now
            self._frame_index = (self._frame_index + 1) % self.nr_frames
            self.first_update = False

    @abstractmethod
    def _do_throb(self):
        """
        Hook for subclasses to update the throbber display.
        """

    def end(self, message: Optional[str] = None):
        """
        End the throbber run and finalize the display.

        :param message: An optional completion message.
        :type message: ``Optional[str]``
        """
        self._check_started("end")
        self._do_end(message=message)
        _flush_with_broken_pipe_guard(self.stream)
        self.started = False
        self._last = None
        if self.registered:
            unregister_progress(self)

    def _do_end(self, message: Optional[str] = None):
        """
        Perform final end-of-throbber handling.

        Subclasses that implement fancy throbbers with ``TermControl``
        should override this method to perform clearing/redrawing the
        throbber frame.

        :param message: An optional completion message.
        :type message: ``Optional[str]``
        """
        print(f"\n{message}" if message else "", file=self.stream)


class Throbber(ThrobberBase):
    """
    A ``ThrobberBase`` subclass to display a one line Unicode or ASCII
    throbber for capable terminals.
    """

    STYLES: ClassVar[Dict[str, Union[str, List[str]]]] = {
        "ascii": r"-\|/",
        "wave": "⠁⠂⠄⡀⢀⠠⠐⠈",
        "braillewave": "⣾⣽⣻⢿⡿⣟⣯⣷",
        "horizontalbars": "█▉▊▋▌▍▎▏▎▍▌▋▊▉█",
        "verticalbars": "▁▂▃▄▅▆▇█▇▆▅▄▃▂▁",
        "arrowspinner": "←↖↑↗→↘↓↙",
        "braillecircle": ["⢎⡰", "⢎⡡", "⢎⡑", "⢎⠱", "⠎⡱", "⢊⡱", "⢌⡱", "⢆⡱"],
        "bouncingball": [
            "[●     ]",
            "[ ●    ]",
            "[  ●   ]",
            "[   ●  ]",
            "[    ● ]",
            "[     ●]",
            "[    ● ]",
            "[   ●  ]",
            "[  ●   ]",
            "[ ●    ]",
            "[●     ]",
        ],
        "bouncingbar": [
            "[|     ]",
            "[ |    ]",
            "[  |   ]",
            "[   |  ]",
            "[    | ]",
            "[     |]",
            "[    | ]",
            "[   |  ]",
            "[  |   ]",
            "[ |    ]",
            "[|     ]",
        ],
    }

    def __init__(
        self,
        header: str,
        register: bool = True,
        style: Optional[str] = None,
        term_stream: Optional[TextIO] = None,
        no_clear: bool = False,
        tc: Optional[TermControl] = None,
    ):
        """
        Initialise a new one line throbber instance.

        :param header: The header string to print.
        :type header: ``str``
        :param register: Register this ``Throbber`` for log callbacks.
        :type register: ``bool``
        :param style: A Unicode throbber style string. Ignored if the terminal
                      does not support Unicode encoding. Defaults to "wave" if
                      unset.
        :type style: ``Optional[str]``
        :param term_stream: The terminal stream to write to.
        :type term_stream: ``TextIO``
        :param no_clear: For throbber indicators that clear and re-draw the
                         content on progress, do not erase the throbber frame
                         when ``ThrobberBase.end()`` is called, leaving it
                         on the terminal for the user to refer to. Ignored
                         by ``ThrobberBase`` child classes that do not use
                         ``TerminalControl``.
        :type no_clear: ``bool``
        :param tc: An optional ``TermControl`` object already initialised with
                               a ``term_stream`` value. If this argument is set
                               it will override any ``term_stream`` argument.
        :type tc: ``Optional[TermControl]``
        :raises ValueError: If terminal lacks required capabilities.
        """
        super().__init__(header, register=register)

        if style is not None and style not in Throbber.STYLES:
            raise ValueError(f"Unknown Throbber style: {style}")

        style = style or "wave"

        if tc is not None:
            term_stream = tc.term_stream

        self.term: Optional[TermControl] = tc or TermControl(term_stream=term_stream)
        self.stream: Optional[TextIO] = term_stream or sys.stdout
        self.no_clear = no_clear

        # Override default frames
        ascii_frames = self.STYLES["ascii"]
        encoding = getattr(self.stream, "encoding", None)
        if not encoding:
            # Stream has no usable encoding; fall back to ASCII frames.
            self.frames = ascii_frames
        else:
            try:
                unicode_frames = self.STYLES[style]
                if isinstance(unicode_frames, str):
                    unicode_frames.encode(encoding)
                else:
                    for frame in unicode_frames:
                        frame.encode(encoding)
                self.frames = unicode_frames
            except UnicodeEncodeError:
                self.frames = ascii_frames
        self.nr_frames = len(self.frames)

    def _do_start(self):
        """
        Just turn off the cursor: ``_do_throb()`` will print the header.
        """
        print(f"{self.term.HIDE_CURSOR}", end="", file=self.stream)

    def _do_throb(self):
        """
        Update the throbber frame.
        """
        if not self.first_update:
            # Erase previous throb frame.
            print(
                self.term.BOL + self.term.UP + self.term.CLEAR_EOL,
                end="",
                file=self.stream,
            )

        # Draw current throb frame.
        print(f"{self.header}: ", end="", file=self.stream)
        print(
            (
                f"{self.term.GREEN}"
                f"{self.frames[self._frame_index]}"
                f"{self.term.NORMAL}\n"
            ),
            end="",
            file=self.stream,
        )

    def _do_end(self, message: Optional[str] = None):
        """
        Finalise the throbber.
        """
        if not self.first_update and not self.no_clear:
            # Header length plus ": ".
            header_width = len(self.header) + 2
            # Erase previous throb frame.
            print(
                self.term.UP + header_width * self.term.RIGHT + self.term.CLEAR_EOL,
                end="",
                file=self.stream,
            )

        print(self.term.SHOW_CURSOR, end="", file=self.stream)

        print(f"{message}\n" if message else "", end="", file=self.stream)


class SimpleThrobber(ThrobberBase):
    """
    A simple throbber that does not rely on terminal capabilities.
    """

    def __init__(
        self, header: str, register: bool = True, term_stream: Optional[TextIO] = None
    ):
        """
        Initialise a simple ascii throbber.

        :param header: The throbber header.
        :type header: ``str``
        :param register: Register this ``SimpleThrobber`` for log callbacks.
        :type register: ``bool``
        :param term_stream: The terminal stream to write to.
        :type term_stream: ``Optional[TextIO]``
        """
        super().__init__(header, register=register)
        self.stream = term_stream or sys.stdout

    def _do_throb(self):
        """
        Output simple throb frame.
        """
        print(self.frames[self._frame_index], end="", file=self.stream)


class NullThrobber(ThrobberBase):
    """
    A throbber class that produces no output.
    """

    def _do_start(self):
        """No-op start for NullThrobber."""

    def _do_throb(self):
        """No-op throb hook for NullThrobber."""

    def _do_end(self, message: Optional[str] = None):
        """No-op end for NullThrobber."""

    def throb(self):
        """Silent throb for NullThrobber."""
        self._check_started("throb")

    def end(self, message: Optional[str] = None):
        """Silent end for NullThrobber."""
        self._check_started("end")
        self.started = False
        if self.registered:
            unregister_progress(self)


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
        register: bool = True,
    ) -> ProgressBase:
        """
        Return an appropriate ProgressBase implementation.

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
        :param register: Register the new object with the log system for
                         notification callbacks.
        :type register: ``bool``
        :returns: An appropriate progress implementation.
        :rtype: ``ProgressBase``
        """
        if term_control:
            term_stream = term_control.term_stream

        term_stream = term_stream or sys.stdout
        if quiet:
            progress = NullProgress(register=register)
        elif not hasattr(term_stream, "isatty") or not term_stream.isatty():
            progress = SimpleProgress(
                header,
                register=register,
                term_stream=term_stream,
                width=width,
                width_frac=width_frac,
            )
        else:
            progress = Progress(
                header,
                register=register,
                term_stream=term_stream,
                width=width,
                width_frac=width_frac,
                no_clear=no_clear,
                tc=term_control,
            )

        return progress

    @staticmethod
    def get_throbber_styles() -> List[str]:
        """
        Return a list of known ``Throbber`` style strings.

        :returns: A list of throbber styles.
        :rtype: ``List[str]``
        """
        return list(Throbber.STYLES.keys())

    @staticmethod
    def get_throbber(
        header: str,
        quiet: bool = False,
        style: Optional[str] = None,
        term_stream: Optional[TextIO] = None,
        term_control: Optional[TermControl] = None,
        no_clear: bool = False,
        register: bool = True,
    ) -> ThrobberBase:
        """
        Return an appropriate ThrobberBase implementation.

        :param header: The throbber header.
        :type header: ``str``
        :param quiet: Suppress all output.
        :type quiet: ``bool``
        :param style: An optional Unicode throbber style string. Ignored if the
                      terminal does not support Unicode encoding.
        :type style: ``Optional[str]``
        :param term_stream: An optional ``TextIO`` output object.
                            Defaults to ``sys.stdout`` if unspecified.
        :type term_stream: ``Optional[TextIO]``
        :param term_control: An optional ``TermControl`` object to use
                             for the throbber.
        :type term_control: ``Optional[TermControl]``
        :param no_clear: For throbber indicators that clear and re-draw the
                         content on throbber, do not erase the throbber bar
                         when ``ThrobberBase.end()`` is called, leaving it
                         on the terminal for the user to refer to. Ignored
                         by ``ThrobberBase`` child classes that do not use
                         ``TerminalControl``.
        :type no_clear: ``bool``
        :param register: Register the new object with the log system for
                         notification callbacks.
        :type register: ``bool``:
        :returns: An appropriate throbber implementation.
        :rtype: ``ThrobberBase``
        """
        if term_control:
            term_stream = term_control.term_stream

        term_stream = term_stream or sys.stdout
        if quiet:
            throbber = NullThrobber(header, register=register)
        elif not hasattr(term_stream, "isatty") or not term_stream.isatty():
            throbber = SimpleThrobber(
                header,
                register=register,
                term_stream=term_stream,
            )
        else:
            throbber = Throbber(
                header,
                register=register,
                style=style,
                term_stream=term_stream,
                no_clear=no_clear,
                tc=term_control,
            )

        return throbber


__all__ = [
    "NullProgress",
    "NullThrobber",
    "Progress",
    "ProgressBase",
    "ProgressFactory",
    "SimpleProgress",
    "SimpleThrobber",
    "TermControl",
    "ThrobberBase",
    "Throbber",
]
