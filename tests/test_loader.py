# Copyright Red Hat
#
# tests/test_loader.py - Plugin loader tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
import logging
import os
import re


import boom
from snapm.manager._loader import load_plugins
from snapm.manager.plugins import Plugin

from tests import BOOT_ROOT_TEST

log = logging.getLogger()

boom.set_boot_path(BOOT_ROOT_TEST)


class LoaderTestsSimple(unittest.TestCase):
    """
    Test manager interfaces with mock callouts
    """

    _old_path = None

    def setUp(self):
        log.debug("Preparing %s", self._testMethodName)
        bin_path = os.path.abspath("tests/bin")
        cur_path = os.environ["PATH"]
        self._old_path = cur_path
        os.environ["PATH"] = bin_path + os.pathsep + cur_path

    def tearDown(self):
        log.debug("Tearing down %s", self._testMethodName)
        os.environ["PATH"] = self._old_path

    def test_load_plugins(self):
        plugin_classes = load_plugins()
        self.assertEqual(len(plugin_classes), 3)

    def test_load_plugins_returns_plugins(self):
        plugin_classes = load_plugins()
        self.assertTrue(isinstance(plugin_classes, list))
        self.assertTrue(all(issubclass(c, Plugin) for c in plugin_classes))

    def test_load_plugins_have_name_and_version(self):
        rx = re.compile(r"\d+\.\d+\.\d+")
        for plugin_cls in load_plugins():
            self.assertTrue(isinstance(plugin_cls.name, str))
            self.assertTrue(isinstance(plugin_cls.version, str))
            self.assertTrue(rx.match(plugin_cls.version) is not None)
