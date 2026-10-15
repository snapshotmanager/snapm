# Copyright Red Hat
#
# snapm/manager/_mountstate.py - Snapshot Manager mount state records
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
"""
Provenance records for snapshot set mounts.

A snapshot set mount cannot be recognised from the mount table alone. The
devices a set is made of are the only thing in the table that is distinctive
about it, and an administrator who mounts a member by hand produces a mount
indistinguishable from one snapm made (#1025). Unmounting that mount, or
removing the directory it is mounted on, is not snapm's to do.

Each mount snapm makes is therefore recorded here as it is made, and only a
mount with a record is one snapm will act on. A record names:

- the path the snapshot set is mounted at, unescaped
- the name and UUID of the snapshot set mounted there
- the device mounted at that path, and its device number
- whether snapm created the directory and should remove it again

The records live in the runtime directory, so they last exactly as long as
the mounts they describe: a reboot takes both away together. Within a boot
they outlive the process that wrote them, so a mount made by one snapm
command is recognised by the next.

Each record is named for the path it describes, escaped the way systemd
escapes a path into a unit name, so that the record for a path can be found
directly and an administrator listing the directory can see what is mounted
where. The path inside the record is the unescaped one, and is the
authoritative copy: nothing here has to reverse the escaping.
"""
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from hashlib import sha256
import logging
import string
import json
import os

from snapm import (
    SNAPM_SUBSYSTEM_MOUNTS,
    SnapmPathError,
)

_log = logging.getLogger(__name__)

_log_debug = _log.debug
_log_info = _log.info
_log_warn = _log.warning
_log_error = _log.error


def _log_debug_mounts(msg, *args, **kwargs):
    """A wrapper for mounts subsystem debug logs."""
    _log.debug(msg, *args, extra={"subsystem": SNAPM_SUBSYSTEM_MOUNTS}, **kwargs)


#: The characters systemd leaves alone when escaping a unit name.
_SYSTEMD_VALID_CHARS = string.ascii_letters + string.digits + ":_."

#: The longest file name permitted in a record directory.
_NAME_MAX = 255

#: Suffix for a record that is still being written.
_TEMP_SUFFIX = ".tmp"

#: The longest record name that still leaves room for the suffix a record
#: wears while it is being written.
_RECORD_NAME_MAX = _NAME_MAX - len(_TEMP_SUFFIX)

#: The number of hexadecimal digits of path digest appended to a record name
#: that has to be truncated to fit.
_DIGEST_CHARS = 16


def systemd_escape_path(path: str) -> str:
    r"""
    Escape ``path`` the way systemd escapes a path into a unit name.

    Leading and trailing '/' are dropped, the remaining '/' separators
    become '-', and every character outside ``[a-zA-Z0-9:_.]`` is replaced
    by a ``\xNN`` escape of each of its UTF-8 bytes. A leading '.' is
    escaped too, so that no name begins with a dot. The root directory
    escapes to '-'.

    A path long enough to escape past the file name limit is truncated and
    given a digest of the whole path, so that two long paths sharing a
    prefix still escape to different names. The escaped name is only a key:
    the record it names carries the path itself.

    :param path: The absolute path to escape.
    :type path: ``str``
    :returns: The escaped name for ``path``.
    :rtype: ``str``
    """
    # systemd simplifies the path before escaping it: '//', '.' and '..'
    # components do not reach the escaping proper. Mount paths are already
    # resolved by the time they are recorded, so this only keeps the escaping
    # in step with systemd's for a path that arrives unresolved.
    stripped = os.path.normpath(path).strip("/")
    if not stripped:
        return "-"

    escaped = []
    for index, char in enumerate(stripped):
        if char == "/":
            escaped.append("-")
        elif char in _SYSTEMD_VALID_CHARS and not (index == 0 and char == "."):
            escaped.append(char)
        else:
            escaped.extend(f"\\x{byte:02x}" for byte in char.encode("utf8"))
    name = "".join(escaped)

    if len(name.encode("utf8")) > _RECORD_NAME_MAX:
        digest = sha256(path.encode("utf8")).hexdigest()[:_DIGEST_CHARS]
        keep = _RECORD_NAME_MAX - len(digest) - 1
        # Truncate on a byte boundary that is still valid UTF-8.
        name = name.encode("utf8")[:keep].decode("utf8", "ignore")
        name = f"{name}-{digest}"
    return name


@dataclass
class MountStateRecord:
    """
    A record that snapm mounted a snapshot set at a given path.
    """

    #: The path the snapshot set is mounted at.
    mount_path: str

    #: The name of the snapshot set mounted at ``mount_path``.
    snapset_name: str

    #: The UUID of the snapshot set mounted at ``mount_path``.
    snapset_uuid: str

    #: The path to the device mounted at ``mount_path``. This is the device
    #: returned by ``find_snapset_root()`` when the set was mounted, and is
    #: recorded so that discovery does not have to derive it a second time
    #: and reach a different answer (#1031).
    root_device: str

    #: The device number of ``root_device`` when the set was mounted, as
    #: returned by ``os.stat().st_rdev``. Verification matches on this
    #: rather than on ``root_device``, which can be renamed (#1030).
    root_devno: int

    #: Whether snapm is responsible for the directory at ``mount_path`` and
    #: should remove it when the set is unmounted. This is true of the
    #: default mount points snapm creates in the mounts directory, including
    #: one left behind by an earlier mount and re-used, and false of a mount
    #: point nominated with ``--mount-root``, which belongs to whoever made
    #: it.
    created_mount_point: bool

    @classmethod
    def from_dict(cls, fields: Dict) -> "MountStateRecord":
        """
        Build a ``MountStateRecord`` from the fields read from a record
        file.

        :param fields: The decoded contents of a record file.
        :type fields: ``Dict``
        :returns: The record described by ``fields``.
        :rtype: ``MountStateRecord``
        :raises ValueError: If a field is missing or of the wrong type.
        """
        try:
            record = cls(
                mount_path=str(fields["mount_path"]),
                snapset_name=str(fields["snapset_name"]),
                snapset_uuid=str(fields["snapset_uuid"]),
                root_device=str(fields["root_device"]),
                root_devno=int(fields["root_devno"]),
                created_mount_point=bool(fields["created_mount_point"]),
            )
        except (KeyError, TypeError, ValueError) as err:
            raise ValueError(f"Malformed mount state record: {err}") from err
        if not record.mount_path.startswith("/"):
            raise ValueError(
                f"Mount state record has a relative mount path: {record.mount_path}"
            )
        return record


