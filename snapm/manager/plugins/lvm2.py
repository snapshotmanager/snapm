# Copyright Red Hat
#
# snapm/manager/plugins/lvm2.py - Snapshot Manager LVM2 plugins
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: GPL-2.0-only
"""
LVM2 snapshot manager plugins
"""
from subprocess import run, CalledProcessError
from json import loads, JSONDecodeError
from math import floor
from os.path import join as path_join
from os import stat, environ
from stat import S_ISBLK
from time import time
from shutil import which

from snapm import (
    SnapmInvalidIdentifierError,
    SnapmCalloutError,
    SnapmBusyError,
    SnapmNoSpaceError,
    SnapmNotFoundError,
    SnapmPluginError,
    SizePolicy,
    SnapStatus,
    Snapshot,
)
from snapm.manager.plugins import (
    DEV_PREFIX,
    DEV_MAPPER_PREFIX,
    DMSETUP_CMD,
    DMSETUP_INFO,
    DMSETUP_NO_HEADINGS,
    DMSETUP_COLUMNS,
    DMSETUP_FIELDS_UUID,
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
LVM_BASIC = "basic"
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
VGS_FIELD_OPTIONS = "vg_name,vg_free"

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
    Check for the presence of the required LVM2 commands.

    :returns: ``True`` if all commands are present and executable or
              ``False`` otherwise.
    """
    if not all(which(cmd) for cmd in _LVM_CMDS):
        raise SnapmNotFoundError("LVM2 commands not found")


def _get_lvm_version():
    """
    Return the installed version of LVM2 as a tuple.

    :returns: A version tuple (major, minor, patch) of LVM2
    """

    def _version_string_to_tuple(version):
        return tuple(map(int, version.split(".")))

    lvm_cmd_args = [LVM_CMD, LVM_VERSION]
    lvm_cmd = run(lvm_cmd_args, capture_output=True, check=True)
    lvm_version_info = str.strip(lvm_cmd.stdout.decode("utf8"))
    for line in lvm_version_info.splitlines():
        if LVM_VERSION_STR in line:
            (_, _, version, _) = line.split()
            if "(" in version:
                version, _ = version.split("(", maxsplit=1)
            return _version_string_to_tuple(version)
    return (0, 0, 0)


def is_lvm_device(devpath):
    """
    Test whether ``devpath`` is an LVM device.

    Return ``True`` if the device at ``devpath`` is an LVM device or
    ``False`` otherwise.
    """

    if devpath.startswith(DEV_MAPPER_PREFIX):
        dm_name = devpath.removeprefix(DEV_MAPPER_PREFIX)
    else:
        dm_name = devpath

    dmsetup_cmd_args = [
        DMSETUP_CMD,
        DMSETUP_INFO,
        DMSETUP_COLUMNS,
        DMSETUP_NO_HEADINGS,
        DMSETUP_FIELDS_UUID,
        dm_name,
    ]
    try:
        dmsetup_cmd = run(dmsetup_cmd_args, capture_output=True, check=True)
    except CalledProcessError as err:
        raise SnapmCalloutError(
            f"Error calling {DMSETUP_CMD}: {_decode_stderr(err)}"
        ) from err
    uuid = dmsetup_cmd.stdout.decode("utf8").strip()
    return uuid.startswith(LVM_UUID_PREFIX)


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


def _activate(active, name, silent=False):
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
        run(lvchange_cmd, capture_output=True, check=True)
    except CalledProcessError as err:
        if not silent:
            raise SnapmCalloutError(
                f"{LVCHANGE_CMD} failed with: {_decode_stderr(err)}"
            ) from err


class _Lvm2(Plugin):
    """
    Abstract base class for LVM2 snapshot plugins.
    """

    max_name_len = LVM_MAX_NAME_LEN

    def vg_lv_from_device_path(self, devpath):
        """
        Return a ``(vg_name, lv_name)`` tuple for the LVM device at
        ``devpath``.
        """
        lvs_cmd_args = [
            LVS_CMD,
            LVS_NO_HEADINGS,
            LVM_REPORT_FORMAT,
            LVM_BASIC,
            LVM_OPTIONS,
            LVS_FIELD_MIN_OPTIONS,
            devpath,
        ]
        try:
            lvs_cmd = run(lvs_cmd_args, env=self._env, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"Error calling {LVS_CMD}: {_decode_stderr(err)}"
            ) from err
        name = lvs_cmd.stdout.decode("utf8").strip()
        name_parts = name.split(" ")
        return (name_parts[0], name_parts[1])

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
        Return the free space available as bytes for the volume group named
        ``vg_name``.
        """
        vgs_dict = self.get_vgs_json_report(vg_name=vg_name)
        for vg_dict in vgs_dict[VGS_REPORT][0][VGS_VG]:
            if vg_dict["vg_name"] == vg_name:
                return int(vg_dict["vg_free"].rstrip("B"))
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
            lvs_cmd = run(lvs_cmd_args, capture_output=True, check=True)
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
            vgs_cmd = run(vgs_cmd_args, capture_output=True, check=True)
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
            lvm_version = _get_lvm_version()
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

    def __init__(self, logger):
        super().__init__(logger)

        # Default to using json_std when available.
        self._json_fmt = LVM_JSON_STD

        # Sanitize environment for LVM2 callouts.
        self._env = self._sanitize_environment()

        # Check for presence of required LVM2 binaries.
        _check_lvm_present()

        # Check LVM2 minimum version requirements.
        self._check_lvm_version()

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
        if not is_lvm_device(device):
            return None
        (vg_name, lv_name) = self.vg_lv_from_device_path(device)
        return path_join(DEV_PREFIX, vg_name, lv_name)

    def delete_snapshot(self, name):
        """
        Delete the snapshot named ``name``

        :param name: The name of the snapshot to be removed.
        """
        lvremove_cmd = [LVREMOVE_CMD, LVREMOVE_YES, name]
        try:
            run(lvremove_cmd, env=self._env, capture_output=True, check=True)
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
            run(lvrename_cmd, env=self._env, capture_output=True, check=True)
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
            run(lvconvert_cmd, env=self._env, capture_output=False, check=True)
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
        _activate(LVCHANGE_ACTIVE_YES, name)

    def deactivate_snapshot(self, name):
        """
        Deactivate the snapshot named ``name``

        :param name: The name of the snapshot to be deactivated.
        """
        self._log_debug("Deactivating %s snapshot %s", self.name, name)
        _activate(LVCHANGE_ACTIVE_NO, name, silent=True)

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
            run(lvchange_cmd, env=self._env, capture_output=True, check=True)
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
    version = "0.1.0"
    snapshot_class = Lvm2CowSnapshot

    max_name_len = LVM_MAX_NAME_LEN - LVM_COW_SNAPSHOT_NAME_LEN

    def discover_snapshots(self):
        """
        Discover snapshots managed by the lvm2-cow plugin.

        :returns: A list of ``Lvm2Snapshot`` objects discovered by this plugin.
        """
        snapshots = []
        lvs_dict = self.get_lvs_json_report(lvs_all=True)
        for lv_dict in lvs_dict[LVS_REPORT][0][LVS_LV]:
            if filter_cow_snapshot(lv_dict):
                try:
                    lv_name = lv_dict[LVS_LV_NAME]
                    if lv_name.startswith(LVS_LV_HIDDEN_START):
                        lv_name = lv_name[1:-1]
                    fields = parse_snapshot_name(lv_name, lv_dict[LVS_LV_ORIGIN])
                except ValueError:
                    continue
                if fields is not None:
                    (snapset, timestamp, mount_point) = fields
                    full_name = f"{lv_dict[LVS_VG_NAME]}/{lv_name}"
                    self._log_debug("Found %s snapshot: %s", self.name, full_name)
                    snapshots.append(
                        Lvm2CowSnapshot(
                            full_name,
                            snapset,
                            f"{lv_dict[LVS_LV_ORIGIN]}",
                            timestamp,
                            mount_point,
                            self,
                            f"{lv_dict[LVS_VG_NAME]}",
                            f"{lv_name}",
                            lv_dict=lv_dict,
                        )
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

        if not is_lvm_device(device):
            return False
        (vg_name, lv_name) = self.vg_lv_from_device_path(device)
        lvs_dict = self.get_lvs_json_report(f"{vg_name}/{lv_name}")
        lv_report = lvs_dict[LVS_REPORT][0][LVS_LV][0]
        lv_attr = lv_report[LVS_LV_ATTR]
        if lv_attr[0] == LVM_LV_ORIGIN_MERGING:
            raise SnapmBusyError(
                f"Snapshot revert is in progress for {self.name} origin volume {vg_name}/{lv_name}"
            )
        if lv_attr[0] != LVM_LV_TYPE_DEFAULT and lv_attr[0] != LVM_COW_ORIGIN_ATTR:
            return False
        return True

    def _check_free_space(self, origin, mount_point, size_policy):
        """
        Check for available space in volume group ``vg_name`` for the specified
        mount point.

        :param vg_name: The name of the volume group to check.
        :param mount_point: The mount point path to check.
        :returns: The space used on the mount point.
        :raises: ``SnapmNoSpaceError`` if the minimum snapshot size exceeds the
                 available space.
        """
        vg_name, lv_name = vg_lv_from_origin(origin)
        fs_used = mount_point_space_used(mount_point)
        vg_free = self.vg_free_space(vg_name)
        lv_size = self.lv_dev_size(vg_name, lv_name)
        policy = SizePolicy(origin, mount_point, vg_free, fs_used, lv_size, size_policy)
        snapshot_min_size = _snapshot_min_size(policy.size)
        if vg_free < (sum(self.size_map[vg_name].values()) + snapshot_min_size):
            raise SnapmNoSpaceError(
                f"Volume group {vg_name} has insufficient free space to snapshot {mount_point}"
            )
        return snapshot_min_size

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
            origin, mount_point, size_policy
        )

    def create_snapshot(
        self, origin, snapset_name, timestamp, mount_point, size_policy
    ):
        vg_name, lv_name = vg_lv_from_origin(origin)
        self._log_debug(
            "Creating CoW snapshot for %s/%s %s %s",
            vg_name,
            lv_name,
            "mounted at" if mount_point else "",
            mount_point,
        )
        snapshot_name = format_snapshot_name(
            lv_name, snapset_name, timestamp, encode_mount_point(mount_point)
        )
        self._check_lvm_name(vg_name, snapshot_name)
        snapshot_size = self.size_map[vg_name][lv_name]
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
            run(lvcreate_cmd, env=self._env, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"{LVCREATE_CMD} failed with: {_decode_stderr(err)}"
            ) from err
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

    def check_resize_snapshot(self, name, origin, mount_point, size_policy):
        vg_name, lv_name = vg_lv_from_origin(origin)
        if vg_name not in self.size_map:
            self.size_map[vg_name] = {}
        self.size_map[vg_name][lv_name] = self._check_free_space(
            origin, mount_point, size_policy
        )

    def resize_snapshot(self, name, origin, mount_point, size_policy):
        vg_name, lv_name = vg_lv_from_origin(origin)
        snapshot_size = self.size_map[vg_name][lv_name]

        lvresize_cmd = [
            LVRESIZE_CMD,
            LVCREATE_SIZE,
            f"{snapshot_size}b",
            name,
        ]
        try:
            run(lvresize_cmd, env=self._env, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"{LVRESIZE_CMD} failed with: {_decode_stderr(err)}"
            ) from err

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
    version = "0.1.0"
    snapshot_class = Lvm2ThinSnapshot

    max_name_len = LVM_MAX_NAME_LEN

    def discover_snapshots(self):
        snapshots = []
        lvs_dict = self.get_lvs_json_report(lvs_all=True)
        for lv_dict in lvs_dict[LVS_REPORT][0][LVS_LV]:
            if filter_thin_snapshot(lv_dict):
                try:
                    lv_name = lv_dict[LVS_LV_NAME]
                    if lv_name.startswith(LVS_LV_HIDDEN_START):
                        lv_name = lv_name[1:-1]
                    fields = parse_snapshot_name(lv_name, lv_dict[LVS_LV_ORIGIN])
                except ValueError:
                    continue
                if fields is not None:
                    full_name = f"{lv_dict[LVS_VG_NAME]}/{lv_name}"
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
                            f"{lv_dict[LVS_VG_NAME]}",
                            f"{lv_name}",
                            lv_dict=lv_dict,
                        )
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

        if not is_lvm_device(device):
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

        lvcreate_cmd = [
            LVCREATE_CMD,
            LVCREATE_SNAPSHOT,
            LVCREATE_NAME,
            snapshot_name,
            origin,
        ]
        try:
            run(lvcreate_cmd, env=self._env, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(
                f"{LVCREATE_CMD} failed with: {_decode_stderr(err)}"
            ) from err
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
