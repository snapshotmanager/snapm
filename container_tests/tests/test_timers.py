import unittest
import logging

log = logging.getLogger(__name__)

class TimerTests(unittest.TestCase):
    def test_hello_world(self):
        # A test that always fails
        log.warn("Hello World!")
        self.assertTrue(False)
