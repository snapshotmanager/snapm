# Copyright Red Hat
#
# tests/__init__.py - Snapshot Manager test package
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import logging
import os
from os.path import abspath, join
import time

import snapm.manager.plugins as plugins

log = logging.getLogger()
log.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s')
file_handler = logging.FileHandler("test.log")
file_handler.setFormatter(formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
log.addHandler(file_handler)
log.addHandler(console_handler)

os.environ["TZ"] = "UTC"
time.tzset()

BOOT_ROOT_TEST = join(os.getcwd(), "tests/boot")


class MockArgs(object):
    identifier = None
    debug = None
    name = None
    new_name = None
    name_prefixes = False
    no_headings = False
    options = ""
    members = False
    sort = ""
    rows = False
    separator = None
    uuid = None
    verbose = 0
    version = False
    json = False
    autoindex = False
    schedule_name = None
    keep_count = 0
    keep_days = 0
    keep_weeks = 0
    keep_months = 0
    keep_years = 0
    keep_hourly = 0
    keep_weekly = 0
    keep_monthly = 0
    keep_quaterly = 0
    keep_yearly = 0


class MockPlugin(plugins.Plugin):
    """Minimal fulfillment of the ABC contract"""
    def discover_snapshots(self): return []
    def can_snapshot(self, _source): return False
    def check_create_snapshot(self, _origin, _snapset_name, _timestamp, _mount_point, _size_policy):
        raise NotImplementedError
    def create_snapshot(self, _origin, _snapset_name, _timestamp, _mount_point, _size_policy):
        raise NotImplementedError
    def rename_snapshot(self, _old_name, _origin, _snapset_name, _timestamp, _mount_point):
        raise NotImplementedError
    def check_resize_snapshot(self, _name, _origin, _mount_point, _size_policy):
        raise NotImplementedError
    def resize_snapshot(self, name, _origin, _mount_point, _size_policy):
        raise NotImplementedError
    def check_revert_snapshot(self, _name, _origin):
        raise NotImplementedError
    def revert_snapshot(self, _name):
        raise NotImplementedError
    def delete_snapshot(self, _name):
        raise NotImplementedError
    def activate_snapshot(self, _name):
        raise NotImplementedError
    def deactivate_snapshot(self, _name):
        raise NotImplementedError
    def set_autoactivate(self, _name, auto=False):
        raise NotImplementedError
    def origin_from_mount_point(self, _mount_point):
        raise NotImplementedError


def is_redhat():
    """
    Return True for Red Hat derivatives (excluding CentOS pending boom-boot #59).
    """
    return (os.path.exists("/etc/redhat-release")
            and not os.path.exists("/etc/centos-release"))


def have_root():
    """Return ``True`` if the test suite is running as the root user,
    and ``False`` otherwise.
    """
    return os.geteuid() == 0 and os.getegid() == 0


def in_rh_ci():
    """Return ``True`` if we appear to be running in CentOS/RHEL CI,
    and ``False`` otherwise.
    """
    return os.environ.get("IN_RH_CI", None) is not None
