# Copyright Red Hat
#
# snapm/manager/__init__.py - Snapshot Manager
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Top level interface to the snapshot manager.
"""

from ._manager import Manager  # noqa: F401, F403
from ._calendar import CalendarSpec
from ._schedule import Schedule, GcPolicy

__all__ = [
    "Manager",
    "Schedule",
    "GcPolicy",
    "CalendarSpec",
]
