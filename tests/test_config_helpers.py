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

    def test_vllm_omni_helpers_return_defaults_when_unset(self):
        self._write(
            """
            TTS: {}
            """
        )
        self.assertEqual(config_module.get_vllm_omni_url(), "http://localhost:8091")
        self.assertEqual(config_module.get_vllm_omni_api_key(), "")
        self.assertEqual(config_module.get_vllm_omni_model(), "")
        self.assertEqual(config_module.get_vllm_omni_voice(), "vivian")
        self.assertEqual(config_module.get_vllm_omni_language(), "Auto")
        self.assertEqual(config_module.get_vllm_omni_task_type(), "")
        self.assertEqual(config_module.get_vllm_omni_instructions(), "")
        self.assertEqual(config_module.get_vllm_omni_sample_rate(), 24000)

    def test_vllm_omni_helpers_read_overrides(self):
        self._write(
            """
            TTS:
              VLLM_OMNI_URL: "http://example.com:9000/"
              VLLM_OMNI_API_KEY: "secret"
              VLLM_OMNI_MODEL: "fishaudio/s2-pro"
              VLLM_OMNI_VOICE: "ryan"
              VLLM_OMNI_LANGUAGE: "Chinese"
              VLLM_OMNI_TASK_TYPE: "CustomVoice"
              VLLM_OMNI_INSTRUCTIONS: "Speak warmly"
              VLLM_OMNI_SAMPLE_RATE: 44100
            """
        )
        self.assertEqual(
            config_module.get_vllm_omni_url(), "http://example.com:9000/"
        )
        self.assertEqual(config_module.get_vllm_omni_api_key(), "secret")
        self.assertEqual(config_module.get_vllm_omni_model(), "fishaudio/s2-pro")
        self.assertEqual(config_module.get_vllm_omni_voice(), "ryan")
        self.assertEqual(config_module.get_vllm_omni_language(), "Chinese")
        self.assertEqual(config_module.get_vllm_omni_task_type(), "CustomVoice")
        self.assertEqual(
            config_module.get_vllm_omni_instructions(), "Speak warmly"
        )
        self.assertEqual(config_module.get_vllm_omni_sample_rate(), 44100)

    def test_vllm_omni_sample_rate_falls_back_on_invalid_value(self):
        self._write(
            """
            TTS:
              VLLM_OMNI_SAMPLE_RATE: "not-a-number"
            """
        )
        self.assertEqual(config_module.get_vllm_omni_sample_rate(), 24000)


if __name__ == "__main__":
    unittest.main()
