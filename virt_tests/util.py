# Copyright Red Hat
#
# virt_tests/util.py - Snapshot Manager virt_tests utilities.
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Utilities for snapm virt-tests
"""
import sys


def log_print(*args):
    print(*args, flush=True)


def err_print(*args):
    print(*args, file=sys.stderr, flush=True)
