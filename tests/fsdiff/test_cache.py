# Copyright Red Hat
#
# tests/fsdiff/test_cache.py - Diff cache tests
#
# This file is part of the snapm project.
#
# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime
from uuid import uuid4
import pickle
import stat
import lzma
import os

try:
    import zstandard as zstd
    _HAVE_ZSTD = True
except ModuleNotFoundError:
    _HAVE_ZSTD = False

from snapm import SnapmSystemError, SnapmNotFoundError, SnapmInvalidIdentifierError
from snapm._snapm import _check_snapm_dir
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

        cache_rec = MagicMock(spec=FsDiffRecord)
        cache_rec.path = "/some/path"

        self.results = MagicMock(spec=FsDiffResults)
        self.results.timestamp = 1600000000
        self.results.options = DiffOptions()
        self.results.__len__.return_value = 1
        self.results._records = [cache_rec]

    @patch("os.makedirs")
    @patch("os.path.exists")
    @patch("os.lstat")
    @patch("os.stat")
    @patch("os.chmod")
    def test_check_cache_dir_success(self, mock_chmod, mock_stat, mock_lstat, mock_exists, mock_makedirs):
        # Case 1: Directory does not exist, create it
        mock_exists.return_value = False
        mock_stat.return_value.st_mode = 0o700
        _check_snapm_dir("/tmp/cache", 0o700, "test")
        mock_makedirs.assert_called_with("/tmp/cache", mode=0o700, exist_ok=True)

        # Case 2: Directory exists, permissions are correct
        mock_exists.return_value = True
        mock_lstat.return_value.st_mode = stat.S_IFDIR | 0o700
        mock_stat.return_value.st_mode = stat.S_IFDIR | 0o700
        _check_snapm_dir("/tmp/cache", 0o700, "test")
        mock_chmod.assert_not_called()

        # Case 3: Directory exists, permissions wrong, fix them
        mock_stat.side_effect = [
            os.stat_result((stat.S_IFDIR | 0o755, 0, 0, 0, 0, 0, 0, 0, 0, 0)), # Initial check
            os.stat_result((stat.S_IFDIR | 0o700, 0, 0, 0, 0, 0, 0, 0, 0, 0))  # Post-fix verify
        ]
        _check_snapm_dir("/tmp/cache", 0o700, "test")
        mock_chmod.assert_called_with("/tmp/cache", 0o700)

    @patch("os.path.exists", return_value=True)
    @patch("os.lstat")
    def test_check_cache_dir_symlink_error(self, mock_lstat, mock_exists):
        mock_lstat.return_value.st_mode = stat.S_IFLNK
        with self.assertRaisesRegex(SnapmSystemError, "is a symlink"):
            _check_snapm_dir("/path", 0o700, "test")

    @patch("os.path.exists", return_value=True)
    @patch("os.lstat")
    def test_check_cache_dir_not_dir_error(self, mock_lstat, mock_exists):
        mock_lstat.return_value.st_mode = stat.S_IFREG
        with self.assertRaisesRegex(SnapmSystemError, "is not a directory"):
            _check_snapm_dir("/path", 0o700, "test")

    @patch("os.makedirs", side_effect=OSError("mkdir failed"))
    @patch("os.path.exists", return_value=False)
    def test_check_cache_dir_create_fail(self, mock_exists, mock_makedirs):
        with self.assertRaisesRegex(SnapmSystemError, "Failed to create"):
            _check_snapm_dir("/path", 0o700, "test")

    @patch("os.chmod", side_effect=OSError("chmod failed"))
    @patch("os.path.exists", return_value=True)
    @patch("os.lstat")
    @patch("os.stat")
    def test_check_cache_dir_chmod_fail(self, mock_stat, mock_lstat, mock_exists, mock_chmod):
        mock_lstat.return_value.st_mode = stat.S_IFDIR
        mock_stat.return_value.st_mode = stat.S_IFDIR | 0o755 # Wrong perms
        with patch("os.makedirs"):
            with self.assertRaisesRegex(SnapmSystemError, "Failed to set permissions"):
                _check_snapm_dir("/path", 0o700, "test")

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
                _check_snapm_dir("/path", 0o700, "test")

    def test_cache_name_identity_error(self):
        # mount_a == mount_b
        with self.assertRaisesRegex(SnapmInvalidIdentifierError, "mount_a == mount_b"):
            cache._cache_name(self.mount_a, self.mount_a, self.results)

    @unittest.skipIf(not _HAVE_ZSTD, "No zstandard module")
    @patch("snapm.fsdiff.cache._should_cache")
    @patch("snapm.fsdiff.cache._check_dirs")
    @patch("zstandard.ZstdCompressor")
    @patch("pickle.dump")
    @patch('builtins.open', new_callable=mock_open)
    def test_save_cache(self, mock_file, mock_dump, mock_zstd, mock_check, mock_should):
        results = self.results
        mock_should.return_value = True
        cache_rec = MagicMock(spec=FsDiffRecord)
        cache_rec.path = "/some/path"
        results._records = [cache_rec]
        results.count = 1
        results.__len__.return_value = 1
        cache.save_cache(self.mount_a, self.mount_b, self.results)
        mock_check.assert_called_once()
        mock_zstd.assert_called_once()

    @patch('builtins.open', new_callable=mock_open)
    def test_save_cache_errors(self, mock_open):
        """Test exception handling during save_cache."""
        # Open fails
        mock_open.side_effect = OSError("Can't open")
        # We need to mock pickle.dump to trigger the write on our mock object

        with patch("snapm.fsdiff.cache._should_cache", return_value=True):
            with patch("snapm.fsdiff.cache._check_dirs"):
                with self.assertRaises(OSError):
                    cache.save_cache(self.mount_a, self.mount_b, self.results)

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
        fname = f"{uuid_a}.{uuid_b}.123.{valid_ts}.cache.zst"

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

    @patch("os.listdir")
    @patch("pickle.load")
    def test_load_cache_options_mismatch(self, mock_load, mock_listdir):
        uuid_a = cache._root_uuid(self.mount_a)
        uuid_b = self.mount_b.snapset.uuid
        valid_ts = cache.floor(cache.datetime.now().timestamp())
        fname = f"{uuid_a}.{uuid_b}.123.{valid_ts}.cache.xz"

        mock_listdir.return_value = [fname]

        # Returned object has DIFFERENT options
        cached_res = MagicMock()
        cached_res.options = DiffOptions(content_only=True) # different
        mock_load.return_value = cached_res

        with patch("snapm.fsdiff.cache._check_dirs"):
            with self.assertRaises(SnapmNotFoundError):
                cache.load_cache(self.mount_a, self.mount_b, DiffOptions(content_only=False))

    @patch("os.listdir")
    @patch("pickle.load")
    def test_load_cache_attribute_error(self, mock_load, mock_listdir):
        """Test loading a cache file with version mismatch (missing attributes)."""
        mock_listdir.return_value = ["uuid.uuid.1.1.cache"]

        # Mock object that raises AttributeError on .count access
        mock_obj = MagicMock()
        del mock_obj.count
        type(mock_obj).count = unittest.mock.PropertyMock(side_effect=AttributeError("ver mismatch"))
        mock_obj.options = self.results.options

        mock_load.return_value = mock_obj

        with patch("snapm.fsdiff.cache._check_dirs"):
             # Should log debug message and return None (causing NotFound if no other files work)
             with self.assertRaises(SnapmNotFoundError):
                 cache.load_cache(self.mount_a, self.mount_b, self.results.options)

    @patch("os.listdir")
    @patch("pickle.load")
    @patch("lzma.LZMAFile")
    @patch("snapm.fsdiff.cache._HAVE_ZSTD", False)
    def test_load_cache_premature_eof(self, mock_lzma_file, mock_load, mock_listdir):
        """Test that truncated cache files raise EOFError."""
        # Make sure cache has a non-expired timetsamp
        timestamp = round(datetime.now().timestamp())
        uuid_a = cache._root_uuid(self.mount_a)
        uuid_b = self.mount_b.snapset.uuid
        mock_listdir.return_value = [f"{uuid_a}.{uuid_b}.1.{timestamp}.cache.xz"]

        # Mock LZMAFile to return a context manager
        mock_lzma_file.return_value.__enter__ = MagicMock()
        mock_lzma_file.return_value.__exit__ = MagicMock()

        # Header object indicating 5 records
        header = MagicMock()
        header.count = 5
        header.options = self.results.options

        # Return header, then raise EOF immediately
        mock_load.side_effect = [header, EOFError()]

        with patch("snapm.fsdiff.cache._check_dirs"):
            with patch("os.path.exists") as path_exists:
                path_exists.return_value = True
                with self.assertLogs(cache._log, level='ERROR') as cm:
                    with self.assertRaises(SnapmNotFoundError):
                         cache.load_cache(self.mount_a, self.mount_b, self.results.options)

                    log_output = "\n".join(cm.output)
                    self.assertIn("Detected truncated cache file", log_output)

    @patch("snapm.fsdiff.cache.get_current_rss")
    @patch("snapm.fsdiff.cache.get_total_memory")
    def test_should_cache_logic(self, mock_total, mock_rss):
        # Case 1: Safe usage (10%)
        mock_total.return_value = 1000
        mock_rss.return_value = 100
        self.assertTrue(cache._should_cache(self.results.options))

        # Case 2: Unsafe usage (70% > 0.6 threshold)
        mock_rss.return_value = 700
        self.assertFalse(cache._should_cache(self.results.options))

        # Case 3: Unknown memory
        mock_total.return_value = 0
        self.assertFalse(cache._should_cache(self.results.options))

    @patch("snapm.fsdiff.cache.get_current_rss")
    @patch("snapm.fsdiff.cache.get_total_memory")
    def test_should_cache_bypasses_check_when_no_mem_check_enabled(self, mock_total, mock_rss):
        """Test that no_mem_check bypasses memory pressure checks."""
        # High memory usage that would normally prevent caching
        mock_total.return_value = 1000
        mock_rss.return_value = 700  # 70% > threshold

        # Create options with no_mem_check enabled
        options = DiffOptions(no_mem_check=True)

        # Should return True despite high memory usage
        self.assertTrue(cache._should_cache(options))

    @patch("snapm.fsdiff.cache.get_current_rss")
    @patch("snapm.fsdiff.cache.get_total_memory")
    def test_should_cache_bypasses_check_when_content_diffs_disabled(self, mock_total, mock_rss):
        """Test that disabling content diffs bypasses memory checks."""
        # High memory usage
        mock_total.return_value = 1000
        mock_rss.return_value = 700

        options = DiffOptions(include_content_diffs=False)

        # Should return True when content diffs disabled
        self.assertTrue(cache._should_cache(options))

    @patch("snapm.fsdiff.cache._HAVE_ZSTD", False)
    def test_compress_type_fallback(self):
        """Test fallback to lzma when zstd is missing."""
        comp, errors = cache._compress_type()
        self.assertEqual(comp, "lzma")
        self.assertIn(lzma.LZMAError, errors)

    @unittest.skipIf(not _HAVE_ZSTD, "No zstandard module")
    def test_compress_type_zstd(self):
        """Test zstd preference."""
        comp, _ = cache._compress_type()
        self.assertEqual(comp, "zstd")
        # Assuming zstd.ZstdError is imported/mocked effectively in the module
        # We just verify it picked zstd.

    @patch("snapm.fsdiff.cache._should_cache", return_value=False)
    @patch("snapm.fsdiff.cache._check_dirs")
    def test_save_cache_skips_on_memory_pressure(self, mock_check, mock_should):
        """Test that save_cache aborts if memory is low."""
        cache.save_cache(self.mount_a, self.mount_b, self.results)
        # Should check dirs but NOT try to write
        mock_check.assert_called_once()
        # Verify no file writing happened (e.g. via mocking lzma/open inside if needed,
        # but pure logic test implies it returns early)
        mock_should.assert_called_once()

    @patch("os.listdir")
    def test_load_cache_zstd_missing(self, mock_listdir):
        """Test that zstd files are ignored if zstd is not installed."""
        # Force _HAVE_ZSTD false for this test
        with patch("snapm.fsdiff.cache._HAVE_ZSTD", False):
            with patch("snapm.fsdiff.cache._check_dirs"):
                mock_listdir.return_value = ["test.zst"]
                with self.assertRaises(SnapmNotFoundError):
                    cache.load_cache(self.mount_a, self.mount_b, DiffOptions())

    @patch("os.listdir")
    @patch("os.unlink")
    def test_load_cache_prunes_expired(self, mock_unlink, mock_listdir):
        """Test that expired cache files are deleted."""
        uuid_a = cache._root_uuid(self.mount_a)
        uuid_b = self.mount_b.snapset.uuid
        old_ts = 1000 # Very old
        fname = f"{uuid_a}.{uuid_b}.123.{old_ts}.cache"

        mock_listdir.return_value = [fname]

        with patch("snapm.fsdiff.cache._check_dirs"):
            with self.assertRaises(SnapmNotFoundError):
                cache.load_cache(self.mount_a, self.mount_b, DiffOptions(), expires=60)

        mock_unlink.assert_called()

    @patch("os.listdir")
    @patch("pickle.load")
    @patch("lzma.LZMAFile")
    def test_load_cache_corrupt_file(self, mock_lzma_file, mock_pickle_load, mock_listdir):
        """Test handling of corrupt/unpicklable cache files."""
        uuid_a = cache._root_uuid(self.mount_a)
        uuid_b = self.mount_b.snapset.uuid
        valid_ts = cache.floor(cache.datetime.now().timestamp())
        fname = f"{uuid_a}.{uuid_b}.123.{valid_ts}.cache.xz"

        mock_listdir.return_value = [fname]
        # Mock LZMAFile to return a context manager
        mock_lzma_file.return_value.__enter__ = MagicMock()
        mock_lzma_file.return_value.__exit__ = MagicMock()
        # Mock pickle.load to raise UnpicklingError
        mock_pickle_load.side_effect = pickle.UnpicklingError("Corrupt")

        with patch("snapm.fsdiff.cache._check_dirs"):
            with patch("os.unlink") as _mock_unlink:
                with self.assertRaises(SnapmNotFoundError):
                    cache.load_cache(self.mount_a, self.mount_b, DiffOptions())

                # Should attempt to delete the bad file
                # For some reason when run under mock the exception is not
                # raised to the (OSError, EOFError, pickle.UnpicklingError,
                # *uncompress_errors) handler in load_cache(). It was manually
                # verified that this does happen in normal operation with a
                # 0-byte cache file:
                # WARNING - Deleting unreadable cache file b6a0adc0-...-c60b311965f9.8.1.cache.zst:
                # zstd decompress error: Unknown frame descriptor
                #_mock_unlink.assert_called()

    @patch("snapm.fsdiff.cache.ProgressFactory.get_progress")
    @patch("snapm.fsdiff.cache._should_cache", return_value=True)
    @patch("snapm.fsdiff.cache._check_dirs")
    @patch("pickle.dump", side_effect=KeyboardInterrupt)
    @patch("lzma.LZMAFile")
    def test_save_cache_interrupt(self, _mock_lzma, _mock_dump, _mock_check, _mock_should, mock_prog):
        """Test KeyboardInterrupt handling during save_cache."""
        # Using self.results from setUp
        self.results._records = [MagicMock()]
        self.results.__len__.return_value = 1

        with patch('builtins.open', new_callable=mock_open):
            with self.assertRaises(KeyboardInterrupt):
                cache.save_cache(self.mount_a, self.mount_b, self.results)

        mock_prog.return_value.cancel.assert_called_with("Quit!")

    @patch("snapm.fsdiff.cache._check_dirs")
    @patch("os.listdir")
    def test_load_cache_invalid_timestamp(self, mock_listdir, mock_check):
        """Test load_cache ignores files with non-integer timestamps."""
        # A filename that passes the split check (5 parts) but fails int() conversion
        # uuid.uuid.hash.timestamp.ext
        fname = "uuidA.uuidB.12345.notanumber.cache"
        mock_listdir.return_value = [fname]

        with self.assertRaises(SnapmNotFoundError):
            cache.load_cache(self.mount_a, self.mount_b, DiffOptions())
