# Copyright Red Hat
#
# tests/fsdiff/test_cache.py - Diff cache tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
import os
import stat
import lzma
import pickle
from unittest.mock import MagicMock, patch, mock_open
from uuid import uuid4

try:
    import zstandard as zstd
    _HAVE_ZSTD = True
except ModuleNotFoundError:
    _HAVE_ZSTD = False

from snapm import SnapmSystemError, SnapmNotFoundError, SnapmInvalidIdentifierError
from snapm.fsdiff import cache
from snapm.fsdiff.options import DiffOptions
from snapm.fsdiff.engine import FsDiffRecord, FsDiffResults

class TestCache(unittest.TestCase):
    def setUp(self):
        self.mount_a = MagicMock()
        self.mount_a.name = "root"
        self.mount_a.snapset = None  # Triggers _root_uuid

        self.mount_b = MagicMock()
        self.mount_b.name = "snap1"
        self.mount_b.snapset.uuid = uuid4()

        self.results = MagicMock(spec=FsDiffResults)
        self.results.timestamp = 1600000000
        self.results.options = DiffOptions()

    @patch("os.makedirs")
    @patch("os.path.exists")
    @patch("os.lstat")
    @patch("os.stat")
    @patch("os.chmod")
    def test_check_cache_dir_success(self, mock_chmod, mock_stat, mock_lstat, mock_exists, mock_makedirs):
        # Case 1: Directory does not exist, create it
        mock_exists.return_value = False
        mock_stat.return_value.st_mode = 0o700
        cache._check_cache_dir("/tmp/cache", 0o700, "test")
        mock_makedirs.assert_called_with("/tmp/cache", mode=0o700, exist_ok=True)

        # Case 2: Directory exists, permissions are correct
        mock_exists.return_value = True
        mock_lstat.return_value.st_mode = stat.S_IFDIR | 0o700
        mock_stat.return_value.st_mode = stat.S_IFDIR | 0o700
        cache._check_cache_dir("/tmp/cache", 0o700, "test")
        mock_chmod.assert_not_called()

        # Case 3: Directory exists, permissions wrong, fix them
        mock_stat.side_effect = [
            os.stat_result((stat.S_IFDIR | 0o755, 0, 0, 0, 0, 0, 0, 0, 0, 0)), # Initial check
            os.stat_result((stat.S_IFDIR | 0o700, 0, 0, 0, 0, 0, 0, 0, 0, 0))  # Post-fix verify
        ]
        cache._check_cache_dir("/tmp/cache", 0o700, "test")
        mock_chmod.assert_called_with("/tmp/cache", 0o700)

    @patch("os.path.exists", return_value=True)
    @patch("os.lstat")
    def test_check_cache_dir_symlink_error(self, mock_lstat, mock_exists):
        mock_lstat.return_value.st_mode = stat.S_IFLNK
        with self.assertRaisesRegex(SnapmSystemError, "is a symlink"):
            cache._check_cache_dir("/path", 0o700, "test")

    @patch("os.path.exists", return_value=True)
    @patch("os.lstat")
    def test_check_cache_dir_not_dir_error(self, mock_lstat, mock_exists):
        mock_lstat.return_value.st_mode = stat.S_IFREG
        with self.assertRaisesRegex(SnapmSystemError, "is not a directory"):
            cache._check_cache_dir("/path", 0o700, "test")

    @patch("os.makedirs", side_effect=OSError("mkdir failed"))
    @patch("os.path.exists", return_value=False)
    def test_check_cache_dir_create_fail(self, mock_exists, mock_makedirs):
        with self.assertRaisesRegex(SnapmSystemError, "Failed to create"):
            cache._check_cache_dir("/path", 0o700, "test")

    @patch("os.chmod", side_effect=OSError("chmod failed"))
    @patch("os.path.exists", return_value=True)
    @patch("os.lstat")
    @patch("os.stat")
    def test_check_cache_dir_chmod_fail(self, mock_stat, mock_lstat, mock_exists, mock_chmod):
        mock_lstat.return_value.st_mode = stat.S_IFDIR
        mock_stat.return_value.st_mode = stat.S_IFDIR | 0o755 # Wrong perms
        with patch("os.makedirs"):
            with self.assertRaisesRegex(SnapmSystemError, "Failed to set permissions"):
                cache._check_cache_dir("/path", 0o700, "test")

    @patch("os.chmod")
    @patch("os.path.exists", return_value=True)
    @patch("os.lstat")
    @patch("os.stat")
    def test_check_cache_dir_verify_fail(self, mock_stat, mock_lstat, mock_exists, mock_chmod):
        mock_lstat.return_value.st_mode = stat.S_IFDIR
        # Stat returns wrong permissions even after chmod (simulated)
        mock_stat.return_value.st_mode = stat.S_IFDIR | 0o755
        with patch("os.makedirs"):
            with self.assertRaisesRegex(SnapmSystemError, "incorrect permissions"):
                cache._check_cache_dir("/path", 0o700, "test")

    def test_cache_name_identity_error(self):
        # mount_a == mount_b
        with self.assertRaisesRegex(SnapmInvalidIdentifierError, "mount_a == mount_b"):
            cache._cache_name(self.mount_a, self.mount_a, self.results)

    @unittest.skipIf(not _HAVE_ZSTD, "No zstandard module")
    @patch("snapm.fsdiff.cache._check_dirs")
    @patch("zstandard.ZstdCompressor")
    @patch("pickle.dump")
    def test_save_cache(self, mock_dump, mock_zstd, mock_check):
        results = self.results

        cache_rec = MagicMock(spec=FsDiffRecord)
        cache_rec.path = "/some/path"
        results._records = [cache_rec]
        results.count = 1
        results.__len__.return_value = 1
        cache.save_cache(self.mount_a, self.mount_b, self.results)
        mock_check.assert_called_once()
        mock_zstd.assert_called_once()

    @unittest.skipIf(not _HAVE_ZSTD, "No zstandard module")
    @patch("snapm.fsdiff.cache._check_dirs")
    @patch("os.listdir")
    @patch("zstandard.ZstdDecompressor")
    @patch("pickle.load")
    @patch('builtins.open', new_callable=mock_open)
    def test_load_cache_success(self, mock_file, mock_load, mock_zstd, mock_listdir, mock_check):
        # Setup matching filename
        uuid_a = cache._root_uuid(self.mount_a)
        uuid_b = self.mount_b.snapset.uuid
        # Ensure timestamp is fresh enough
        valid_ts = cache.floor(cache.datetime.now().timestamp())
        fname = f"{uuid_a}.{uuid_b}.123.{valid_ts}.cache.zstd"

        mock_listdir.return_value = [fname]

        # Setup returned results object with matching options
        cached_res = MagicMock(spec=FsDiffResults)
        cached_res.timestamp = 12345678
        cached_res.options = self.results.options
        cached_res.count = 1

        # Set up returned record object
        cached_rec = MagicMock(spec=FsDiffRecord)
        cached_rec.path = "/some/path"
        mock_load.side_effect = [cached_res, cached_rec, None]

        res = cache.load_cache(self.mount_a, self.mount_b, self.results.options)
        self.assertEqual(res.options, cached_res.options)
        self.assertEqual(res.timestamp, cached_res.timestamp)
        self.assertEqual(res[0].path, "/some/path")

    @patch("snapm.fsdiff.cache._check_dirs")
    def test_load_cache_identity_error(self, mock_check):
        with self.assertRaisesRegex(SnapmInvalidIdentifierError, "mount_a == mount_b"):
            cache.load_cache(self.mount_a, self.mount_a, DiffOptions())

    @patch("snapm.fsdiff.cache._check_dirs")
    @patch("os.listdir", return_value=["random_file.txt", "bad.uuid.1.2.cache", "a.b.c.not_int.cache"])
    def test_load_cache_ignores_malformed(self, mock_listdir, mock_check):
        # Should iterate over garbage without crashing and raise NotFound
        with self.assertRaises(SnapmNotFoundError):
            cache.load_cache(self.mount_a, self.mount_b, DiffOptions())

    @patch("snapm.fsdiff.cache._check_dirs")
    @patch("os.listdir")
    @patch("os.unlink")
    def test_load_cache_prunes_expired(self, mock_unlink, mock_listdir, mock_check):
        # Setup expired filename
        uuid_a = cache._root_uuid(self.mount_a)
        uuid_b = self.mount_b.snapset.uuid
        old_ts = 1000 # Very old
        fname = f"{uuid_a}.{uuid_b}.123.{old_ts}.cache"

        mock_listdir.return_value = [fname]

        with self.assertRaises(SnapmNotFoundError):
            cache.load_cache(self.mount_a, self.mount_b, DiffOptions(), expires=60)

        mock_unlink.assert_called()

    @patch("snapm.fsdiff.cache._check_dirs")
    @patch("os.listdir")
    @patch("lzma.open")
    @patch("pickle.load")
    def test_load_cache_options_mismatch(self, mock_load, mock_lzma, mock_listdir, mock_check):
        uuid_a = cache._root_uuid(self.mount_a)
        uuid_b = self.mount_b.snapset.uuid
        valid_ts = cache.floor(cache.datetime.now().timestamp())
        fname = f"{uuid_a}.{uuid_b}.123.{valid_ts}.cache"

        mock_listdir.return_value = [fname]

        # Returned object has DIFFERENT options
        cached_res = MagicMock()
        cached_res.options = DiffOptions(content_only=True) # different
        mock_load.return_value = cached_res

        with self.assertRaises(SnapmNotFoundError):
            cache.load_cache(self.mount_a, self.mount_b, DiffOptions(content_only=False))


