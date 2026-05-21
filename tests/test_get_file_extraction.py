"""Tests for safe archive extraction in :mod:`src.get_file`.

These exercise the path-traversal guard added on top of ``zipfile.extractall``
and ``tarfile.extractall`` (Bandit B202, CVE-2007-4559).
"""

from __future__ import annotations

import io
import os
import sys
import tarfile
import tempfile
import types
import unittest
import zipfile


# Stub ``six`` so that importing ``src.get_file`` does not require the (otherwise
# heavy) runtime stack on minimal CI environments. ``src.get_file`` only uses
# ``six.string_types``.
if "six" not in sys.modules:
    six_module = types.ModuleType("six")
    six_module.string_types = (str,)
    sys.modules["six"] = six_module

# Stub ``tqdm`` likewise. ``src.get_file`` only consults ``tqdm.tqdm`` and we do
# not exercise the download path in these tests.
if "tqdm" not in sys.modules:
    tqdm_module = types.ModuleType("tqdm")

    class _DummyTqdm:
        def __init__(self, *args, **kwargs):
            pass

        def update(self, _n):
            pass

        def close(self):
            pass

    tqdm_module.tqdm = _DummyTqdm
    sys.modules["tqdm"] = tqdm_module

# ``requests`` is referenced at import time but no test triggers a download.
if "requests" not in sys.modules:
    requests_module = types.ModuleType("requests")
    sys.modules["requests"] = requests_module

from src import get_file


class SafeArchiveExtractionTests(unittest.TestCase):
    def test_safe_zip_extracts_normally(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "safe.zip")
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("a/b.txt", "hello")

            dest = os.path.join(tmp, "out")
            os.makedirs(dest, exist_ok=True)
            extracted = get_file._extract_archive(archive, dest, "zip")
            self.assertTrue(extracted)
            self.assertTrue(os.path.isfile(os.path.join(dest, "a", "b.txt")))

    def test_safe_tar_extracts_normally(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "safe.tar")
            payload = b"safe payload"
            with tarfile.open(archive, "w") as tf:
                data = io.BytesIO(payload)
                info = tarfile.TarInfo(name="dir/file.txt")
                info.size = len(payload)
                tf.addfile(info, data)

            dest = os.path.join(tmp, "out")
            os.makedirs(dest, exist_ok=True)
            extracted = get_file._extract_archive(archive, dest, "tar")
            self.assertTrue(extracted)
            self.assertTrue(os.path.isfile(os.path.join(dest, "dir", "file.txt")))

    def test_zip_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "evil.zip")
            with zipfile.ZipFile(archive, "w") as zf:
                # ``../escape.txt`` would normally land in the parent directory.
                zf.writestr("../escape.txt", "pwn")

            dest = os.path.join(tmp, "out")
            os.makedirs(dest, exist_ok=True)
            with self.assertRaises(ValueError):
                get_file._extract_archive(archive, dest, "zip")
            self.assertFalse(os.path.exists(os.path.join(tmp, "escape.txt")))

    def test_tar_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "evil.tar")
            payload = b"pwn"
            with tarfile.open(archive, "w") as tf:
                data = io.BytesIO(payload)
                info = tarfile.TarInfo(name="../escape.txt")
                info.size = len(payload)
                tf.addfile(info, data)

            dest = os.path.join(tmp, "out")
            os.makedirs(dest, exist_ok=True)
            with self.assertRaises(ValueError):
                get_file._extract_archive(archive, dest, "tar")
            self.assertFalse(os.path.exists(os.path.join(tmp, "escape.txt")))

    def test_tar_unsafe_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "symlink.tar")
            with tarfile.open(archive, "w") as tf:
                info = tarfile.TarInfo(name="link")
                info.type = tarfile.SYMTYPE
                info.linkname = "../outside"
                tf.addfile(info)

            dest = os.path.join(tmp, "out")
            os.makedirs(dest, exist_ok=True)
            with self.assertRaises(ValueError):
                get_file._extract_archive(archive, dest, "tar")

    def test_unknown_archive_format_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "data.bin")
            with open(archive, "wb") as fh:
                fh.write(b"not an archive")
            dest = os.path.join(tmp, "out")
            os.makedirs(dest, exist_ok=True)
            self.assertFalse(get_file._extract_archive(archive, dest, "auto"))


if __name__ == "__main__":
    unittest.main()
