# Copyright (C) 2023 Red Hat, Inc., Bryn M. Reeves <bmr@redhat.com>
#
# snapm/manager/plugins/lvm2.py - Snapshot Manager LVM2 plugins
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
"""
LVM2 snapshot manager plugins
"""
from subprocess import run, CalledProcessError
from json import loads, JSONDecodeError
from math import floor
from os.path import join as path_join
from time import time
import logging

from snapm import (
    SNAPM_DEBUG_PLUGINS,
    SnapmInvalidIdentifierError,
    SnapmCalloutError,
    SnapmNoSpaceError,
    SnapStatus,
    Snapshot,
)
from snapm.manager import Plugin
from snapm.manager.plugins import (
    parse_snapshot_name,
    device_from_mount_point,
    mount_point_space_used,
    format_snapshot_name,
    encode_mount_point,
)

_log = logging.getLogger(__name__)
_log.set_debug_mask(SNAPM_DEBUG_PLUGINS)

_log_debug = _log.debug
_log_debug_lvm2 = _log.debug_masked
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error

LVM_MAX_NAME_LEN = 127

# Global LVM2 report options
LVM_REPORT_FORMAT = "--reportformat"
LVM_JSON = "json"
LVM_OPTIONS = "--options"
LVM_UNITS = "--units"
LVM_BYTES = "b"
LVM_UUID_PREFIX = "LVM-"

# lvs report options
LVS_CMD = "lvs"
LVS_REPORT = "report"
LVS_LV = "lv"
LVS_LV_NAME = "lv_name"
LVS_VG_NAME = "vg_name"
LVS_LV_ATTR = "lv_attr"
LVS_LV_ROLE = "lv_role"
LVS_LV_ORIGIN = "origin"
LVS_POOL_LV = "pool_lv"
LVS_LV_SIZE = "lv_size"
LVS_DATA_PERCENT = "data_percent"
LVS_FIELD_OPTIONS = (
    "vg_name,lv_name,lv_attr,origin,pool_lv,lv_size,data_percent,lv_role"
)

# lv_attr flag values
LVM_COW_SNAP_ATTR = "s"
LVM_THIN_VOL_ATTR = "V"
LVM_COW_ORIGIN_ATTR = "o"
LVM_ACTIVE_ATTR = "a"
LVM_INVALID_ATTR = "I"
LVM_LV_TYPE_DEFAULT = "-"
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

# dmsetup options
DMSETUP_CMD = "dmsetup"
DMSETUP_SPLITNAME = "splitname"
DMSETUP_INFO = "info"
DMSETUP_LVM_SUBSYS = "LVM"
DMSETUP_NO_HEADINGS = "--noheadings"
DMSETUP_COLUMNS = "--columns"
DMSETUP_FIELDS_VG_LV = "-ovg_name,lv_name"
DMSETUP_FIELDS_UUID = "-ouuid"
DMSETUP_REPORT_SEP = ":"

DEV_PREFIX = "/dev"
DEV_MAPPER_PREFIX = "/dev/mapper/"

# Minimum possible LVM2 CoW snapshot size (512MiB)
MIN_LVM2_COW_SNAPSHOT_SIZE = 512 * 1024**2

# Maximum time to cache lvs data in seconds
LVS_CACHE_VALID = 5


def get_lvs_json_report(vg_lv=None):
    """
    Call out to the ``lvs`` program and return a report in JSON format.
    """
    lvs_cmd_args = [
        LVS_CMD,
        LVM_REPORT_FORMAT,
        LVM_JSON,
        LVM_UNITS,
        LVM_BYTES,
        LVM_OPTIONS,
        LVS_FIELD_OPTIONS,
    ]
    if vg_lv:
        lvs_cmd_args.append(vg_lv)
    try:
        lvs_cmd = run(lvs_cmd_args, capture_output=True, check=True)
    except CalledProcessError as err:
        raise SnapmCalloutError(f"Error calling {LVS_CMD}: {err}") from err
    try:
        lvs_dict = loads(lvs_cmd.stdout)
    except JSONDecodeError as err:
        raise SnapmCalloutError(
            f"Unable to decode {LVS_CMD} JSON output: {err}"
        ) from err
    return lvs_dict