class MountStateDir:
    """
    The set of mount state records in a runtime directory.

    Records are read once when the directory is opened and kept in memory
    afterwards: the directory is only written by snapm, under the manager
    lock, so the copy in memory and the copy on disk cannot diverge while a
    command runs.
    """

    def __init__(self, path: str):
        """
        Open the record directory at ``path`` and read the records in it.

        :param path: The directory holding the mount state records.
        :type path: ``str``
        :raises SnapmPathError: If the directory cannot be read.
        """
        self.path = path
        self._records: Dict[str, MountStateRecord] = {}
        self.load()

    def _record_path(self, mount_path: str) -> str:
        """
        Return the path to the record file describing ``mount_path``.

        :param mount_path: The mount path to build a record path for.
        :type mount_path: ``str``
        :returns: The path to the record file.
        :rtype: ``str``
        """
        return os.path.join(self.path, systemd_escape_path(mount_path))

    def load(self) -> None:
        """
        Read every record in the directory, discarding anything held from a
        previous call.

        A file that cannot be read or does not parse is skipped with a
        warning rather than failing the command: it describes a mount that
        snapm can no longer account for, and the remaining records still
        describe mounts it can.

        :raises SnapmPathError: If the directory cannot be read.
        """
        self._records = {}
        try:
            names = os.listdir(self.path)
        except OSError as err:
            raise SnapmPathError(
                f"Mount state directory is not accessible: {self.path}"
            ) from err

        for name in names:
            path = os.path.join(self.path, name)
            if name.endswith(_TEMP_SUFFIX):
                # A record a previous command was interrupted partway
                # through writing. It describes nothing: the rename that
                # would have made it a record never happened.
                _log_debug_mounts("Removing partial mount state record '%s'", path)
                try:
                    os.unlink(path)
                except OSError as err:  # pragma: no cover
                    _log_warn("Could not remove '%s': %s", path, err)
                continue
            try:
                with open(path, "r", encoding="utf8") as record_file:
                    record = MountStateRecord.from_dict(json.load(record_file))
            except (OSError, ValueError) as err:
                _log_warn("Ignoring unreadable mount state record '%s': %s", path, err)
                continue
            _log_debug_mounts(
                "Read mount state record for %s at '%s'",
                record.snapset_name,
                record.mount_path,
            )
            self._records[record.mount_path] = record

    @property
    def records(self) -> List[MountStateRecord]:
        """
        The records in this directory, ordered by mount path.

        :returns: A list of ``MountStateRecord`` objects.
        :rtype: ``List[MountStateRecord]``
        """
        return [self._records[path] for path in sorted(self._records)]

    def get(self, mount_path: str) -> Optional[MountStateRecord]:
        """
        Return the record describing ``mount_path``.

        :param mount_path: The mount path to look up.
        :type mount_path: ``str``
        :returns: The record for ``mount_path``, or ``None`` if there is
                  none.
        :rtype: ``Optional[MountStateRecord]``
        """
        return self._records.get(mount_path)

    def add(self, record: MountStateRecord) -> None:
        """
        Write ``record`` to the directory, replacing any record describing
        the same mount path.

        The record is written to a temporary file and renamed into place, so
        that a command interrupted partway through writing leaves either the
        previous record or none, never a truncated one.

        :param record: The record to write.
        :type record: ``MountStateRecord``
        :raises SnapmPathError: If the record cannot be written.
        """
        path = self._record_path(record.mount_path)
        temp_path = f"{path}{_TEMP_SUFFIX}"
        try:
            with open(temp_path, "w", encoding="utf8") as record_file:
                json.dump(asdict(record), record_file, indent=4, sort_keys=True)
                record_file.write("\n")
                record_file.flush()
                os.fsync(record_file.fileno())
            os.rename(temp_path, path)
        except OSError as err:
            try:
                os.unlink(temp_path)
            except OSError:  # pragma: no cover
                pass
            raise SnapmPathError(
                f"Could not write mount state record '{path}': {err}"
            ) from err
        _log_debug_mounts(
            "Wrote mount state record for %s at '%s'",
            record.snapset_name,
            record.mount_path,
        )
        self._records[record.mount_path] = record

    def discard(self, mount_path: str) -> None:
        """
        Remove the record describing ``mount_path``, if there is one.

        :param mount_path: The mount path to discard the record for.
        :type mount_path: ``str``
        """
        if self._records.pop(mount_path, None) is None:
            return
        path = self._record_path(mount_path)
        try:
            os.unlink(path)
        except OSError as err:  # pragma: no cover
            _log_warn("Could not remove mount state record '%s': %s", path, err)
            return
        _log_debug_mounts("Discarded mount state record for '%s'", mount_path)

    def __repr__(self):
        """
        Return a machine readable string representation of this
        ``MountStateDir``.
        """
        return f"MountStateDir(path='{self.path}')"


__all__ = [
    "MountStateDir",
    "MountStateRecord",
    "systemd_escape_path",
]
