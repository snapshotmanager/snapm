# Copyright Red Hat
#
# snapm/manager/plugins/lvm2.py - Snapshot Manager LVM2 plugins
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
LVM2 snapshot manager plugins
"""
from os.path import exists as path_exists, join as path_join, isabs as path_isabs
from os import stat, major as dev_major, environ
from subprocess import run, CalledProcessError
from json import loads, JSONDecodeError
from math import floor
from stat import S_ISBLK
from time import time
from shutil import which

from snapm import (
    SnapmInvalidIdentifierError,
    SnapmSizePolicyError,
    SnapmCalloutError,
    SnapmBusyError,
    SnapmNoSpaceError,
    SnapmNotFoundError,
    SnapmPluginError,
    SnapmLimitError,
    SizePolicy,
    SnapStatus,
    Snapshot,
    size_fmt,
)
from snapm.manager.plugins import (
    DEV_PREFIX,
    DEV_MAPPER_PREFIX,
    DMSETUP_CMD,
    DMSETUP_INFO,
    DMSETUP_NO_HEADINGS,
    DMSETUP_COLUMNS,
    DMSETUP_FIELDS_UUID,
    PLUGIN_NO_PRIORITY,
    Plugin,
    parse_snapshot_name,
    device_from_mount_point,
    mount_point_space_used,
    format_snapshot_name,
    encode_mount_point,
)

#: Maximum length for LVM2 LV names
LVM_MAX_NAME_LEN = 127
#: Length of extension for LVM2 CoW snapshot LV name
LVM_COW_SNAPSHOT_NAME_LEN = 4

# Global LVM2 report options
LVM_REPORT_FORMAT = "--reportformat"
LVM_JSON = "json"
LVM_JSON_STD = "json_std"
LVM_OPTIONS = "--options"
LVM_UNITS = "--units"
LVM_BYTES = "b"
LVM_UUID_PREFIX = "LVM-"

# Main lvm executable
LVM_CMD = "lvm"

# Version subcommand
LVM_VERSION = "version"

# LVM version string prefix
LVM_VERSION_STR = "LVM version"

# lvs report options
LVS_CMD = "lvs"
LVS_REPORT = "report"
LVS_ALL = "--all"
LVS_LV = "lv"
LVS_LV_NAME = "lv_name"
LVS_VG_NAME = "vg_name"
LVS_LV_ATTR = "lv_attr"
LVS_LV_ROLE = "lv_role"
LVS_LV_ORIGIN = "origin"
LVS_POOL_LV = "pool_lv"
LVS_LV_SIZE = "lv_size"
LVS_DATA_PERCENT = "data_percent"
LVS_LV_HIDDEN_START = "["
LVS_LV_HIDDEN_END = "]"
LVS_FIELD_OPTIONS = (
    "vg_name,lv_name,lv_attr,origin,pool_lv,lv_size,data_percent,lv_role"
)

# lvs options for devpath to vg/lv name
LVS_FIELD_MIN_OPTIONS = "vg_name,lv_name"
LVS_NO_HEADINGS = "--noheadings"


# lv_attr flag values
LVM_COW_SNAP_ATTR = "s"
LVM_MERGE_SNAP_ATTR = "S"
LVM_THIN_VOL_ATTR = "V"
LVM_COW_ORIGIN_ATTR = "o"
LVM_ACTIVE_ATTR = "a"
LVM_INVALID_ATTR = "I"
LVM_LV_TYPE_DEFAULT = "-"
LVM_LV_ORIGIN_MERGING = "O"
LVM_SKIP_ACTIVATION_ATTR = "k"

# lv_attr flag indexes
LVM_LV_STATE_ATTR_IDX = 4
LVM_LV_SKIP_ACTIVATION_IDX = 9

# lv_role strings
LVM_COW_SNAPSHOT_ROLE = "thicksnapshot"
LVM_THIN_SNAPSHOT_ROLE = "thinsnapshot"

# vgs report options
VGS_CMD = "vgs"
VGS_REPORT = "report"
VGS_VG = "vg"
VGS_FIELD_OPTIONS = "vg_name,vg_free,vg_extent_size"

# lvcreate command options
LVCREATE_CMD = "lvcreate"
LVCREATE_SNAPSHOT = "--snapshot"
LVCREATE_NAME = "--name"
LVCREATE_SIZE = "--size"

# lvremove command options
LVREMOVE_CMD = "lvremove"
LVREMOVE_YES = "--yes"

# lvrename command
LVRENAME_CMD = "lvrename"

# lvchange command
LVCHANGE_CMD = "lvchange"

# lvchange command options
LVCHANGE_ACTIVATE = "--activate"
LVCHANGE_ACTIVE_YES = "y"
LVCHANGE_ACTIVE_NO = "n"
LVCHANGE_YES = "--yes"
LVCHANGE_IGNOREACTIVATIONSKIP = "--ignoreactivationskip"
LVCHANGE_SETACTIVATIONSKIP = "--setactivationskip"
LVCHANGE_ACTIVATIONSKIP_YES = "y"
LVCHANGE_ACTIVATIONSKIP_NO = "n"

# lvconvert command
LVCONVERT_CMD = "lvconvert"

# lvconvert command options
LVCONVERT_MERGE = "--merge"

# lvresize command
LVRESIZE_CMD = "lvresize"

# Minimum possible LVM2 CoW snapshot size (512MiB)
MIN_LVM2_COW_SNAPSHOT_SIZE = 512 * 1024**2

# Maximum time to cache lvs data in seconds
LVS_CACHE_VALID = 5

# LVM commands required by the plugin
_LVM_CMDS = [
    DMSETUP_CMD,
    LVM_CMD,
    LVS_CMD,
    VGS_CMD,
    LVCREATE_CMD,
    LVREMOVE_CMD,
    LVRENAME_CMD,
    LVCHANGE_CMD,
    LVCONVERT_CMD,
]

MINIMUM_LVM_VERSION = (2, 3, 11)
MINIMUM_LVM_VERSION_JSON_STD = (2, 3, 17)

#: Lvm2Cow Static Priority
LVM2_COW_STATIC_PRIORITY = 10

#: Lvm2Thin Static Priority
LVM2_THIN_STATIC_PRIORITY = 15

# LVM2 environment variables to filter out
_LVM_ENV_FILTER = [
    "LVM_OUT_FD",
    "LVM_ERR_FD",
    "LVM_REPORT_FD",
    "LVM_COMMAND_PROFILE",
    "LVM_RUN_BY_DMEVENTD",
    "LVM_SUPPRESS_FD_WARNINGS",
    "LVM_SUPPRESS_SYSLOG",
    "LVM_VG_NAME",
    "LVM_LVMPOLLD_PIDFILE",
    "LVM_LVMPOLLD_SOCKET",
    "LVM_LOG_FILE_EPOCH",
    "LVM_LOG_FILE_MAX_LINES",
    "LVM_EXPECTED_EXIT_STATUS",
    "LVM_SUPPRESS_LOCKING_FAILURE_MESSAGES",
    "DM_ABORT_ON_INTERNAL_ERRORS",
    "DM_DISABLE_UDEV",
    "DM_DEBUG_WITH_LINE_NUMBERS",
]

_dm_major: int = 0


def _get_dm_major() -> int:
    """
    Return the device-mapper major device number for the running
    system.

    :returns: The device-mapper major number as an int, or zero if
              device-mapper could not be found in ``/proc/devices``.
    """
    global _dm_major  # pylint: disable=global-statement

    if _dm_major:
        return _dm_major

    major: int = 0
    try:
        with open("/proc/devices", "r", encoding="utf8") as fp:
            in_block = False
            for raw in fp:
                line = raw.strip()
                if line.startswith("Block devices:"):
                    in_block = True
                    continue
                if not in_block or not line:
                    continue
                parts = line.split(maxsplit=1)
                if len(parts) != 2:
                    continue
                major_str, name = parts
                if name.strip() == "device-mapper":
                    try:
                        major = int(major_str)
                    except ValueError:
                        return 0
                    if major:
                        _dm_major = major
                        return _dm_major
    except OSError:
        return 0
    return 0


def _round_up_extents(size_bytes: int, extent_size: int) -> int:
    """
    Round up ``size_bytes`` to the next ``extent_size`` boundary.

    :param size_bytes: An arbitrary size in bytes.
    :param extent_size: An extent size.
    :returns: The given size rounded up to the next extent size boundary.
    """
    if not extent_size or extent_size < 0 or not isinstance(extent_size, int):
        raise ValueError("extent_size must be a positive integer")

    if size_bytes < 0 or not isinstance(size_bytes, int):
        raise ValueError("size_bytes must be a non-negative integer")

    return ((size_bytes + extent_size - 1) // extent_size) * extent_size


def _decode_stderr(err):
    """
    Decode and strip the stderr member of a ``CalledProcessError`` and
    return the result as a string.

    :param err: A ``CalledProcessError`` like exception.
    :returns: A stripped string representation of the exception's stderr
              member.
    """
    return err.stderr.decode("utf8").strip()


def _check_lvm_present():
    """
    Check for the presence of the required LVM2 commands and device-mapper
    support.

    :raises: ``SnapmNotFoundError`` if required dependencies are not found.
    """
    if not all(which(cmd) for cmd in _LVM_CMDS):
        raise SnapmNotFoundError("LVM2 commands not found")

    if not _get_dm_major():
        raise SnapmNotFoundError("device-mapper not found")


def vg_lv_from_origin(origin):
    """
    Return a ``(vg_name, lv_name)`` tuple for the LVM device with origin
    path ``origin``.
    """
    name_parts = origin.removeprefix(DEV_PREFIX + "/").split("/")
    return (name_parts[0], name_parts[1])


class Lvm2Snapshot(Snapshot):
    """
    Class for LVM2 snapshot objects.
    """

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        name,
        snapset_name,
        origin,
        timestamp,
        mount_point,
        provider,
        vg_name,
        lv_name,
        lv_dict=None,
    ):
        super().__init__(name, snapset_name, origin, timestamp, mount_point, provider)
        self.vg_name = vg_name
        self.lv_name = lv_name
        if lv_dict:
            self._lv_dict_cache = lv_dict
        else:
            lvs_dict = self.provider.get_lvs_json_report(
                f"{self.vg_name}/{self.lv_name}"
            )
            self._lv_dict_cache = lvs_dict[LVS_REPORT][0][LVS_LV][0]
        self._lv_dict_cache_ts = time()

    def __str__(self):
        return "".join(
            [
                super().__str__(),
                f"\nVolumeGroup:    {self.vg_name}",
                f"\nLogicalVolume:  {self.lv_name}",
            ]
        )

    @property
    def origin(self):
        return path_join(DEV_PREFIX, self.vg_name, self._origin)

    @property
    def origin_options(self):
        """
        File system options needed to specify the origin of this snapshot.

        No file system options are requires for LVM2 block snapshots: always
        return the empty string.
        """
        return ""

    def _devpath(self):
        return path_join(DEV_PREFIX, self.vg_name, self.lv_name)

    @property
    def devpath(self):
        if self.status != SnapStatus.ACTIVE:
            return ""
        return self._devpath()

    @property
    def status(self):
        lv_dict = self._get_lv_dict_cache()
        lv_attr = lv_dict[LVS_LV_ATTR]
        if lv_attr[LVM_LV_STATE_ATTR_IDX] == LVM_INVALID_ATTR:
            return SnapStatus.INVALID
        if lv_attr.startswith(LVM_MERGE_SNAP_ATTR):
            return SnapStatus.REVERTING
        if lv_attr[LVM_LV_STATE_ATTR_IDX] == LVM_ACTIVE_ATTR:
            return SnapStatus.ACTIVE
        return SnapStatus.INACTIVE

    @property
    def size(self):
        lv_dict = self._get_lv_dict_cache()
        return int(lv_dict[LVS_LV_SIZE].rstrip("B"))

    @property
    def free(self):
        raise NotImplementedError

    # Pylint does not understand the decorator notation.
    # pylint: disable=invalid-overridden-method
    @Snapshot.autoactivate.getter
    def autoactivate(self):
        lv_dict = self._get_lv_dict_cache()
        lv_attr = lv_dict[LVS_LV_ATTR]
        if lv_attr[LVM_LV_SKIP_ACTIVATION_IDX] == LVM_SKIP_ACTIVATION_ATTR:
            return False
        return True

    def invalidate_cache(self):
        self._lv_dict_cache = None
        self._lv_dict_cache_ts = 0

    def _get_lv_dict_cache(self):
        now = time()
        if not self._lv_dict_cache or (self._lv_dict_cache_ts + LVS_CACHE_VALID) < now:
            lvs_dict = self.provider.get_lvs_json_report(
                f"{self.vg_name}/{self.lv_name}"
            )
            self._lv_dict_cache = lvs_dict[LVS_REPORT][0][LVS_LV][0]
            self._lv_dict_cache_ts = now
        return self._lv_dict_cache


class Lvm2CowSnapshot(Lvm2Snapshot):
    """
    Class for LVM2 copy-on-write snapshot objects.
    """

    @property
    def free(self):
        lv_dict = self._get_lv_dict_cache()
        lv_data_percent = float(lv_dict[LVS_DATA_PERCENT])
        return int(((100.0 - lv_data_percent) * self.size) / 100.0)


class Lvm2ThinSnapshot(Lvm2Snapshot):
    """
    Class for LVM2 thin snapshot objects.
    """

    @property
    def pool(self):
        """
        Return the pool name for this ``Lvm2ThinSnapshot``.
        """
        lv_dict = self._get_lv_dict_cache()
        return lv_dict[LVS_POOL_LV]

    @property
    def free(self):
        lv_dict = self._get_lv_dict_cache()
        return self.provider.pool_free_space(self.vg_name, lv_dict[LVS_POOL_LV])


def _volume_type_or_merging(lv_dict, lv_type):
    """
    Return ``True`` if the logical volume represented by ``lv_dict`` is either
    the specified ``lv_type``, or a merging snapshot volume, or ``False``
    otherwise.
    """
    if lv_dict[LVS_LV_ATTR].startswith(lv_type):
        return True
    if lv_dict[LVS_LV_ATTR].startswith(LVM_MERGE_SNAP_ATTR):
        return True
    return False


def filter_cow_snapshot(lv_dict):
    """
    Filter LVM2 CoW snapshots.

    Return ``True`` if the logical volume represented by ``lv_dict`` is an LVM2
    COW snapshot or ``False`` otherwise. The ``lv_dict`` argument must be a
    dictionary representing the output of the ``lvs`` reporting command.
    """
    if not _volume_type_or_merging(lv_dict, LVM_COW_SNAP_ATTR):
        return False
    if LVM_COW_SNAPSHOT_ROLE not in lv_dict[LVS_LV_ROLE]:
        return False
    if lv_dict[LVS_LV_ORIGIN] == "":
        return False
    return True


def filter_thin_snapshot(lv_dict):
    """
    Filter LVM2 thin snapshots.

    Return ``True`` if the logical volume represented by ``lv_dict`` is an LVM2
    thin snapshot or ``False`` otherwise. The ``lv_dict`` argument must be a
    dictionary representing the output of the ``lvs`` reporting command.
    """
    if not _volume_type_or_merging(lv_dict, LVM_THIN_VOL_ATTR):
        return False
    if LVM_THIN_SNAPSHOT_ROLE not in lv_dict[LVS_LV_ROLE]:
        return False
    if lv_dict[LVS_LV_ORIGIN] == "":
        return False
    return True


class _Lvm2(Plugin):
    """
    Abstract base class for LVM2 snapshot plugins.
    """

    max_name_len = LVM_MAX_NAME_LEN

    def _run(
        self,
        *popenargs,
        # subprocess.run() hits the same pylint warning.
        input=None,  # pylint: disable=redefined-builtin
        capture_output=False,
        timeout=None,
        check=False,
        **kwargs,
    ):
        """
        Thin wrapper around ``subprocess.run`` to enforce lvm environment
        sanitization.

        Refer to the function documentation for the ``run`` function for a full
        description of arguments and keyword arguments: ``_Lvm2.run()`` behaves
        identically, other than setting the ``env`` keyword argument to the
        value of ``self._env``.

        Callers can still pass ``env`` to ``_run()`` calls: in this case
        caller's ``env`` value is merged with the dictionary passed to the
        underlying ``run()`` call, potentially overriding the values contained
        in ``self._env``.
        """
        kwargs["env"] = self._env | kwargs["env"] if "env" in kwargs else self._env
        return run(
            *popenargs,
            input=input,
            capture_output=capture_output,
            timeout=timeout,
            check=check,
            **kwargs,
        )

    def _is_lvm_device(self, device):
        """
        Test whether ``device`` is an LVM device.

        Return ``True`` if the device at ``device`` is an LVM device or
        ``False`` otherwise.
        """

        def _check_dm_major(dev):
            st = stat(dev, follow_symlinks=True)
            if not S_ISBLK(st.st_mode):
                return False
            if dev_major(st.st_rdev) != _get_dm_major():
                return False
            return True

        if path_isabs(device):
            if not path_exists(device):
                return False
            if not _check_dm_major(device):
                return False
        else:
            check_path = path_join(DEV_MAPPER_PREFIX, device)
            if not path_exists(check_path):
                return False
            if not _check_dm_major(check_path):
                return False

        if device.startswith(DEV_MAPPER_PREFIX):
            dm_name = device.removeprefix(DEV_MAPPER_PREFIX)
        else:
            dm_name = device

        dmsetup_cmd_args = [
            DMSETUP_CMD,
            DMSETUP_INFO,
            DMSETUP_COLUMNS,
            DMSETUP_NO_HEADINGS,
            DMSETUP_FIELDS_UUID,
            dm_name,
        ]
        try:
            dmsetup_cmd = self._run(dmsetup_cmd_args, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"Error calling {DMSETUP_CMD}: {_decode_stderr(err)}"
            ) from err
        uuid = dmsetup_cmd.stdout.decode("utf8").strip()
        return uuid.startswith(LVM_UUID_PREFIX)

    def _get_lvm_version(self):
        """
        Return the installed version of LVM2 as a tuple.

        :returns: A version tuple (major, minor, patch) of LVM2
        """

        def _version_string_to_tuple(version):
            return tuple(map(int, version.split(".")))

        lvm_cmd_args = [LVM_CMD, LVM_VERSION]
        lvm_cmd = self._run(lvm_cmd_args, capture_output=True, check=True)
        lvm_version_info = str.strip(lvm_cmd.stdout.decode("utf8"))
        for line in lvm_version_info.splitlines():
            if LVM_VERSION_STR in line:
                (_, _, version, _) = line.split()
                if "(" in version:
                    version, _ = version.split("(", maxsplit=1)
                return _version_string_to_tuple(version)
        return (0, 0, 0)

    def vg_lv_from_device_path(self, devpath):
        """
        Return a ``(vg_name, lv_name)`` tuple for the LVM device at
        ``devpath``.
        """
        lvs_cmd_args = [
            LVS_CMD,
            LVS_NO_HEADINGS,
            LVM_REPORT_FORMAT,
            self._json_fmt,
            LVM_OPTIONS,
            LVS_FIELD_MIN_OPTIONS,
            devpath,
        ]
        try:
            lvs_cmd = self._run(lvs_cmd_args, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"Error calling {LVS_CMD}: {_decode_stderr(err)}"
            ) from err
        lvs_dict = loads(lvs_cmd.stdout)
        lv_dict = lvs_dict[LVS_REPORT][0][LVS_LV][0]
        return (lv_dict["vg_name"], lv_dict["lv_name"])

    def pool_name_from_vg_lv(self, vg_lv):
        """
        Return the thin pool associated with the logical volume identified by
        ``vg_lv``.
        """
        lvs_dict = self.get_lvs_json_report(vg_lv)
        lv_dict = lvs_dict[LVS_REPORT][0][LVS_LV][0]
        return lv_dict[LVS_POOL_LV]

    def vg_free_space(self, vg_name):
        """
        Return a tuple of the free space available as bytes and the volume
        group extent size for the volume group named ``vg_name``.

        :param vg_name: The name of the volume group to check.
        :returns: A 2-tuple ``(vg_free: int, vg_extent_size: int)``.
        """
        vgs_dict = self.get_vgs_json_report(vg_name=vg_name)
        for vg_dict in vgs_dict[VGS_REPORT][0][VGS_VG]:
            if vg_dict["vg_name"] == vg_name:
                vg_free = int(vg_dict["vg_free"].rstrip("B"))
                vg_extent_size = int(vg_dict["vg_extent_size"].rstrip("B"))
                return (vg_free, vg_extent_size)
        raise ValueError(f"Volume group {vg_name} not found")

    def lv_dev_size(self, vg_name, lv_name):
        """
        Return the size of the specified logical volume in bytes.
        """
        lvs_dict = self.get_lvs_json_report(f"{vg_name}/{lv_name}")
        for lv_dict in lvs_dict[LVS_REPORT][0][LVS_LV]:
            if lv_dict["vg_name"] == vg_name and lv_dict["lv_name"] == lv_name:
                return int(lv_dict["lv_size"].rstrip("B"))
        raise ValueError(f"Logical volume {vg_name}/{lv_name} not found")

    def pool_free_space(self, vg_name, pool_name):
        """
        Return the free space available as bytes for the thin pool identified
        by ``vg_name`` and ``pool_name``.
        """
        lvs_dict = self.get_lvs_json_report(f"{vg_name}/{pool_name}")
        lv_dict = lvs_dict[LVS_REPORT][0][LVS_LV][0]
        data_percent = float(lv_dict[LVS_DATA_PERCENT])
        pool_size = int(lv_dict[LVS_LV_SIZE].rstrip("B"))
        return int(pool_size - floor((pool_size * data_percent) / 100.0))

    def get_lvs_json_report(self, vg_lv=None, lvs_all=False):
        """
        Call out to the ``lvs`` program and return a report in JSON format.
        """
        lvs_cmd_args = [
            LVS_CMD,
            LVM_REPORT_FORMAT,
            self._json_fmt,
            LVM_UNITS,
            LVM_BYTES,
            LVM_OPTIONS,
            LVS_FIELD_OPTIONS,
        ]
        if vg_lv:
            lvs_cmd_args.append(vg_lv)
        if lvs_all:
            lvs_cmd_args.append(LVS_ALL)
        try:
            lvs_cmd = self._run(lvs_cmd_args, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"Error calling {LVS_CMD}: {_decode_stderr(err)}"
            ) from err
        try:
            lvs_dict = loads(lvs_cmd.stdout)
        except JSONDecodeError as err:
            raise SnapmCalloutError(
                f"Unable to decode {LVS_CMD} JSON output: {err}"
            ) from err
        return lvs_dict

    def get_vgs_json_report(self, vg_name=None):
        """
        Call out to the ``vgs`` program and return a report in JSON format.
        """
        vgs_cmd_args = [
            VGS_CMD,
            LVM_REPORT_FORMAT,
            self._json_fmt,
            LVM_UNITS,
            LVM_BYTES,
            LVM_OPTIONS,
            VGS_FIELD_OPTIONS,
        ]
        if vg_name:
            vgs_cmd_args.append(vg_name)
        try:
            vgs_cmd = self._run(vgs_cmd_args, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"Error calling {VGS_CMD}: {_decode_stderr(err)}"
            ) from err
        try:
            vgs_dict = loads(vgs_cmd.stdout)
        except JSONDecodeError as err:
            raise SnapmCalloutError(
                f"Unable to decode {VGS_CMD} JSON output: {err}"
            ) from err
        return vgs_dict

    def _check_lvm_version(self):
        """
        Check for the required minimum LVM2 version and enable any version
        dependent workarounds as needed.
        """

        def _version_string(value):
            return f"{value[0]}.{value[1]}.{value[2]}"

        try:
            lvm_version = self._get_lvm_version()
        except CalledProcessError as err:
            raise SnapmPluginError(
                f"Error getting LVM2 version: {_decode_stderr(err)}"
            ) from err
        if lvm_version < MINIMUM_LVM_VERSION:
            raise SnapmPluginError(
                f"Unsupported LVM2 version: {_version_string(lvm_version)} "
                f"< {_version_string(MINIMUM_LVM_VERSION)}"
            )

        # Work around missing --reportformat json_std in ubuntu-24.04
        # See https://github.com/snapshotmanager/snapm/issues/110
        if lvm_version < MINIMUM_LVM_VERSION_JSON_STD:
            self._json_fmt = LVM_JSON

    def _sanitize_environment(self):
        env = environ.copy()
        for var in _LVM_ENV_FILTER:
            if var in env:
                env.pop(var)
        return env

    def __init__(self, logger, plugin_cfg):
        super().__init__(logger, plugin_cfg)

        # Default to using json_std when available.
        self._json_fmt = LVM_JSON_STD

        # Sanitize environment for LVM2 callouts.
        self._env = self._sanitize_environment()

        # Export LC_ALL=C
        self._env["LC_ALL"] = "C"

        # Check for presence of required LVM2 binaries and device-mapper.
        _check_lvm_present()

        # Check LVM2 minimum version requirements.
        self._check_lvm_version()

    def _activate(self, active, name, silent=False):
        """
        Call lvchange to activate or deactivate an LVM2 volume.

        :param name: The name of the LV to operate on.
        :param silent: ``True`` if errors should not be propagated or
                       ``False`` otherwise.
        """
        lvchange_cmd = [
            LVCHANGE_CMD,
            LVCHANGE_YES,
            LVCHANGE_IGNOREACTIVATIONSKIP,
            LVCHANGE_ACTIVATE,
            active,
            name,
        ]
        try:
            self._run(lvchange_cmd, capture_output=True, check=True)
        except CalledProcessError as err:
            if not silent:
                raise SnapmCalloutError(
                    f"{LVCHANGE_CMD} failed with: {_decode_stderr(err)}"
                ) from err

    def discover_snapshots(self):
        """
        Discover snapshots managed by this plugin class.

        Returns a list of objects that are a subclass of ``Snapshot``.
        """
        raise NotImplementedError

    def can_snapshot(self, source):
        """
        Test whether this plugin can snapshot the specified block device or
        mount point path.

        :param source: The mount point or block device path to test.
        :returns: ``True`` if this plugin can snapshot the file system or
                  block device at ``source``, or ``False`` otherwise.
        """
        raise NotImplementedError

    # pylint: disable=too-many-arguments
    def check_create_snapshot(
        self, origin, snapset_name, timestamp, mount_point, size_policy
    ):
        """
        Perform pre-creation checks before creating a snapshot.

        :param origin: The origin volume for the snapshot.
        :param snapset_name: The name of the snapshot set to be created.
        :param timestamp: The snapshot set timestamp.
        :param mount_point: The mount point path for this snapshot.
        :raises: ``SnapmNoSpaceError`` if there is insufficient free space to
                 create the snapshot.
        """
        raise NotImplementedError

    # pylint: disable=too-many-arguments
    def create_snapshot(
        self, origin, snapset_name, timestamp, mount_point, size_policy
    ):
        """
        Create a snapshot of ``origin`` in the snapset named ``snapset_name``.

        :param origin: The origin volume for the snapshot.
        :param snapset_name: The name of the snapshot set to be created.
        :param timestamp: The snapshot set timestamp.
        :param mount_point: The mount point path for this snapshot.
        :raises: ``SnapmNoSpaceError`` if there is insufficient free space to
                 create the snapshot.
        """
        raise NotImplementedError

    def origin_from_mount_point(self, mount_point):
        """
        Return a string representing the origin from a given mount point path.
        """
        if S_ISBLK(stat(mount_point).st_mode):
            device = mount_point
        else:
            device = device_from_mount_point(mount_point)
        if not self._is_lvm_device(device):
            return None
        (vg_name, lv_name) = self.vg_lv_from_device_path(device)
        return path_join(DEV_PREFIX, vg_name, lv_name)

    def _delete_snapshot(self, name):
        """
        Delete the snapshot named ``name``

        :param name: The name of the snapshot to be removed.
        """
        lvremove_cmd = [LVREMOVE_CMD, LVREMOVE_YES, name]
        try:
            self._run(lvremove_cmd, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"{LVREMOVE_CMD} failed with: {_decode_stderr(err)}"
            ) from err

    # pylint: disable=too-many-arguments
    def rename_snapshot(self, old_name, origin, snapset_name, timestamp, mount_point):
        """
        Rename the snapshot named ``old_name`` according to the provided
        snapshot field values.

        :param old_name: The original name of the snapshot to be renamed.
        :param origin: The origin volume for the snapshot.
        :param snapset_name: The new name of the snapshot set.
        :param timestamp: The snapshot set timestamp.
        :param mount_point: The mount point of the snapshot.
        """
        vg_name, lv_name = vg_lv_from_origin(origin)
        new_name = format_snapshot_name(
            lv_name, snapset_name, timestamp, encode_mount_point(mount_point)
        )

        self._log_debug(
            "Renaming snapshot from %s to %s",
            old_name,
            new_name,
        )

        lvrename_cmd = [
            LVRENAME_CMD,
            vg_name,
            old_name,
            new_name,
        ]
        try:
            self._run(lvrename_cmd, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"{LVRENAME_CMD} failed with: {_decode_stderr(err)}"
            ) from err

        return self._build_snapshot(
            f"{vg_name}/{new_name}",
            snapset_name,
            lv_name,
            timestamp,
            mount_point,
            self,
            vg_name,
            new_name,
        )

    def check_revert_snapshot(self, name, origin):
        """
        Check whether this snapshot can be reverted or not. This method returns
        if the current snapshot can be reverted and raises an exception if not.

        :returns: None
        :raises: ``NotImplementedError`` if this plugin does not support the
        revert operation, ``SnapmBusyError`` if the snapshot is already in the
        process of being reverted to another snapshot state or
        ``SnapmPluginError`` if another reason prevents the snapshot from being
        merged.
        """
        vg_name, lv_name = vg_lv_from_origin(origin)
        lvs_dict = self.get_lvs_json_report(f"{vg_name}/{lv_name}")
        lv_report = lvs_dict[LVS_REPORT][0][LVS_LV][0]
        lv_attr = lv_report[LVS_LV_ATTR]
        if lv_attr[0] == LVM_LV_ORIGIN_MERGING:
            raise SnapmBusyError(
                f"Snapshot revert is in progress for {name} origin volume {vg_name}/{lv_name}"
            )

    def revert_snapshot(self, name):
        """
        Revert the state of the content of the origin to the content at the
        time the snapshot was taken.

        For LVM2 snapshots of in-use logical volumes this will take place at
        the next activation (typically a reboot into the revert boot entry
        for the snapshot set).
        """
        lvconvert_cmd = [
            LVCONVERT_CMD,
            LVCONVERT_MERGE,
            name,
        ]
        try:
            self._run(lvconvert_cmd, capture_output=False, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"{LVCONVERT_CMD} failed with: {_decode_stderr(err)}"
            ) from err

    def activate_snapshot(self, name):
        """
        Activate the snapshot named ``name``

        :param name: The name of the snapshot to be activated.
        """
        self._log_debug("Activating %s snapshot %s", self.name, name)
        self._activate(LVCHANGE_ACTIVE_YES, name)

    def deactivate_snapshot(self, name):
        """
        Deactivate the snapshot named ``name``

        :param name: The name of the snapshot to be deactivated.
        """
        self._log_debug("Deactivating %s snapshot %s", self.name, name)
        self._activate(LVCHANGE_ACTIVE_NO, name, silent=True)

    def set_autoactivate(self, name, auto=False):
        """
        Set the autoactivation state of the snapshot named ``name``.

        :param name: The name of the snapshot to be modified.
        :param auto: ``True`` to enable autoactivation or ``False`` otherwise.
        """
        lvchange_cmd = [
            LVCHANGE_CMD,
            LVCHANGE_SETACTIVATIONSKIP,
            LVCHANGE_ACTIVATIONSKIP_NO if auto else LVCHANGE_ACTIVATIONSKIP_YES,
            name,
        ]
        try:
            self._run(lvchange_cmd, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"{LVCHANGE_CMD} failed with: {_decode_stderr(err)}"
            ) from err

    def _check_lvm_name(self, vg_name, lv_name):
        """
        Check whether a proposed LVM name exceeds the maximum allowed name
        length for an LVM2 logical volume.

        :param vg_name: The volume group name to check.
        :param lv_name: The logical volume name to check.
        :raises: ``SnapmInvalidIdentifierError`` if the proposed name exceeds
                 limits.
        """
        full_name = f"{vg_name}/{lv_name}"
        if len(full_name) > self.max_name_len:
            raise SnapmInvalidIdentifierError(
                f"Logical volume name {full_name} exceeds maximum LVM2 name length"
            )

    def _build_snapshot(
        self,
        name,
        snapset_name,
        origin,
        timestamp,
        mount_point,
        provider,
        vg_name,
        lv_name,
        lv_dict=None,
    ):
        """
        Build an instance of this plugin's snapshot class and return it.
        """
        raise NotImplementedError


def _snapshot_min_size(space_used):
    """
    Return the minimum snapshot size given the space used by the snapshot
    mount point.

    :param space_used: The space currently in use on the mount point.
    :returns: The greater of ``space_used`` and ``MIN_LVM2_COW_SNAPSHOT_SIZE``.
    """
    return max(MIN_LVM2_COW_SNAPSHOT_SIZE, space_used)


class Lvm2Cow(_Lvm2):
    """
    LVM2 Copy-on-write snapshot plugin.
    """

    name = "lvm2-cow"
    version = "0.2.0"
    snapshot_class = Lvm2CowSnapshot

    max_name_len = LVM_MAX_NAME_LEN - LVM_COW_SNAPSHOT_NAME_LEN

    def __init__(self, logger, plugin_cfg):
        super().__init__(logger, plugin_cfg)
        self.origins = {}
        if self.priority == PLUGIN_NO_PRIORITY:
            self.priority = LVM2_COW_STATIC_PRIORITY

    def discover_snapshots(self):
        """
        Discover snapshots managed by the lvm2-cow plugin.

        :returns: A list of ``Lvm2Snapshot`` objects discovered by this plugin.
        """
        snapshots = []
        lvs_dict = self.get_lvs_json_report(lvs_all=True)
        for lv_dict in lvs_dict[LVS_REPORT][0][LVS_LV]:
            if filter_cow_snapshot(lv_dict):
                # Initialise pool counters to 0
                origin = lv_dict[LVS_LV_ORIGIN]
                vg_name = lv_dict[LVS_VG_NAME]
                self.origins[path_join(DEV_PREFIX, vg_name, origin)] = 0
                try:
                    lv_name = lv_dict[LVS_LV_NAME]
                    if lv_name.startswith(LVS_LV_HIDDEN_START):
                        lv_name = lv_name[1:-1]
                    fields = parse_snapshot_name(lv_name, origin)
                except ValueError:
                    continue
                if fields is not None:
                    (snapset, timestamp, mount_point) = fields
                    full_name = f"{vg_name}/{lv_name}"
                    self._log_debug("Found %s snapshot: %s", self.name, full_name)
                    snapshots.append(
                        Lvm2CowSnapshot(
                            full_name,
                            snapset,
                            f"{origin}",
                            timestamp,
                            mount_point,
                            self,
                            f"{vg_name}",
                            f"{lv_name}",
                            lv_dict=lv_dict,
                        )
                    )

        for snapshot in snapshots:
            self.origins[snapshot.origin] += 1

        if self.limits.snapshots_per_origin > 0:
            for origin, count in self.origins.items():
                if count > self.limits.snapshots_per_origin:
                    self._log_warn(
                        "Snapshots for origin '%s' exceeds MaxSnapshotsPerOrigin (%d > %d)",
                        origin,
                        count,
                        self.limits.snapshots_per_origin,
                    )

        return snapshots

    def can_snapshot(self, source):
        """
        Test whether the lvm2-cow plugin can snapshot the specified ``source``.

        :param source: The mount point or block device path to test.
        :returns: ``True`` if this plugin can snapshot the file system or block
                  device found at ``source``, or ``False`` otherwise.
        """

        if S_ISBLK(stat(source).st_mode):
            device = source
        else:
            device = device_from_mount_point(source)

        if not self._is_lvm_device(device):
            return False

        (vg_name, lv_name) = self.vg_lv_from_device_path(device)
        lvs_dict = self.get_lvs_json_report(f"{vg_name}/{lv_name}")
        lv_report = lvs_dict[LVS_REPORT][0][LVS_LV][0]
        lv_attr = lv_report[LVS_LV_ATTR]
        if lv_attr[0] == LVM_LV_ORIGIN_MERGING:
            raise SnapmBusyError(
                f"Snapshot revert is in progress for {self.name} origin volume {vg_name}/{lv_name}"
            )

        return True

    # pylint: disable=too-many-locals
    def _check_free_space(self, origin, mount_point, size_policy, snapshot_name=None):
        """
        Check for available space in volume group ``vg_name`` for the specified
        mount point.

        :param origin: The name of the origin logical volume to check.
        :param mount_point: The mount point path to check.
        :param size_policy: A ``SizePolicy`` to apply to this source.
        :param snapshot_name: An optional LV name: if set treat this as a resize operation
                     for the snapshot logical volume named ``name``.
        :returns: The space used on the mount point.
        :raises: ``SnapmNoSpaceError`` if the minimum snapshot size exceeds the
                 available space.
        """
        # Id, space & size info
        vg_name, lv_name = vg_lv_from_origin(origin)
        fs_used = mount_point_space_used(mount_point)
        vg_free, vg_extent_size = self.vg_free_space(vg_name)
        lv_size = self.lv_dev_size(vg_name, lv_name)

        # Determine current size if resizing
        current_size = 0
        if snapshot_name:
            _, _, snap_lv = snapshot_name.partition("/")
            if not snap_lv:
                raise SnapmPluginError(f"Malformed snapshot name: {snapshot_name}")
            current_size = self.lv_dev_size(vg_name, snap_lv)

        # Calculate policy defined size (bytes)
        policy = SizePolicy(origin, mount_point, vg_free, fs_used, lv_size, size_policy)

        self._log_debug(
            "Applying SizePolicy(%s): origin=%s mp=%s vg_free=%d fs_used=%d lv_size=%d "
            "current_size=%d snapshot_min_size=%d (policy.size=%d, rounded=%d)",
            size_policy,
            origin,
            mount_point,
            vg_free,
            fs_used,
            lv_size,
            current_size,
            _snapshot_min_size(policy.size),
            policy.size,
            _round_up_extents(policy.size, vg_extent_size),
        )

        # Determine space needed for this operation
        rounded_size = _round_up_extents(
            _snapshot_min_size(policy.size), vg_extent_size
        )
        if current_size and rounded_size < current_size:
            raise SnapmSizePolicyError(
                f"{self.name} does not support shrinking snapshots"
            )
        needed = rounded_size - current_size
        used = sum(self.size_map[vg_name].values())
        if vg_free < (used + needed):
            raise SnapmNoSpaceError(
                f"Volume group {vg_name} has insufficient free space to snapshot {mount_point} "
                f"({size_fmt(vg_free)} < {size_fmt(used + needed)}) "
            )
        return needed

    def _check_limits(self, origin: str):
        """
        Check ``origin`` against configured plugin limits: return ``True`` if
        adding a new snapshot of this origin would exceed limits, and ``False``
        otherwise.

        :param origin: The origin volume to check.
        :type origin: ``str``
        :returns: ``True`` if adding a new snapshot would exceed limits, or
                 ``False`` otherwise.
        :rtype: ``bool``
        """
        return self._check_limit(self.limits.snapshots_per_origin, self.origins, origin)

    def check_create_snapshot(
        self, origin, snapset_name, timestamp, mount_point, size_policy
    ):
        vg_name, lv_name = vg_lv_from_origin(origin)
        snapshot_name = format_snapshot_name(
            lv_name, snapset_name, timestamp, encode_mount_point(mount_point)
        )
        self._check_lvm_name(vg_name, snapshot_name)
        if vg_name not in self.size_map:
            self.size_map[vg_name] = {}
        self.size_map[vg_name][lv_name] = self._check_free_space(
            origin,
            mount_point,
            size_policy,
        )
        if self._check_limits(origin):
            raise SnapmLimitError(
                f"Adding snapshot of {mount_point} would exceed MaxSnapshotsPerOrigin "
                f"for {origin} ({self.limits.snapshots_per_origin})"
            )

    def create_snapshot(
        self, origin, snapset_name, timestamp, mount_point, size_policy
    ):
        vg_name, lv_name = vg_lv_from_origin(origin)
        snapshot_size = self.size_map[vg_name][lv_name]
        self._log_debug(
            "Creating %s CoW snapshot for %s/%s %s %s",
            size_fmt(snapshot_size),
            vg_name,
            lv_name,
            "mounted at" if mount_point else "",
            mount_point,
        )
        snapshot_name = format_snapshot_name(
            lv_name, snapset_name, timestamp, encode_mount_point(mount_point)
        )
        self._check_lvm_name(vg_name, snapshot_name)
        lvcreate_cmd = [
            LVCREATE_CMD,
            LVCREATE_SNAPSHOT,
            LVCREATE_NAME,
            snapshot_name,
            LVCREATE_SIZE,
            f"{snapshot_size}b",
            origin,
        ]
        try:
            self._run(lvcreate_cmd, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"{LVCREATE_CMD} failed with: {_decode_stderr(err)}"
            ) from err

        origin_path = path_join(DEV_PREFIX, origin)
        if origin_path not in self.origins:
            self.origins[origin_path] = 1
        else:
            self.origins[origin_path] += 1

        return Lvm2CowSnapshot(
            f"{vg_name}/{snapshot_name}",
            snapset_name,
            lv_name,
            timestamp,
            mount_point,
            self,
            vg_name,
            snapshot_name,
        )

    def delete_snapshot(self, name):
        """
        Delete the CoW snapshot named ``name``

        :param name: The name of the snapshot to be removed.
        """
        lvs_dict = self.get_lvs_json_report(vg_lv=name)
        lv_dict = lvs_dict[LVS_REPORT][0][LVS_LV][0]
        origin = path_join(DEV_PREFIX, lv_dict[LVS_VG_NAME], lv_dict[LVS_LV_ORIGIN])

        self._delete_snapshot(name)

        if origin in self.origins:
            self.origins[origin] -= 1
        else:
            # This is harmless but indicates a bug (plugin methods called out-of-sequence).
            self._log_warn("Deleted untracked snapshot set member: %s", name)

    def check_resize_snapshot(self, name, origin, mount_point, size_policy):
        vg_name, lv_name = vg_lv_from_origin(origin)
        if vg_name not in self.size_map:
            self.size_map[vg_name] = {}
        self.size_map[vg_name][lv_name] = self._check_free_space(
            origin,
            mount_point,
            size_policy,
            snapshot_name=name,
        )

    def resize_snapshot(self, name, origin, mount_point, size_policy):
        vg_name, lv_name = vg_lv_from_origin(origin)
        size_change = self.size_map[vg_name][lv_name]

        if size_change:
            self._log_debug(
                "Resizing CoW snapshot %s by +%s", name, size_fmt(size_change)
            )
            lvresize_cmd = [
                LVRESIZE_CMD,
                LVCREATE_SIZE,
                f"+{size_change}b",
                name,
            ]
            try:
                self._run(lvresize_cmd, capture_output=True, check=True)
            except CalledProcessError as err:
                raise SnapmCalloutError(
                    f"{LVRESIZE_CMD} failed with: {_decode_stderr(err)}"
                ) from err
        else:
            self._log_debug("Skipping no-op resize for %s", name)

    def _build_snapshot(
        self,
        name,
        snapset_name,
        origin,
        timestamp,
        mount_point,
        provider,
        vg_name,
        lv_name,
        lv_dict=None,
    ):
        """
        Build an instance of `Lvm2CowSnapshot` and return it.
        """
        return Lvm2CowSnapshot(
            name,
            snapset_name,
            origin,
            timestamp,
            mount_point,
            provider,
            vg_name,
            lv_name,
            lv_dict=lv_dict,
        )


class Lvm2Thin(_Lvm2):
    """
    LVM2 thin provisioning snapshot plugin.
    """

    name = "lvm2-thin"
    version = "0.2.0"
    snapshot_class = Lvm2ThinSnapshot

    max_name_len = LVM_MAX_NAME_LEN

    def __init__(self, logger, plugin_cfg):
        super().__init__(logger, plugin_cfg)
        self.pools = {}
        if self.priority == PLUGIN_NO_PRIORITY:
            self.priority = LVM2_THIN_STATIC_PRIORITY

    def discover_snapshots(self):
        snapshots = []
        lvs_dict = self.get_lvs_json_report(lvs_all=True)
        for lv_dict in lvs_dict[LVS_REPORT][0][LVS_LV]:
            if filter_thin_snapshot(lv_dict):
                # Initialise pool counters to 0
                vg_name = lv_dict[LVS_VG_NAME]
                pool = lv_dict[LVS_POOL_LV]
                pool_key = f"{vg_name}/{pool}"
                self.pools[pool_key] = 0
                try:
                    lv_name = lv_dict[LVS_LV_NAME]
                    if lv_name.startswith(LVS_LV_HIDDEN_START):
                        lv_name = lv_name[1:-1]
                    fields = parse_snapshot_name(lv_name, lv_dict[LVS_LV_ORIGIN])
                except ValueError:
                    continue
                if fields is not None:
                    full_name = f"{vg_name}/{lv_name}"
                    self._log_debug("Found %s snapshot: %s", self.name, full_name)
                    (snapset, timestamp, mount_point) = fields
                    snapshots.append(
                        Lvm2ThinSnapshot(
                            full_name,
                            snapset,
                            f"{lv_dict[LVS_LV_ORIGIN]}",
                            timestamp,
                            mount_point,
                            self,
                            f"{vg_name}",
                            f"{lv_name}",
                            lv_dict=lv_dict,
                        )
                    )

        for snapshot in snapshots:
            pool_key = f"{snapshot.vg_name}/{snapshot.pool}"
            self.pools[pool_key] += 1

        if self.limits.snapshots_per_pool > 0:
            for pool, count in self.pools.items():
                if count > self.limits.snapshots_per_pool:
                    self._log_warn(
                        "Snapshots for pool '%s' exceeds MaxSnapshotsPerPool (%d > %d)",
                        pool,
                        count,
                        self.limits.snapshots_per_pool,
                    )

        return snapshots

    def can_snapshot(self, source):
        """
        Test whether the lvm2-thin plugin can snapshot the specified ``source``.

        :param source: The mount point or block device path to test.
        :returns: ``True`` if this plugin can snapshot the file system or block
                  device found at ``source``, or ``False`` otherwise.
        """
        if S_ISBLK(stat(source).st_mode):
            device = source
        else:
            device = device_from_mount_point(source)

        if not self._is_lvm_device(device):
            return False

        (vg_name, lv_name) = self.vg_lv_from_device_path(device)
        lvs_dict = self.get_lvs_json_report(f"{vg_name}/{lv_name}")
        lv_report = lvs_dict[LVS_REPORT][0][LVS_LV][0]
        lv_attr = lv_report[LVS_LV_ATTR]
        if lv_attr[0] == LVM_LV_ORIGIN_MERGING:
            raise SnapmBusyError(
                f"Snapshot revert is in progress for {self.name} origin volume {vg_name}/{lv_name}"
            )
        if lv_attr[0] != LVM_THIN_VOL_ATTR:
            return False

        return True

    def _check_free_space(self, origin, pool_name, mount_point, size_policy):
        """
        Check for available space in pool ``pool_name`` for the specified
        mount point.

        :param pool_name: The name of the pool to check.
        :param mount_point: The mount point path to check.
        :returns: The space used on the mount point.
        :raises: ``SnapmNoSpaceError`` if the minimum snapshot size exceeds the
                 available space.
        """
        vg_name, lv_name = vg_lv_from_origin(origin)
        fs_used = mount_point_space_used(mount_point)
        lv_size = self.lv_dev_size(vg_name, lv_name)
        pool_free = self.pool_free_space(vg_name, pool_name)
        policy = SizePolicy(
            origin, mount_point, pool_free, fs_used, lv_size, size_policy
        )
        snapshot_min_size = policy.size
        if pool_free < (
            sum(self.size_map[vg_name][pool_name].values()) + snapshot_min_size
        ):
            raise SnapmNoSpaceError(
                f"Volume group thin pool {vg_name}/{pool_name} "
                f"has insufficient free space to snapshot {mount_point}"
            )
        return snapshot_min_size

    def _check_limits(self, pool: str) -> bool:
        """
        Check ``pool`` against configured plugin limits: return ``True`` if
        adding a new snapshot of this pool would exceed limits, and ``False``
        otherwise.

        :param pool: The pool volume to check.
        :type pool: ``str``
        :returns: ``True`` if adding a new snapshot would exceed limits, or
                 ``False`` otherwise.
        :rtype: ``bool``
        """
        return self._check_limit(self.limits.snapshots_per_pool, self.pools, pool)

    def check_create_snapshot(
        self, origin, snapset_name, timestamp, mount_point, size_policy
    ):
        vg_name, lv_name = vg_lv_from_origin(origin)
        pool_name = self.pool_name_from_vg_lv(origin)
        snapshot_name = format_snapshot_name(
            lv_name, snapset_name, timestamp, encode_mount_point(mount_point)
        )
        self._check_lvm_name(vg_name, snapshot_name)
        if vg_name not in self.size_map:
            self.size_map[vg_name] = {}
        if pool_name not in self.size_map[vg_name]:
            self.size_map[vg_name][pool_name] = {}
        self.size_map[vg_name][pool_name][lv_name] = self._check_free_space(
            origin, pool_name, mount_point, size_policy
        )
        if self._check_limits(pool_name):
            raise SnapmLimitError(
                f"Adding snapshot of {mount_point} would exceed MaxSnapshotsPerPool "
                f"for {pool_name} ({self.limits.snapshots_per_pool})"
            )

    def create_snapshot(
        self, origin, snapset_name, timestamp, mount_point, size_policy
    ):
        vg_name, lv_name = vg_lv_from_origin(origin)
        self._log_debug(
            "Creating thin snapshot for %s/%s mounted at %s",
            vg_name,
            lv_name,
            mount_point,
        )
        snapshot_name = format_snapshot_name(
            lv_name, snapset_name, timestamp, encode_mount_point(mount_point)
        )
        self._check_lvm_name(vg_name, snapshot_name)
        pool_name = self.pool_name_from_vg_lv(origin)

        lvcreate_cmd = [
            LVCREATE_CMD,
            LVCREATE_SNAPSHOT,
            LVCREATE_NAME,
            snapshot_name,
            origin,
        ]
        try:
            self._run(lvcreate_cmd, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"{LVCREATE_CMD} failed with: {_decode_stderr(err)}"
            ) from err

        pool_key = f"{vg_name}/{pool_name}"
        if pool_key not in self.pools:
            self.pools[pool_key] = 1
        else:
            self.pools[pool_key] += 1

        return Lvm2ThinSnapshot(
            f"{vg_name}/{snapshot_name}",
            snapset_name,
            lv_name,
            timestamp,
            mount_point,
            self,
            vg_name,
            snapshot_name,
        )

    def delete_snapshot(self, name):
        """
        Delete the thin snapshot named ``name``

        :param name: The name of the snapshot to be removed.
        """
        lvs_dict = self.get_lvs_json_report(vg_lv=name)
        lv_dict = lvs_dict[LVS_REPORT][0][LVS_LV][0]
        vg_name = lv_dict[LVS_VG_NAME]
        pool = lv_dict[LVS_POOL_LV]
        self._delete_snapshot(name)

        pool_key = f"{vg_name}/{pool}"
        if pool_key in self.pools:
            self.pools[pool_key] -= 1
        else:
            # This is harmless but indicates a bug (plugin methods called out-of-sequence).
            self._log_warn("Deleted untracked snapshot set member: %s", name)

    def check_resize_snapshot(self, name, origin, mount_point, size_policy):
        vg_name, lv_name = vg_lv_from_origin(origin)
        pool_name = self.pool_name_from_vg_lv(origin)
        if vg_name not in self.size_map:
            self.size_map[vg_name] = {}
        if pool_name not in self.size_map[vg_name]:
            self.size_map[vg_name][pool_name] = {}
        self.size_map[vg_name][pool_name][lv_name] = self._check_free_space(
            origin, pool_name, mount_point, size_policy
        )

    def resize_snapshot(self, name, origin, mount_point, size_policy):
        pass

    def _build_snapshot(
        self,
        name,
        snapset_name,
        origin,
        timestamp,
        mount_point,
        provider,
        vg_name,
        lv_name,
        lv_dict=None,
    ):
        """
        Build an instance of `Lvm2CowSnapshot` and return it.
        """
        return Lvm2ThinSnapshot(
            name,
            snapset_name,
            origin,
            timestamp,
            mount_point,
            provider,
            vg_name,
            lv_name,
            lv_dict=lv_dict,
        )