class TestGetDictSize(unittest.TestCase):
    """Test automatic lzma dictionary sizing"""

    def setUp(self):
        # Expected return values based on the function's logic
        self.LIMIT_EXTRA_SMALL = 1000
        self.LIMIT_SMALL = 10000
        self.LIMIT_MEDIUM = 50000
        self.LIMIT_LARGE = 100000
        self.LIMIT_EXTRA_LARGE = 0

    def _create_meminfo_content(self, total_memory_kb):
        """Helper to create fake /proc/meminfo content."""
        return [
            f"MemTotal:       {total_memory_kb} kB\n",
            "MemFree:         10000 kB\n",
            "MemAvailable:    2733124 kB\n",
            "Buffers:           11848 kB\n",
            "Cached:           930844 kB\n",
        ]


    @patch('builtins.open', new_callable=mock_open)
    def test_very_small_memory_returns_1000(self, mock_file):
        """Test that systems with <= 2GiB memory get the small results max."""
        # Case: 1 GiB system
        mem_kb = 1 * 1024 * 1024 # 1 GiB in kB
        mock_file.return_value.readlines.return_value = (
            self._create_meminfo_content(mem_kb)
        )

        result = cache._get_max_cache_records()

        self.assertEqual(result, self.LIMIT_EXTRA_SMALL, "Should return 1000 for 1GiB RAM")

    @patch('builtins.open', new_callable=mock_open)
    def test_small_memory_returns_10000(self, mock_file):
        """Test that systems with <= 4GiB memory get the small results max."""
        # Case: 3 GiB system
        mem_kb = 3 * 1024 * 1024 # 2 GiB in kB
        mock_file.return_value.readlines.return_value = (
            self._create_meminfo_content(mem_kb)
        )

        result = cache._get_max_cache_records()

        self.assertEqual(result, self.LIMIT_SMALL, "Should return 10000 for 3GiB RAM")

    @patch('builtins.open', new_callable=mock_open)
    def test_medium_memory_returns_50000(self, mock_file):
        """Test that systems with > 4GiB and <= 8GiB memory get the medium results max."""
        # Case: 6 GiB system
        mem_kb = 6 * 1024 * 1024 # 6 GiB in kB
        mock_file.return_value.readlines.return_value = (
            self._create_meminfo_content(mem_kb)
        )

        result = cache._get_max_cache_records()

        self.assertEqual(result, self.LIMIT_MEDIUM, "Should return 50000 for 6GiB RAM")

    @patch('builtins.open', new_callable=mock_open)
    def test_large_memory_returns_100000(self, mock_file):
        """Test that systems with > 8GiB memory get the large results max."""
        # Case: 10 GiB system
        mem_kb = 10 * 1024 * 1024 # 10 GiB in kB
        mock_file.return_value.readlines.return_value = (
            self._create_meminfo_content(mem_kb)
        )

        result = cache._get_max_cache_records()

        self.assertEqual(result, self.LIMIT_LARGE, "Should return 100000 for 10GiB RAM")

    @patch('builtins.open', new_callable=mock_open)
    def test_extra_large_memory_returns_0(self, mock_file):
        """Test that systems with > 16GiB memory get the extra_large results max."""
        # Case: 32 GiB system
        mem_kb = 32 * 1024 * 1024 # 32 GiB in kB
        mock_file.return_value.readlines.return_value = (
            self._create_meminfo_content(mem_kb)
        )

        result = cache._get_max_cache_records()

        self.assertEqual(result, self.LIMIT_EXTRA_LARGE, "Should return 0 for 32GiB RAM")

    @patch('builtins.open', new_callable=mock_open)
    def test_boundary_conditions(self, mock_file):
        """Test the exact boundary of 4GiB (should be small/medium threshold logic)."""
        # The function logic is: if memtotal >= thresh: dict_size = dict_sizes[thresh]
        # dict_sizes keys are: 0, 4GiB, 8GiB.

        # Case: Exactly 4 GiB
        # 4GiB >= 4GiB is True, so it should upgrade to Medium size (512MiB)
        # *Note: Verify if this matches your intention. Based on your code:
        # medium = 4 * 2**30. If memtotal == 4GiB, it matches medium key.
        mem_kb = 4 * 1024 * 1024
        mock_file.return_value.readlines.return_value = (
            self._create_meminfo_content(mem_kb)
        )

        result = cache._get_max_cache_records()
        self.assertEqual(result, self.LIMIT_MEDIUM, "Exactly 4GiB should trigger Medium size based on loop logic")

    @patch('builtins.open', new_callable=mock_open)
    # We patch the logger to suppress output and prevent NameError if _log_debug is not imported
    @patch.dict(globals(), {'_log_debug': MagicMock()})
    def test_malformed_meminfo_defaults_to_small(self, mock_file):
        """Test that parsing errors result in the safe default (Small size)."""
        # Malformed line (string instead of int)
        mock_file.return_value.readlines.return_value = ["MemTotal:       NotANumber kB\n"]

        result = cache._get_max_cache_records()

        # Should default to memtotal = 0, which falls into the 'small' bucket
        self.assertEqual(result, self.LIMIT_EXTRA_SMALL, "Parsing error should fallback to small results max")
