# Copyright Red Hat
#
# tests/fsdiff/_util.py - Fs difference engine test utilities.
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import os
import stat
from pathlib import Path
from snapm.fsdiff.treewalk import FsEntry
from snapm.fsdiff.filetypes import FileTypeInfo, FileTypeCategory

def make_entry(
    path_str,
    is_dir=False,
    is_symlink=False,
    size=1024,
    mtime=1600000000,
    mode=0o0,
    uid=1000,
    gid=1000,
    content_hash=None,
    mime_type="text/plain",
    category=FileTypeCategory.TEXT,
    strip_prefix="",
):
    """
    Factory to create FsEntry objects without touching disk.
    """
    def _mode_has_file_type(mode):
        if mode is not None and (
            any(
                (
                    stat.S_ISBLK(mode),
                    stat.S_ISCHR(mode),
                    stat.S_ISDIR(mode),
                    stat.S_ISFIFO(mode),
                    stat.S_ISLNK(mode),
                    stat.S_ISREG(mode),
                    stat.S_ISSOCK(mode),
                )
            )
        ):
            return True
        return False

    def _mode_has_permissions(mode):
        if mode is not None and not stat.S_IMODE(mode):
            return False
        return True

    if not _mode_has_file_type(mode):
        if is_dir:
            mode |= stat.S_IFDIR
        elif is_symlink:
            mode |= stat.S_IFLNK
        else:
            mode |= stat.S_IFREG

    if not _mode_has_permissions(mode):
        if is_dir:
            mode |= 0o755
        elif is_symlink:
            mode |= 0o777
        else:
            mode |= 0o644

    # Create a fake stat result
    # st_mode, st_ino, st_dev, st_nlink, st_uid, st_gid, st_size, st_atime, st_mtime, st_ctime
    stat_info = os.stat_result((
        mode, 12345, 2049, 1, uid, gid, size, mtime, mtime, mtime
    ))
    ft_info = None
    if not is_dir and not is_symlink:
        ft_info = FileTypeInfo(mime_type, "Mock File", category)

    entry = FsEntry(path_str, strip_prefix, stat_info, content_hash, ft_info)

    # Symlinks need a target for equality checks in ChangeDetector
    if is_symlink:
        entry.symlink_target = "/target/path"

    return entry
