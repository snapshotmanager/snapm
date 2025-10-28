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
