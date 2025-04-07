# Copyright Red Hat
#
# snapm/manager/signals.py - Snapshot Manager signal handling
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Helpers for blocking and unblocking signal delivery.
"""
from signal import SIG_BLOCK, SIG_UNBLOCK, SIGINT, SIGTERM, pthread_sigmask
from functools import wraps
import logging

_log = logging.getLogger(__name__)
_log_debug = _log.debug

_to_block = {SIGINT, SIGTERM}


def block_signals():
    """
    Block termination signals before entering critical section.
    """
    _log_debug("Blocking termination signals %s", _to_block)
    pthread_sigmask(SIG_BLOCK, _to_block)


def unblock_signals():
    """
    Unblock and deliver termination signals after leaving critical section.
    """
    _log_debug("Unblocking termination signals %s", _to_block)
    pthread_sigmask(SIG_UNBLOCK, _to_block)


def suspend_signals(func):
    """
    Decorator to wrap functions that implement a critical section.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        block_signals()
        try:
            ret = func(*args, **kwargs)
        finally:
            unblock_signals()
        return ret

    return wrapper
