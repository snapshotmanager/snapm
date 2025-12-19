# Copyright Red Hat
#
# snapm/fsdiff/fsdiffer.py - Snapshot Manager file system differ
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Top-level fsdiff interface.
"""
from typing import Optional, TYPE_CHECKING
import logging
import lzma

from snapm import SnapmError, SnapmNotFoundError
from snapm.progress import TermControl

from .cache import load_cache, save_cache
from .engine import DiffEngine, FsDiffResults
from .options import DiffOptions
from .treewalk import TreeWalker

if TYPE_CHECKING:
    from snapm.manager import Manager
    from snapm.manager._mounts import Mount


_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


class FsDiffer:
    """
    Top-level interface for generating file system comparisons.
    """

    def __init__(
        self,
        manager: "Manager",
        options: Optional[DiffOptions] = None,
        cache: bool = True,
        cache_expires: int = -1,
        color: str = "auto",
        term_control: Optional[TermControl] = None,
    ):
        """
        Initialise a new ``FsDiffer`` to compute file system differences.

        :param manager: The manager context for snapshot operations (used
                        by future methods).
        :type manager: ``Manager``
        :param options: Options to control this ``FsDiffer`` instance.
        :type options: ``DiffOptions``
        :param cache: Use diff cache if available.
        :type cache: ``bool``
        :param cache_expires: Cache expiry in seconds. Use 0 to disable cache
                        expiry or -1 to use the cache default (15m).
        :type cache_expires: ``int``
        :param color: A string to control color tree rendering: "auto",
                      "always", or "never".
        :type color: ``str``
        :param term_control: An optional ``TermControl`` instance to use for
                             formatting. The supplied instance overrides any
                             ``color`` argument if set.

        :type term_control: ``Optional[TermControl]``
        """
        options = options or DiffOptions()
        #: Manager context for snapshot operations (used by future methods)
        self.manager: "Manager" = manager
        self.options: DiffOptions = options
        self.cache = cache
        self.cache_expires = cache_expires
        self.tree_walker: TreeWalker = TreeWalker(options)
        self.diff_engine: DiffEngine = DiffEngine()
        self._term_control: Optional[TermControl] = term_control or TermControl(
            color=color
        )

    def compare_roots(self, mount_a: "Mount", mount_b: "Mount") -> FsDiffResults:
        """
        Compare two mounted snapshot sets and return diff results.

        :param mount_a: The first (left hand) mount to compare.
        :type mount_a: ``Mount``
        :param mount_b: The second (right hand) mount to compare.
        :type mount_b: ``Mount``
        :returns: The diff results for the comparison.
        :rtype: ``FsDiffResults``
        """
        try:
            _log_debug(
                "Attempting to load diff cache for %s/%s (cache=%s, expiry=%d)",
                mount_a.name,
                mount_b.name,
                self.cache,
                self.cache_expires,
            )
            cached_results = (
                load_cache(
                    mount_a,
                    mount_b,
                    self.options,
                    expires=self.cache_expires,
                    quiet=self.options.quiet,
                    term_control=self._term_control,
                )
                if self.cache
                else None
            )
            if cached_results is not None:
                _log_debug(
                    "Loaded diff cache for %s/%s: %s",
                    mount_a.name,
                    mount_b.name,
                    cached_results,
                )
                return cached_results
        except SnapmNotFoundError:
            _log_debug("No diff cache found for %s/%s", mount_a.name, mount_b.name)
        except SnapmError as err:
            _log_info(
                "Failed to load diff cache for %s/%s: %s",
                mount_a.name,
                mount_b.name,
                err,
            )

        strip_prefix_a = "" if mount_a.root == "/" else mount_a.root
        tree_a = self.tree_walker.walk_tree(
            mount_a,
            strip_prefix=strip_prefix_a,
            quiet=self.options.quiet,
            term_control=self._term_control,
        )

        strip_prefix_b = "" if mount_b.root == "/" else mount_b.root
        tree_b = self.tree_walker.walk_tree(
            mount_b,
            strip_prefix=strip_prefix_b,
            quiet=self.options.quiet,
            term_control=self._term_control,
        )

        results = self.diff_engine.compute_diff(
            tree_a, tree_b, self.options, self._term_control
        )
        if self.cache:
            try:
                save_cache(
                    mount_a,
                    mount_b,
                    results,
                    quiet=self.options.quiet,
                    term_control=self._term_control,
                )
            except (OSError, lzma.LZMAError) as err:
                _log_info(
                    "Failed to save diff cache for %s/%s: %s",
                    mount_a.name,
                    mount_b.name,
                    err,
                )
        return results
