# Copyright (C) 2023 Red Hat, Inc., Bryn M. Reeves <bmr@redhat.com>
#
# tests/__init__.py - Snapshot Manager
#
# This file is part of the snapm project.
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions
# of the GNU General Public License v.2.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
import os
import time

os.environ["TZ"] = "UTC"
time.tzset()


class MockArgs(object):
    identifier = None
    debug = None
    name = None
    name_prefixes = False
    no_headings = False
    options = ""
    sort = ""
    rows = False
    separator = None
    uuid = None
    verbose = 0
    version = False
