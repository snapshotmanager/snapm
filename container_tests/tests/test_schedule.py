import unittest
import logging
import os.path

import snapm
from snapm.manager._schedule import (
    Schedule,
    GcPolicy,
    GcPolicyType,
    GcPolicyParams,
)
from snapm.manager._calendar import CalendarSpec

log = logging.getLogger(__name__)


class ScheduleTests(unittest.TestCase):
    def test_schedule(self):
        pass
