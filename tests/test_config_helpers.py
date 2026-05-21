"""Tests for the small helpers in :mod:`src.config`.

These cover only the YAML-loading and lookup logic that does not depend on the
heavy ML runtime stack.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


from src import config as config_module


class ConfigHelperTests(unittest.TestCase):
    def setUp(self):
        # Reset the lazy singleton so each test loads a fresh config file.
        self._orig_config = config_module._config
        config_module._config = None
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg_path = Path(self._tmp.name) / "config.yml"

    def tearDown(self):
        config_module._config = self._orig_config
        self._tmp.cleanup()

    def _write(self, body: str) -> None:
        self._cfg_path.write_text(textwrap.dedent(body), encoding="utf-8")
        # Force load_config() to read our file by replacing the global default.
        config_module._config = config_module.load_config(str(self._cfg_path))

    def test_load_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            config_module.load_config(str(self._cfg_path / "does-not-exist"))

    def test_avatar_lookup_by_id_and_dir(self):
        self._write(
            """
            AVATARS:
              default_avatar: "alpha"
              avatars:
                - id: "alpha"
                  name: "Alpha"
                  avatar_dir: "wav2lip_avatar_alpha"
                  tts_config:
                    type: "doubao"
                    voice: "v1"
                - id: "beta"
                  name: "Beta"
                  avatar_dir: "wav2lip_avatar_beta"
                  tts_config:
                    type: "doubao"
                    voice: "v2"
            """
        )
        # Lookup by id.
        found = config_module.get_avatar_config("alpha")
        self.assertIsNotNone(found)
        self.assertEqual(found["name"], "Alpha")

        # Lookup by avatar_dir (backward compatibility).
        found_by_dir = config_module.get_avatar_config("wav2lip_avatar_beta")
        self.assertIsNotNone(found_by_dir)
        self.assertEqual(found_by_dir["name"], "Beta")

        # Missing avatars return None.
        self.assertIsNone(config_module.get_avatar_config("ghost"))

    def test_model_download_config_composes_full_url(self):
        self._write(
            """
            DOWNLOAD:
              BASE_URL: "https://example.com/models"
              MODELS:
                wav2lip.pth:
                  path: "./models/wav2lip.pth"
                  size: "215 MB"
                  description: "wav2lip"
            """
        )
        models = config_module.get_model_download_config()
        self.assertIn("wav2lip.pth", models)
        self.assertEqual(
            models["wav2lip.pth"]["url"],
            "https://example.com/models/wav2lip.pth",
        )

    def test_doubao_helpers_return_empty_when_unset(self):
        self._write(
            """
            TTS: {}
            """
        )
        self.assertEqual(config_module.get_doubao_appid(), "")
        self.assertEqual(config_module.get_doubao_token(), "")
        # Default voice is hard-coded when missing.
        self.assertNotEqual(config_module.get_doubao_voice(), "")


if __name__ == "__main__":
    unittest.main()
