# Copyright Red Hat
#
# tests/test_mountstate.py - Snapshot Manager mount state record tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import snapm
import snapm.manager._mountstate as mountstate


def _systemd_escape(path):
    """Return the escaped name systemd-escape(1) gives ``path``."""
    if shutil.which("systemd-escape") is None:
        return None
    result = subprocess.run(
        ["systemd-escape", "--path", path],
        capture_output=True,
        encoding="utf8",
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


class SystemdEscapePathTests(unittest.TestCase):
    """
    Tests for the path escaping used to name mount state records.
    """

    #: Paths that must escape exactly as systemd escapes them, and the name
    #: systemd gives each.
    PATHS = {
        "/": "-",
        "/run/snapm/mounts/set0": "run-snapm-mounts-set0",
        "/srv/rescue": "srv-rescue",
        "/mnt/a b": r"mnt-a\x20b",
        "/mnt/a-b": r"mnt-a\x2db",
        "/mnt/.hidden": "mnt-.hidden",
        "/.hidden": r"\x2ehidden",
        "/mnt/é": r"mnt-\xc3\xa9",
        "/mnt/a\nb": r"mnt-a\x0ab",
        "//mnt//x": "mnt-x",
        "/..": "-",
    }

    def test_escapes_paths_as_systemd_does(self):
        """Test the escaped names against the expected values."""
        for path, expected in self.PATHS.items():
            with self.subTest(path=path):
                self.assertEqual(mountstate.systemd_escape_path(path), expected)

    def test_no_escaped_name_begins_with_a_dot(self):
        """Test the invariant the temporary record prefix reserves.

        A record being written is marked with a leading '.tmp-', which
        only works because no escaped name can begin with a dot.
        """
        for path in self.PATHS:
            with self.subTest(path=path):
                self.assertFalse(mountstate.systemd_escape_path(path).startswith("."))

    def test_agrees_with_systemd_escape(self):
        """Test the escaped names against systemd-escape(1) itself."""
        for path in self.PATHS:
            with self.subTest(path=path):
                expected = _systemd_escape(path)
                if expected is None:
                    self.skipTest("systemd-escape is not available")
                self.assertEqual(mountstate.systemd_escape_path(path), expected)

    def test_escapes_a_path_too_long_to_name_a_file(self):
        """Test that an over-long path still yields a usable file name."""
        base = "/mnt/" + "a" * 300
        names = {
            mountstate.systemd_escape_path(base + suffix) for suffix in ("/one", "/two")
        }

        # Both names fit in a file name with room left for the prefix a
        # record wears while it is being written, and the shared prefix
        # does not make the two paths collide.
        self.assertEqual(len(names), 2)
        for name in names:
            self.assertLessEqual(len(f".tmp-{name}".encode("utf8")), 255)

    def test_records_an_over_long_path(self):
        """Test that a path too long to name a file is still recordable."""
        mount_path = "/mnt/" + "a" * 300
        state = mountstate.MountStateDir(tempfile.mkdtemp(dir="/var/tmp"))
        self.addCleanup(shutil.rmtree, state.path)
        state.add(
            mountstate.MountStateRecord(
                mount_path=mount_path,
                snapset_name="set0",
                snapset_uuid="00000000-0000-5000-a000-000000000000",
                root_device="/dev/testvg/set0-root",
                root_devno=64513,
                created_mount_point=True,
            )
        )

        reread = mountstate.MountStateDir(state.path)
        self.assertEqual(
            [record.mount_path for record in reread.records], [mount_path]
        )


class MountStateLockTests(unittest.TestCase):
    """
    Tests for the lock that serialises changes to the mount state.
    """

    def setUp(self):
        dir_obj = tempfile.TemporaryDirectory(prefix="snapm_mount_lock_")
        self.addCleanup(dir_obj.cleanup)
        self.path = dir_obj.name

    def test_lock_creates_the_lock_file(self):
        """Test that taking the lock creates a lock file naming the holder."""
        with mountstate.mount_state_lock(self.path):
            lockfile = os.path.join(self.path, "mount-state.lock")
            self.assertTrue(os.path.exists(lockfile))
            with open(lockfile, "r", encoding="utf8") as f:
                self.assertEqual(f.read().strip(), str(os.getpid()))

    def test_lock_excludes_a_second_holder(self):
        """Test that the lock is exclusive and gives up after the timeout.

        An flock() is held by the open file description, so a second
        acquisition conflicts with the first even in the same process.
        """
        with mountstate.mount_state_lock(self.path):
            with self.assertRaises(snapm.SnapmBusyError) as caught:
                with mountstate.mount_state_lock(self.path, timeout=0.2):
                    self.fail("Took the mount state lock twice")

        message = str(caught.exception)
        self.assertIn("mount state lock", message)
        # The message names the process to go and look at.
        self.assertIn(f"held by pid {os.getpid()}", message)

    def test_lock_timeout_without_a_holder_pid(self):
        """Test that a holder that has not written its pid is not named.

        A holder writes its pid once it has the lock, so the file is
        briefly empty: the message says nothing rather than guessing.
        """
        lockfile = os.path.join(self.path, "mount-state.lock")
        fd = os.open(lockfile, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        with self.assertRaises(snapm.SnapmBusyError) as caught:
            with mountstate.mount_state_lock(self.path, timeout=0.2):
                self.fail("Took a mount state lock that was held")

        self.assertNotIn("held by", str(caught.exception))

    def test_lock_is_released_on_exit(self):
        """Test that the lock can be taken again once it is released."""
        with mountstate.mount_state_lock(self.path, timeout=0.2):
            pass
        with mountstate.mount_state_lock(self.path, timeout=0.2):
            pass

    def test_lock_is_released_after_an_error(self):
        """Test that an exception inside the lock still releases it."""
        with self.assertRaises(ValueError):
            with mountstate.mount_state_lock(self.path, timeout=0.2):
                raise ValueError("boom")
        with mountstate.mount_state_lock(self.path, timeout=0.2):
            pass


class MountStateRecordTests(unittest.TestCase):
    """
    Tests for the mount state record type.
    """

    FIELDS = {
        "mount_path": "/run/snapm/mounts/set0",
        "snapset_name": "set0",
        "snapset_uuid": "00000000-0000-5000-a000-000000000000",
        "root_device": "/dev/testvg/set0-root",
        "root_devno": 64513,
        "created_mount_point": True,
    }

    def test_from_dict(self):
        """Test building a record from the fields read from a file."""
        record = mountstate.MountStateRecord.from_dict(dict(self.FIELDS))

        for key, value in self.FIELDS.items():
            self.assertEqual(getattr(record, key), value)

    def test_from_dict_reads_the_mount_devno(self):
        """Test that the device number noted at mount time is read back."""
        fields = dict(self.FIELDS, mount_devno=64514)
        record = mountstate.MountStateRecord.from_dict(fields)
        self.assertEqual(record.mount_devno, 64514)

    def test_from_dict_defaults_an_unverified_mount_devno(self):
        """Test that a record written before its mount has no devno.

        The field is filled in once the mount has been made, so a record
        without one describes a mount that was never confirmed.
        """
        self.assertNotIn("mount_devno", self.FIELDS)
        record = mountstate.MountStateRecord.from_dict(dict(self.FIELDS))
        self.assertIsNone(record.mount_devno)

        fields = dict(self.FIELDS, mount_devno=None)
        self.assertIsNone(mountstate.MountStateRecord.from_dict(fields).mount_devno)

    def test_from_dict_rejects_a_bad_mount_devno(self):
        """Test that a mount device number that is not a number is rejected."""
        fields = dict(self.FIELDS, mount_devno="not-a-number")
        with self.assertRaisesRegex(ValueError, "Malformed"):
            mountstate.MountStateRecord.from_dict(fields)

    def test_from_dict_rejects_a_missing_field(self):
        """Test that a record missing a field is rejected."""
        for key in self.FIELDS:
            with self.subTest(key=key):
                fields = dict(self.FIELDS)
                del fields[key]
                with self.assertRaisesRegex(ValueError, "Malformed"):
                    mountstate.MountStateRecord.from_dict(fields)

    def test_from_dict_rejects_a_bad_device_number(self):
        """Test that a device number that is not a number is rejected."""
        fields = dict(self.FIELDS, root_devno="not-a-number")
        with self.assertRaisesRegex(ValueError, "Malformed"):
            mountstate.MountStateRecord.from_dict(fields)

    def test_from_dict_rejects_a_relative_mount_path(self):
        """Test that a record naming a relative path is rejected."""
        fields = dict(self.FIELDS, mount_path="run/snapm/mounts/set0")
        with self.assertRaisesRegex(ValueError, "relative mount path"):
            mountstate.MountStateRecord.from_dict(fields)


class MountStateDirTests(unittest.TestCase):
    """
    Tests for the mount state record directory.
    """

    def setUp(self):
        dir_obj = tempfile.TemporaryDirectory(prefix="snapm_mount_state_")
        self.addCleanup(dir_obj.cleanup)
        self.path = dir_obj.name
        self.state = mountstate.MountStateDir(self.path)

    @staticmethod
    def _record(mount_path, name="set0", created=True):
        """Return a record describing a mount of ``name`` at ``mount_path``."""
        return mountstate.MountStateRecord(
            mount_path=mount_path,
            snapset_name=name,
            snapset_uuid=f"00000000-0000-5000-a000-{hash(name) & 0xFFFFFFFFFFFF:012x}",
            root_device=f"/dev/testvg/{name}-root",
            root_devno=64513,
            created_mount_point=created,
        )

    def test_add_names_the_record_for_its_path(self):
        """Test that a record is named for the path it describes."""
        record = self._record("/run/snapm/mounts/set0")
        self.state.add(record)

        self.assertEqual(
            os.listdir(self.path), ["run-snapm-mounts-set0"]
        )

    def test_add_writes_the_unescaped_path(self):
        """Test that the record holds the path as it really is."""
        record = self._record("/mnt/a b")
        self.state.add(record)

        with open(
            os.path.join(self.path, r"mnt-a\x20b"), "r", encoding="utf8"
        ) as record_file:
            self.assertEqual(json.load(record_file)["mount_path"], "/mnt/a b")

    def test_records_survive_a_reread(self):
        """Test that records written here are read back by a new reader."""
        first = self._record("/run/snapm/mounts/set0", name="set0")
        second = self._record("/srv/rescue", name="set1", created=False)
        self.state.add(first)
        self.state.add(second)

        reread = mountstate.MountStateDir(self.path)

        # Records come back in mount path order.
        self.assertEqual(reread.records, [first, second])
        self.assertEqual(reread.get("/srv/rescue"), second)
        self.assertIsNone(reread.get("/no/such/path"))

    def test_add_replaces_a_record_for_the_same_path(self):
        """Test that re-recording a path replaces the earlier record."""
        self.state.add(self._record("/srv/rescue", name="set0"))
        self.state.add(self._record("/srv/rescue", name="set1"))

        records = mountstate.MountStateDir(self.path).records
        self.assertEqual([record.snapset_name for record in records], ["set1"])

    def test_discard_removes_the_record(self):
        """Test that discarding a record removes it from the directory."""
        self.state.add(self._record("/srv/rescue"))
        self.state.discard("/srv/rescue")

        self.assertEqual(self.state.records, [])
        self.assertEqual(os.listdir(self.path), [])
        self.assertEqual(mountstate.MountStateDir(self.path).records, [])

    def test_discard_ignores_an_unrecorded_path(self):
        """Test that discarding a path with no record does nothing."""
        self.state.add(self._record("/srv/rescue"))
        self.state.discard("/no/such/path")

        self.assertEqual(len(self.state.records), 1)

    def test_load_ignores_a_partial_record(self):
        """Test that a record left half-written is not read, nor removed.

        Reading the directory never writes to it: a temporary file may
        belong to a command that is writing a record right now, so
        removing it is left to reap_partial_records().
        """
        self.state.add(self._record("/srv/rescue"))
        partial = os.path.join(self.path, ".tmp-mnt-interrupted")
        with open(partial, "w", encoding="utf8") as record_file:
            record_file.write("{\"mount_path\": \"/mnt/inte")

        reread = mountstate.MountStateDir(self.path)

        self.assertEqual(
            [record.mount_path for record in reread.records], ["/srv/rescue"]
        )
        self.assertTrue(os.path.exists(partial))

    def test_reap_removes_a_partial_record(self):
        """Test that reaping cleans up a record left half-written."""
        self.state.add(self._record("/srv/rescue"))
        partial = os.path.join(self.path, ".tmp-mnt-interrupted")
        with open(partial, "w", encoding="utf8") as record_file:
            record_file.write("{\"mount_path\": \"/mnt/inte")

        self.state.reap_partial_records()

        self.assertFalse(os.path.exists(partial))
        # The records proper are left alone.
        reread = mountstate.MountStateDir(self.path)
        self.assertEqual(
            [record.mount_path for record in reread.records], ["/srv/rescue"]
        )

    def test_a_record_named_like_a_temporary_file_survives(self):
        """Test that a path ending in the temporary marker is still a record.

        A set named 'backup.tmp', or a mount root ending in '.tmp',
        escapes to a name ending in '.tmp': that must not read as a
        record that is half-written, or the mount it describes is
        orphaned by the next command that reaps.
        """
        record = self._record("/run/snapm/mounts/backup.tmp", name="backup.tmp")
        self.state.add(record)

        self.assertEqual(os.listdir(self.path), ["run-snapm-mounts-backup.tmp"])

        reread = mountstate.MountStateDir(self.path)
        self.assertEqual(reread.records, [record])

        reread.reap_partial_records()
        self.assertEqual(mountstate.MountStateDir(self.path).records, [record])

    def test_load_skips_an_unreadable_record(self):
        """Test that a malformed record is skipped, not fatal."""
        self.state.add(self._record("/srv/rescue"))
        with open(os.path.join(self.path, "junk"), "w", encoding="utf8") as junk:
            junk.write("not json at all\n")
        with open(os.path.join(self.path, "empty"), "w", encoding="utf8") as empty:
            empty.write("{}\n")

        reread = mountstate.MountStateDir(self.path)

        self.assertEqual(
            [record.mount_path for record in reread.records], ["/srv/rescue"]
        )

    def test_missing_directory_raises_path_error(self):
        """Test that an unreadable state directory is reported."""
        with self.assertRaisesRegex(snapm.SnapmPathError, "not accessible"):
            mountstate.MountStateDir(os.path.join(self.path, "nosuchdir"))

    def test_unwritable_directory_raises_path_error(self):
        """Test that a record that cannot be written is reported."""
        os.chmod(self.path, 0o500)
        self.addCleanup(os.chmod, self.path, 0o700)

        if os.geteuid() == 0:
            self.skipTest("root may write to a read-only directory")

        with self.assertRaisesRegex(snapm.SnapmPathError, "Could not write"):
            self.state.add(self._record("/srv/rescue"))

    def test_repr(self):
        """Test the MountStateDir string representation."""
        self.assertEqual(repr(self.state), f"MountStateDir(path='{self.path}')")
