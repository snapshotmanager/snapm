import tempfile
import subprocess
from pathlib import Path
import os

_LOSETUP_CMD = "losetup"

_VGCREATE_CMD = "vgcreate"
_LVCREATE_CMD = "lvcreate"
_VGREMOVE_CMD = "vgremove"
_VGCHANGE_CMD = "vgchange"
_LVS_CMD = "lvs"
_VGS_CMD = "vgs"
_LVM_CMD = "lvm"
_LVM_VERSION = "version"
_LVM_VERSION_STR = "LVM version"

_MOUNT_CMD = "mount"
_UMOUNT_CMD = "umount"

_MKFS_EXT4_CMD = "mkfs.ext4"

_VG_NAME = "test_vg0"

_THIN_POOL_NAME = "pool0"

_VAR_TMP = "/var/tmp"

# 12GiB
_LOOP_DEVICE_SIZE = 12 * 1024**3

# 1GiB
_LV_SIZE = 1024**3

# GiB
_THIN_POOL_SIZE = 2


def _version_string_to_tuple(version):
    return tuple(map(int, version.split(".")))


def _get_lvm_version():
    lvm_version_info = str.strip(
        subprocess.check_output([_LVM_CMD, _LVM_VERSION]).decode("utf8")
    )
    for line in lvm_version_info.splitlines():
        if _LVM_VERSION_STR in line:
            (_, _, version, _) = line.split()
            if "(" in version:
                version, _ = version.split("(", maxsplit=1)
            return _version_string_to_tuple(version)
    return _version_string_to_tuple("0.0.0")


def _lvm_supports_devices_file():
    version = _get_lvm_version()
    return version >= (2, 3, 12)


if _lvm_supports_devices_file():
    os.environ["LVM_SYSTEM_DIR"] = os.path.join(os.getcwd(), "tests/lvm")
else:
    os.environ["LVM_SYSTEM_DIR"] = os.path.join(os.getcwd(), "tests/lvm-nodevicesfile")


class LoopBackDevices(object):
    """
    Class for creating and managing Linux loop back devices for testing.
    """

    def __init__(self):
        self.dir = tempfile.mkdtemp("_snapm_loop_back", dir=_VAR_TMP)
        self.count = 0
        self.devices = []

    def create_devices(self, count):
        for index in range(count):
            backing_file = os.path.join(self.dir, f"image{index}")

            with open(backing_file, "ab") as file:
                file.truncate(_LOOP_DEVICE_SIZE)

            device = str.strip(
                subprocess.check_output(
                    [_LOSETUP_CMD, "--find", "--show", backing_file]
                ).decode("utf8")
            )
            self.devices.append((device, backing_file))
        self.count = count

    def destroy_devices(self):
        for device, backing_file in self.devices:
            subprocess.check_call([_LOSETUP_CMD, "--detach", device])
            os.remove(backing_file)
        self.devices = []
        self.count = 0

    def destroy_all(self):
        self.destroy_devices()
        os.rmdir(self.dir)

    def device_nodes(self):
        return [dev[0] for dev in self.devices]


class LvmLoopBacked(object):
    """
    Class to create LVM2 volume groups using loop devices.
    """

    def __init__(self, volumes, thin_volumes=None):
        """
        Initialize a new test volume group named ``_VG_NAME``

        :param volumes: A list of linear volumes to create. Each volume
        will be of ``_LV_SIZE``.
        :param thin_volumes: A list of thin volumes to create in the
                             volume group. Each volume will have a
                             virtual size of ``_LV_SIZE``.
        """
        self.mount_root = tempfile.mkdtemp("_snapm_mounts", dir=_VAR_TMP)

        # Create loopback device to back VG
        self._lb = LoopBackDevices()
        self._lb.create_devices(1)

        self._create_volume_group()

        if thin_volumes:
            self._create_thin_pool()

        self._create_and_mount_volumes(volumes, thin=False)
        self.volumes = volumes

        self._create_and_mount_volumes(thin_volumes, thin=True)
        self.thin_volumes = thin_volumes

    def _create_volume_group(self):
        _vgcreate_cmd = [_VGCREATE_CMD, _VG_NAME]
        _vgcreate_cmd.extend(self._lb.device_nodes())
        subprocess.check_call(_vgcreate_cmd)

    def _create_and_mount_volumes(self, volumes, thin=False):
        for lv in volumes:
            os.makedirs(os.path.join(self.mount_root, lv))
            self.lvcreate(lv, thin=thin)
            self.format(lv)
            self.mount(lv)

    def _create_thin_pool(self):
        """
        Create a thin pool of ``_THIN_POOL_SIZE``. The pool will be
        named ``_THIN_POOL_NAME``.
        """
        subprocess.check_call(
            [
                _LVCREATE_CMD,
                "--size",
                f"{_THIN_POOL_SIZE}g",
                "--thin",
                "--name",
                _THIN_POOL_NAME,
                _VG_NAME,
            ]
        )

    def all_volumes(self):
        return self.volumes + self.thin_volumes

    def lvcreate(self, name, thin=False):
        if not thin:
            subprocess.check_call(
                [_LVCREATE_CMD, "--size", f"{_LV_SIZE}b", "--name", name, _VG_NAME]
            )
        else:
            subprocess.check_call(
                [
                    _LVCREATE_CMD,
                    "--virtualsize",
                    f"{_LV_SIZE}b",
                    "--thin",
                    "--name",
                    name,
                    f"{_VG_NAME}/{_THIN_POOL_NAME}",
                ]
            )

    def format(self, name):
        subprocess.check_call([_MKFS_EXT4_CMD, f"/dev/{_VG_NAME}/{name}"])

    def mount(self, name):
        subprocess.check_call(
            [_MOUNT_CMD, f"/dev/{_VG_NAME}/{name}", f"{self.mount_root}/{name}"]
        )

    def mount_all(self):
        for lv in self.all_volumes():
            self.mount(lv)

    def umount(self, name):
        subprocess.check_call([_UMOUNT_CMD, f"{self.mount_root}/{name}"])

    def umount_all(self):
        for lv in self.all_volumes():
            self.umount(lv)

    def activate(self):
        subprocess.check_call([_VGCHANGE_CMD, "-ay", _VG_NAME])

    def deactivate(self):
        subprocess.check_call([_VGCHANGE_CMD, "-an", _VG_NAME])

    def mount_points(self):
        return [f"{self.mount_root}/{name}" for name in self.all_volumes()]

    def touch_path(self, relpath):
        path = Path(f"{self.mount_root}/{relpath}")
        path.touch()

    def test_path(self, relpath):
        path = Path(f"{self.mount_root}/{relpath}")
        return path.exists()

    def dump_lvs(self):
        subprocess.check_call([_LVS_CMD])

    def dump_vgs(self):
        subprocess.check_call([_VGS_CMD])

    def destroy(self):
        self.umount_all()
        for lv in self.all_volumes():
            os.rmdir(f"{self.mount_root}/{lv}")
        os.rmdir(self.mount_root)

        subprocess.check_call([_VGREMOVE_CMD, "--force", "--yes", _VG_NAME])

        self._lb.destroy_all()