def get_vgs_json_report(vg_name=None):
    """
    Call out to the ``vgs`` program and return a report in JSON format.
    """
    vgs_cmd_args = [
        VGS_CMD,
        LVM_REPORT_FORMAT,
        LVM_JSON,
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
        raise SnapmCalloutError(f"Error calling {VGS_CMD}: {err}") from err
    try:
        vgs_dict = loads(vgs_cmd.stdout)
    except JSONDecodeError as err:
        raise SnapmCalloutError(
            f"Unable to decode {VGS_CMD} JSON output: {err}"
        ) from err
    return vgs_dict


def is_lvm_device(devpath):
    """
    Test whether ``devpath`` is an LVM device.

    Return ``True`` if the device at ``devpath`` is an LVM device or
    ``False`` otherwise.
    """
    if not devpath.startswith(DEV_MAPPER_PREFIX):
        return False
    dm_name = devpath.removeprefix(DEV_MAPPER_PREFIX)
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
        raise SnapmCalloutError(f"Error calling {DMSETUP_CMD}") from err
    uuid = dmsetup_cmd.stdout.decode("utf8").strip()
    return uuid.startswith(LVM_UUID_PREFIX)


def vg_lv_from_device_path(devpath):
    """
    Return a ``(vg_name, lv_name)`` tuple for the LVM device at
    ``devpath``.
    """
    dm_name = devpath.removeprefix(DEV_MAPPER_PREFIX)
    dmsetup_cmd_args = [
        DMSETUP_CMD,
        DMSETUP_SPLITNAME,
        DMSETUP_NO_HEADINGS,
        DMSETUP_FIELDS_VG_LV,
        dm_name,
        DMSETUP_LVM_SUBSYS,
    ]
    try:
        dmsetup_cmd = run(dmsetup_cmd_args, capture_output=True, check=True)
    except CalledProcessError as err:
        raise SnapmCalloutError(f"Error calling {DMSETUP_CMD}") from err
    name = dmsetup_cmd.stdout.decode("utf8").strip()
    name_parts = name.split(DMSETUP_REPORT_SEP)
    return (name_parts[0], name_parts[1])


def pool_name_from_vg_lv(vg_lv):
    """
    Return the thin pool associated with the logical volume identified by
    ``vg_lv``.
    """
    lvs_dict = get_lvs_json_report(vg_lv)
    lv_dict = lvs_dict[LVS_REPORT][0][LVS_LV][0]
    return lv_dict[LVS_POOL_LV]


def vg_free_space(vg_name):
    """
    Return the free space available as bytes for the volume group named
    ``vg_name``.
    """
    vgs_dict = get_vgs_json_report(vg_name=vg_name)
    for vg_dict in vgs_dict[VGS_REPORT][0][VGS_VG]:
        if vg_dict["vg_name"] == vg_name:
            return int(vg_dict["vg_free"].rstrip("B"))
    raise ValueError(f"Volume group {vg_name} not found")


def pool_free_space(vg_name, pool_name):
    """
    Return the free space available as bytes for the thin pool identified
    by ``vg_name`` and ``pool_name``.
    """
    lvs_dict = get_lvs_json_report(f"{vg_name}/{pool_name}")
    lv_dict = lvs_dict[LVS_REPORT][0][LVS_LV][0]
    data_percent = float(lv_dict[LVS_DATA_PERCENT])
    pool_size = int(lv_dict[LVS_LV_SIZE].rstrip("B"))
    return pool_size - floor(pool_size * (data_percent / 100))


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
            lvs_dict = get_lvs_json_report(f"{self.vg_name}/{self.lv_name}")
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
        if lv_attr[LVM_LV_STATE_ATTR_IDX] == LVM_ACTIVE_ATTR:
            return SnapStatus.ACTIVE
        if lv_attr[LVM_LV_STATE_ATTR_IDX] == LVM_INVALID_ATTR:
            return SnapStatus.INVALID
        return SnapStatus.INACTIVE

    @property
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
            lvs_dict = get_lvs_json_report(f"{self.vg_name}/{self.lv_name}")
            self._lv_dict_cache = lvs_dict[LVS_REPORT][0][LVS_LV][0]
            self._lv_dict_cache_ts = now
        return self._lv_dict_cache


def filter_cow_snapshot(lv_dict):
    """
    Filter LVM2 CoW snapshots.

    Return ``True`` if the logical volume represented by ``lv_dict`` is an LVM2
    COW snapshot or ``False`` otherwise. The ``lv_dict`` argument must be a
    dictionary representing the output of the ``lvs`` reporting command.
    """
    if not lv_dict[LVS_LV_ATTR].startswith(LVM_COW_SNAP_ATTR):
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
    if not lv_dict[LVS_LV_ATTR].startswith(LVM_THIN_VOL_ATTR):
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

    def discover_snapshots(self):
        """
        Discover snapshots managed by this plugin class.

        Returns a list of objects that are a subclass of ``Snapshot``.
        """
        raise NotImplementedError

    def can_snapshot(self, mount_point):
        """
        Test whether this plugin can snapshot the specified mount point.

        :param mount_point: The mount point path to test.
        :returns: ``True`` if this plugin can snapshot the file system mounted
                  at ``mount_point``, or ``False`` otherwise.
        """
        raise NotImplementedError

    def check_create_snapshot(self, origin, snapset_name, timestamp, mount_point):
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

    def create_snapshot(self, origin, snapset_name, timestamp, mount_point):
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
        device = device_from_mount_point(mount_point)
        if not is_lvm_device(device):
            return None
        (vg_name, lv_name) = vg_lv_from_device_path(device)
        return f"{vg_name}/{lv_name}"

    def delete_snapshot(self, name):
        """
        Delete the snapshot named ``name``

        :param name: The name of the snapshot to be removed.
        """
        lvremove_cmd = [LVREMOVE_CMD, LVREMOVE_YES, name]
        try:
            run(lvremove_cmd, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(f"{LVREMOVE_CMD} failed with: {err}") from err

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
        (_, _, vg_name, lv_name) = origin.split("/")
        new_name = format_snapshot_name(
            origin, snapset_name, timestamp, encode_mount_point(mount_point)
        )
        lvrename_cmd = [
            LVRENAME_CMD,
            vg_name,
            old_name,
            new_name,
        ]
        try:
            run(lvrename_cmd, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(f"{LVRENAME_CMD} failed with: {err}") from err
        return Lvm2Snapshot(
            new_name,
            snapset_name,
            origin,
            timestamp,
            mount_point,
            self,
            vg_name,
            lv_name,
        )

    def rollback_snapshot(self, name):
        """
        Roll back the state of the content of the origin to the content at the
        time the snapshot was taken.

        For LVM2 snapshots of in-use logical volumes this will take place at
        the next activation (typically a reboot into the rollback boot entry
        for the snapshot set).
        """
        lvconvert_cmd = [
            LVCONVERT_CMD,
            LVCONVERT_MERGE,
            name,
        ]
        try:
            run(lvconvert_cmd, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(f"{LVCONVERT_CMD} failed with: {err}") from err

    def _activate(self, active, name, silent=False):
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
                raise SnapmCalloutError(f"{LVCHANGE_CMD} failed with: {err}") from err

    def activate_snapshot(self, name):
        """
        Activate the snapshot named ``name``

        :param name: The name of the snapshot to be activated.
        """
        _log_debug("Activating snapshot %s", name)
        self._activate(LVCHANGE_ACTIVE_YES, name)

    def deactivate_snapshot(self, name):
        """
        Deactivate the snapshot named ``name``

        :param name: The name of the snapshot to be deactivated.
        """
        _log_debug("Deactivating snapshot %s", name)
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
            run(lvchange_cmd, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(f"{LVCHANGE_CMD} failed with: {err}") from err

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
        if len(full_name) > LVM_MAX_NAME_LEN:
            raise SnapmInvalidIdentifierError(
                f"Logical volume name {full_name} exceeds maximum LVM2 name length"
            )


class Lvm2Cow(_Lvm2):
    """
    LVM2 Copy-on-write snapshot plugin.
    """

    name = "lvm2-cow"
    version = "0.1.0"

    def discover_snapshots(self):
        """
        Discover snapshots managed by the lvm2-cow plugin.

        :returns: A list of ``Lvm2Snapshot`` objects discovered by this plugin.
        """
        snapshots = []
        lvs_dict = get_lvs_json_report()
        for lv_dict in lvs_dict[LVS_REPORT][0][LVS_LV]:
            if filter_cow_snapshot(lv_dict):
                try:
                    fields = parse_snapshot_name(
                        lv_dict[LVS_LV_NAME], lv_dict[LVS_LV_ORIGIN]
                    )
                except ValueError:
                    continue
                (snapset, timestamp, mount_point) = fields
                if fields is not None:
                    full_name = f"{lv_dict[LVS_VG_NAME]}/{lv_dict[LVS_LV_NAME]}"
                    _log_debug_lvm2("Found %s snapshot: %s", self.name, full_name)
                    snapshots.append(
                        Lvm2Snapshot(
                            full_name,
                            snapset,
                            f"{lv_dict[LVS_LV_ORIGIN]}",
                            timestamp,
                            mount_point,
                            self,
                            f"{lv_dict[LVS_VG_NAME]}",
                            f"{lv_dict[LVS_LV_NAME]}",
                            lv_dict=lv_dict,
                        )
                    )
        return snapshots

    def can_snapshot(self, mount_point):
        """
        Test whether the lvm2-cow plugin can snapshot the specified ``mount_point``.

        :param mount_point: The mount point path to test.
        :returns: ``True`` if this plugin can snapshot the file system mounted
                  at ``mount_point``, or ``False`` otherwise.
        """
        device = device_from_mount_point(mount_point)
        if not is_lvm_device(device):
            return False
        (vg_name, lv_name) = vg_lv_from_device_path(device)
        lvs_dict = get_lvs_json_report(f"{vg_name}/{lv_name}")
        lv_report = lvs_dict[LVS_REPORT][0][LVS_LV][0]
        lv_attr = lv_report[LVS_LV_ATTR]
        if lv_attr[0] != LVM_LV_TYPE_DEFAULT and lv_attr[0] != LVM_COW_ORIGIN_ATTR:
            return False
        return True

    def _snapshot_min_size(self, space_used):
        """
        Return the minimum snapshot size given the space used by the snapshot
        mount point.

        :param space_used: The space currently in use on the mount point.
        :returns: The greater of ``space_used`` and ``MIN_LVM2_COW_SNAPSHOT_SIZE``.
        """
        return max(MIN_LVM2_COW_SNAPSHOT_SIZE, space_used)

    def _check_free_space(self, vg_name, mount_point):
        """
        Check for available space in volume group ``vg_name`` for the specified
        mount point.

        :param vg_name: The name of the volume group to check.
        :param mount_point: The mount point path to check.
        :returns: The space used on the mount point.
        :raises: ``SnapmNoSpaceError`` if the minimum snapshot size exceeds the
                 available space.
        """
        space_used = mount_point_space_used(mount_point)
        vg_free = vg_free_space(vg_name)
        snapshot_min_size = self._snapshot_min_size(space_used)
        if vg_free < snapshot_min_size:
            raise SnapmNoSpaceError(
                f"Volume group {vg_name} has insufficient free space to snapshot {mount_point}"
            )
        return space_used

    def check_create_snapshot(self, origin, snapset_name, timestamp, mount_point):
        (vg_name, lv_name) = origin.split("/")
        snapshot_name = format_snapshot_name(
            lv_name, snapset_name, timestamp, encode_mount_point(mount_point)
        )
        self._check_lvm_name(vg_name, snapshot_name)
        self._check_free_space(vg_name, mount_point)

    def create_snapshot(self, origin, snapset_name, timestamp, mount_point):
        (vg_name, lv_name) = origin.split("/")
        _log_debug(
            "Creating CoW snapshot for %s/%s mounted at %s",
            vg_name,
            lv_name,
            mount_point,
        )
        snapshot_name = format_snapshot_name(
            lv_name, snapset_name, timestamp, encode_mount_point(mount_point)
        )
        self._check_lvm_name(vg_name, snapshot_name)
        space_used = self._check_free_space(vg_name, mount_point)
        snapshot_size = self._snapshot_min_size(space_used)
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
            run(lvcreate_cmd, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(f"{LVCREATE_CMD} failed with: {err}") from err
        return Lvm2Snapshot(
            f"{vg_name}/{snapshot_name}",
            snapset_name,
            lv_name,
            timestamp,
            mount_point,
            self,
            vg_name,
            snapshot_name,
        )


class Lvm2Thin(_Lvm2):
    """
    LVM2 thin provisioning snapshot plugin.
    """

    name = "lvm2-thin"
    version = "0.1.0"

    def discover_snapshots(self):
        snapshots = []
        lvs_dict = get_lvs_json_report()
        for lv_dict in lvs_dict[LVS_REPORT][0][LVS_LV]:
            if filter_thin_snapshot(lv_dict):
                try:
                    fields = parse_snapshot_name(
                        lv_dict[LVS_LV_NAME], lv_dict[LVS_LV_ORIGIN]
                    )
                except ValueError:
                    continue
                if fields is not None:
                    full_name = f"{lv_dict[LVS_VG_NAME]}/{lv_dict[LVS_LV_NAME]}"
                    _log_debug_lvm2("Found %s snapshot: %s", self.name, full_name)
                    (snapset, timestamp, mount_point) = fields
                    snapshots.append(
                        Lvm2Snapshot(
                            full_name,
                            snapset,
                            f"{lv_dict[LVS_LV_ORIGIN]}",
                            timestamp,
                            mount_point,
                            self,
                            f"{lv_dict[LVS_VG_NAME]}",
                            f"{lv_dict[LVS_LV_NAME]}",
                            lv_dict=lv_dict,
                        )
                    )
        return snapshots

    def can_snapshot(self, mount_point):
        device = device_from_mount_point(mount_point)
        if not is_lvm_device(device):
            return False
        (vg_name, lv_name) = vg_lv_from_device_path(device)
        lvs_dict = get_lvs_json_report(f"{vg_name}/{lv_name}")
        lv_report = lvs_dict[LVS_REPORT][0][LVS_LV][0]
        lv_attr = lv_report[LVS_LV_ATTR]
        if lv_attr[0] != LVM_THIN_VOL_ATTR:
            return False
        return True

    def _check_free_space(self, vg_name, origin, mount_point):
        pool_name = pool_name_from_vg_lv(origin)
        space_used = mount_point_space_used(mount_point)
        pool_free = pool_free_space(vg_name, pool_name)
        if pool_free < space_used:
            raise SnapmNoSpaceError(
                f"Volume group thin pool {vg_name}/{pool_name} "
                f"has insufficient free space to snapshot {mount_point}"
            )

    def check_create_snapshot(self, origin, snapset_name, timestamp, mount_point):
        (vg_name, _) = origin.split("/")
        self._check_free_space(vg_name, origin, mount_point)

    def create_snapshot(self, origin, snapset_name, timestamp, mount_point):
        (vg_name, lv_name) = origin.split("/")
        _log_debug(
            "Creating thin snapshot for %s/%s mounted at %s",
            vg_name,
            lv_name,
            mount_point,
        )
        snapshot_name = format_snapshot_name(
            lv_name, snapset_name, timestamp, encode_mount_point(mount_point)
        )
        self._check_free_space(vg_name, origin, mount_point)

        lvcreate_cmd = [
            LVCREATE_CMD,
            LVCREATE_SNAPSHOT,
            LVCREATE_NAME,
            snapshot_name,
            origin,
        ]
        try:
            run(lvcreate_cmd, capture_output=True, check=True)
        except CalledProcessError as err:
            raise SnapmCalloutError(f"{LVCREATE_CMD} failed with: {err}") from err
        return Lvm2Snapshot(
            f"{vg_name}/{snapshot_name}",
            snapset_name,
            lv_name,
            timestamp,
            mount_point,
            self,
            vg_name,
            snapshot_name,
        )
