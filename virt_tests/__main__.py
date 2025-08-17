# Copyright Red Hat
#
# virt_tests/__main__.py - Snapshot Manager virt_tests CLI driver.
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
CLI interface for snapm end-to-end testing framework.
"""
from argparse import ArgumentParser
from os.path import basename
import sys
import os

from .strategy import run_e2e_test
from .testvm import STORAGE_LAYOUTS

if __name__ == "__main__":

    parser = ArgumentParser(description="snapm virt tests", prog="virt_tests")

    parser.add_argument(
        "base_os",
        metavar="BASE_OS",
        type=str,
        help="Operating system to install",
    )
    parser.add_argument(
        "repo",
        metavar="REPOSITORY",
        type=str,
        help="Repository under test",
    )
    parser.add_argument(
        "ref_name",
        metavar="REF_NAME",
        type=str,
        help="Ref under test",
    )
    parser.add_argument(
        "--storage",
        "-s",
        type=str,
        default="lvm",
        help="Storage layout",
        choices=STORAGE_LAYOUTS.keys(),
    )
    firmware_group = parser.add_mutually_exclusive_group()
    firmware_group.add_argument(
        "--uefi",
        action="store_true",
        default=False,
        help="Use UEFI boot firmware",
    )
    firmware_group.add_argument(
        "--bios",
        action="store_false",
        dest="uefi",
        help="Use BIOS boot firmware (default)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        default=False,
        help="Keep VM running at end of test",
    )

    args = parser.parse_args()

    base_os = args.base_os
    storage = args.storage
    vm_uefi = args.uefi
    vm_keep = args.keep
    repo = args.repo
    ref_name = args.ref_name
    vm_name = f"snapm-test-{base_os}-{os.getpid()}"

    success = run_e2e_test(
        vm_name,
        base_os=base_os,
        storage=storage,
        uefi=vm_uefi,
        keep=vm_keep,
        repo=repo,
        ref_name=ref_name,
    )

    sys.exit(0 if success else 1)
