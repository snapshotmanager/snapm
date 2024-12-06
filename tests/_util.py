import tempfile
import subprocess
from pathlib import Path
import os

_LOSETUP_CMD = "losetup"

_UDEVADM = "udevadm"
_SETTLE = "settle"

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

# LVM2
_VG_NAME = "test_vg0"

_THIN_POOL_NAME = "pool0"

# 1GiB
_LV_SIZE = 1024**3

# 512MiB
_SNAPSHOT_SIZE = 512 * 2**20

# GiB
_THIN_POOL_SIZE = 6

# Stratis
_STRATIS_CMD = "stratis"
_POOL_CMD = "pool"
_FILESYSTEM_CMD = "filesystem"
_CREATE_CMD = "create"
_DESTROY_CMD = "destroy"
_LIST_CMD = "list"
_START_CMD = "start"
_STOP_CMD = "stop"
_NAME_ARG = "--name"
_SIZE_ARG = "--size"

_POOL_NAME = "pool1"

_FS_SIZE = "1GiB"

# Path in which to create temporary files
_VAR_TMP = "/var/tmp"

# 20GiB
_LOOP_DEVICE_SIZE = 20 * 2**30


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
            self._lvcreate(lv, thin=thin)
            self.format(lv)
            self.mount(lv)

    def _lvcreate(self, name, thin=False):
        if not thin:
            subprocess.check_call(
                [_LVCREATE_CMD, _SIZE_ARG, f"{_LV_SIZE}b", "--name", name, _VG_NAME]
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

    def _create_thin_pool(self):
        """
        Create a thin pool of ``_THIN_POOL_SIZE``. The pool will be
        named ``_THIN_POOL_NAME``.
        """
        subprocess.check_call(
            [
                _LVCREATE_CMD,
                _SIZE_ARG,
                f"{_THIN_POOL_SIZE}g",
                "--thin",
                "--name",
                _THIN_POOL_NAME,
                _VG_NAME,
            ]
        )

    def _create_cow_snapshot(self, origin, name):
        """
        Create an LVM2 CoW snapshot in the test VG.
        """
        subprocess.check_call(
            [
                _LVCREATE_CMD,
                "--snapshot",
                _SIZE_ARG,
                f"{_SNAPSHOT_SIZE}b",
                "--name",
                f"{name}",
                f"{_VG_NAME}/{origin}",
            ]
        )

    def _create_thin_snapshot(self, origin, name):
        """
        Create an LVM2 thin snapshot in the test VG.
        """
        subprocess.check_call(
            [
                _LVCREATE_CMD,
                "--snapshot",
                "--name",
                f"{name}",
                f"{_VG_NAME}/{origin}",
            ]
        )

    def create_snapshot(self, origin, name):
        """
        Create a snapshot of volume `origin` in the test VG.
        """
        if origin in self.volumes:
            return self._create_cow_snapshot(origin, name)
        elif origin in self.thin_volumes:
            return self._create_thin_snapshot(origin, name)
        raise ValueError(f"Unknown origin: {origin}")

    def all_volumes(self):
        return self.volumes + self.thin_volumes

    def format(self, name):
        subprocess.check_call([_MKFS_EXT4_CMD, f"/dev/{_VG_NAME}/{name}"])

    def make_mount_point(self, name):
        os.makedirs(os.path.join(self.mount_root, name))

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

    def block_devs(self):
        return [f"/dev/{_VG_NAME}/{name}" for name in self.all_volumes()]

    def touch_path(self, relpath):
        path = Path(f"{self.mount_root}/{relpath}")
        path.touch()

    def test_path(self, relpath):
        path = Path(f"{self.mount_root}/{relpath}")
        return path.exists()

    def dump_lvs(self):
        subprocess.check_call([_LVS_CMD, "-a"])

    def dump_vgs(self):
        subprocess.check_call([_VGS_CMD])

    def destroy(self):
        self.umount_all()
        for lv in self.all_volumes():
            os.rmdir(f"{self.mount_root}/{lv}")
        for subdir in os.listdir(self.mount_root):
            os.rmdir(os.path.join(self.mount_root, subdir))
        os.rmdir(self.mount_root)

        subprocess.check_call([_VGREMOVE_CMD, "--force", "--yes", _VG_NAME])

        self._lb.destroy_all()


class StratisLoopBacked(object):
    """
    Class to create Stratis pools using loop devices.
    """

    def __init__(self, volumes):
        """
        Initialize a new test pool named ``_POOL_NAME``

        :param volumes: A list of linear volumes to create. Each volume
        will be of ``_FS_SIZE``.
        :param thin_volumes: A list of thin volumes to create in the
                             volume group. Each volume will have a
                             virtual size of ``_FS_SIZE``.
        """
        self.mount_root = tempfile.mkdtemp("_snapm_mounts", dir=_VAR_TMP)

        # Create loopback device to back pool
        self._lb = LoopBackDevices()
        self._lb.create_devices(1)

        self._create_pool()

        self._create_and_mount_volumes(volumes)
        self.volumes = volumes

    def _create_pool(self):
        _stratis_pool_create_cmd = [_STRATIS_CMD, _POOL_CMD, _CREATE_CMD, _POOL_NAME]
        _stratis_pool_create_cmd.extend(self._lb.device_nodes())
        subprocess.check_call(_stratis_pool_create_cmd)

    def _create_and_mount_volumes(self, volumes):
        for fs in volumes:
            os.makedirs(os.path.join(self.mount_root, fs))
            self._fs_create(fs)
            subprocess.check_call(
                [
                    _UDEVADM,
                    _SETTLE,
                ]
            )
            self.mount(fs)

    def _fs_create(self, name, thin=False):
        subprocess.check_call(
            [
                _STRATIS_CMD,
                _FILESYSTEM_CMD,
                _CREATE_CMD,
                _SIZE_ARG,
                _FS_SIZE,
                _POOL_NAME,
                name,
            ]
        )

    def create_snapshot(self, origin, name):
        """
        Create a snapshot of volume `origin` in the test pool.
        """
        if origin in self.volumes:
            return subprocess.check_call(
                [
                    _STRATIS_CMD,
                    _FILESYSTEM_CMD,
                    "snapshot",
                    _POOL_NAME,
                    origin,
                    name,
                ]
            )
        raise ValueError(f"Unknown origin: {origin}")

    def all_volumes(self):
        return self.volumes

    def mount(self, name):
        subprocess.check_call(
            [_MOUNT_CMD, f"/dev/stratis/{_POOL_NAME}/{name}", f"{self.mount_root}/{name}"]
        )

    def mount_all(self):
        for fs in self.all_volumes():
            self.mount(fs)

    def umount(self, name):
        subprocess.check_call([_UMOUNT_CMD, f"{self.mount_root}/{name}"])

    def umount_all(self):
        for fs in self.all_volumes():
            self.umount(fs)

    def mount_points(self):
        return [f"{self.mount_root}/{name}" for name in self.all_volumes()]

    def block_devs(self):
        return [f"/dev/stratis/{_POOL_NAME}/{name}" for name in self.all_volumes()]

    def touch_path(self, relpath):
        path = Path(f"{self.mount_root}/{relpath}")
        path.touch()

    def test_path(self, relpath):
        path = Path(f"{self.mount_root}/{relpath}")
        return path.exists()

    def dump_fs(self):
        subprocess.check_call([_STRATIS_CMD, _FILESYSTEM_CMD])

    def dump_pools(self):
        subprocess.check_call([_STRATIS_CMD, _POOL_CMD])

    def start_pool(self):
        subprocess.run([_STRATIS_CMD, _POOL_CMD, _START_CMD, _NAME_ARG, _POOL_NAME], check=True)
        subprocess.run(["udevadm", "settle"], check=True)

    def stop_pool(self):
        subprocess.run([_STRATIS_CMD, _POOL_CMD, _STOP_CMD, _NAME_ARG, _POOL_NAME], check=True)

    def _destroy_one_filesystem(self, pool, filesystem):
        subprocess.check_call(
            [
                _STRATIS_CMD,
                _FILESYSTEM_CMD,
                _DESTROY_CMD,
                pool,
                filesystem,
            ]
        )

    def _destroy_all_filesystems(self):
        stratis_filesystem_cmd_args = [
            _STRATIS_CMD,
            _FILESYSTEM_CMD,
            _LIST_CMD,
            _POOL_NAME,
        ]

        stratis_filesystem_cmd = subprocess.run(
            stratis_filesystem_cmd_args,
            capture_output=True
        )

        for line in stratis_filesystem_cmd.stdout.decode('utf8').splitlines():
            if "Pool" in line and "Filesystem" in line:
                continue
            fields = line.split()
            self._destroy_one_filesystem(_POOL_NAME, fields[1])

    def destroy(self):
        self.umount_all()
        for fs in self.all_volumes():
            os.rmdir(f"{self.mount_root}/{fs}")
        os.rmdir(self.mount_root)

        for fs in self.all_volumes():
            self._destroy_one_filesystem(_POOL_NAME, fs)

        # Cleanup left-over file systems
        self._destroy_all_filesystems()

        subprocess.check_call([_STRATIS_CMD, _POOL_CMD, _DESTROY_CMD, _POOL_NAME])

        self._lb.destroy_all()
