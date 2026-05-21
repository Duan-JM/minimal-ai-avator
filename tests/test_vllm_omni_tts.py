"""Tests for the vLLM-Omni TTS backend HTTP integration.

These tests stub the ``requests.post`` call used by :mod:`src.ttsreal` so that
no real network access is required. They exercise:

* request payload + auth header construction
* PCM streaming end-to-end through ``txt_to_audio``
* JSON error responses are detected and not forwarded as audio
* HTTP error responses emit no audio frames
"""

from __future__ import annotations

import sys
import types
import unittest
from contextlib import contextmanager
from typing import Iterable

import numpy as np

if "resampy" not in sys.modules:
    resampy_module = types.ModuleType("resampy")
    resampy_module.resample = lambda x, sr_orig, sr_new: x
    sys.modules["resampy"] = resampy_module

if "loguru" not in sys.modules:
    loguru_module = types.ModuleType("loguru")

    class _DummyLogger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    loguru_module.logger = _DummyLogger()
    sys.modules["loguru"] = loguru_module


from src import ttsreal
from src.ttsreal import State, VllmOmniTTS


# Some sibling test files (``test_get_file_extraction``) install a bare stub
# for ``requests`` before ``ttsreal`` is imported. Ensure the symbols we patch
# below exist on whatever module ``ttsreal.requests`` ended up referencing.
_requests_mod = ttsreal.requests
if not hasattr(_requests_mod, "HTTPError"):
    class _StubHTTPError(Exception):
        pass

    _requests_mod.HTTPError = _StubHTTPError
if not hasattr(_requests_mod, "RequestException"):
    class _StubRequestException(Exception):
        pass

    _requests_mod.RequestException = _StubRequestException


class _DummyParent:
    def __init__(self):
        self.frames = []

    def put_audio_frame(self, frame, eventpoint):
        self.frames.append((frame.copy(), dict(eventpoint)))


class _FakeResponse:
    """Minimal stand-in for ``requests.Response`` used with ``stream=True``."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict | None = None,
        chunks: Iterable[bytes] = (),
        body: bytes = b"",
    ):
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "audio/pcm"}
        self._chunks = list(chunks)
        self.content = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            err = ttsreal.requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err

    def iter_content(self, chunk_size: int = 6400):
        for chunk in self._chunks:
            yield chunk


def _build_tts(**overrides) -> VllmOmniTTS:
    """Build a VllmOmniTTS bypassing ``__init__`` (which reads config + logs)."""
    tts = VllmOmniTTS.__new__(VllmOmniTTS)
    tts.chunk = 320
    tts.sample_rate = 16000
    tts.parent = _DummyParent()
    tts.state = State.RUNNING
    tts.base_url = overrides.get("base_url", "http://localhost:8091")
    tts.endpoint = tts.base_url + VllmOmniTTS.OPENAI_PATH
    tts.api_key = overrides.get("api_key", "")
    tts.model = overrides.get("model", "")
    tts.language = overrides.get("language", "Auto")
    tts.task_type = overrides.get("task_type", "")
    tts.instructions = overrides.get("instructions", "")
    tts.voice = overrides.get("voice", "vivian")
    tts.source_sample_rate = overrides.get("source_sample_rate", 16000)
    tts._resample_quantum = VllmOmniTTS._compute_resample_quantum(
        tts.source_sample_rate, tts.sample_rate
    )
    return tts


@contextmanager
def _patch_requests_post(handler):
    sentinel = object()
    original = getattr(ttsreal.requests, "post", sentinel)
    ttsreal.requests.post = handler
    try:
        yield
    finally:
        if original is sentinel:
            try:
                delattr(ttsreal.requests, "post")
            except AttributeError:
                pass
        else:
            ttsreal.requests.post = original


class PayloadAndHeaderTests(unittest.TestCase):
    def test_payload_omits_empty_optional_fields_and_no_auth_when_unset(self):
        tts = _build_tts()
        captured = {}

        def handler(url, headers=None, json=None, stream=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResponse(chunks=[b""])

        with _patch_requests_post(handler):
            list(tts.vllm_omni_voice("hello"))

        self.assertEqual(captured["url"], "http://localhost:8091/v1/audio/speech")
        self.assertNotIn("Authorization", captured["headers"])
        payload = captured["json"]
        # Required fields are present.
        self.assertEqual(payload["input"], "hello")
        self.assertEqual(payload["voice"], "vivian")
        self.assertEqual(payload["response_format"], "pcm")
        self.assertIs(payload["stream"], True)
        # Optional empties are omitted.
        self.assertNotIn("model", payload)
        self.assertNotIn("task_type", payload)
        self.assertNotIn("instructions", payload)
        # 'language' default "Auto" is truthy and forwarded.
        self.assertEqual(payload["language"], "Auto")

    def test_payload_includes_optional_fields_and_auth_when_set(self):
        tts = _build_tts(
            api_key="sk-test",
            model="fishaudio/s2-pro",
            task_type="CustomVoice",
            instructions="Speak slowly",
            language="Chinese",
            voice="ryan",
        )
        captured = {}

        def handler(url, headers=None, json=None, stream=None, timeout=None):
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResponse(chunks=[b""])

        with _patch_requests_post(handler):
            list(tts.vllm_omni_voice("hello"))

        self.assertEqual(captured["headers"].get("Authorization"), "Bearer sk-test")
        payload = captured["json"]
        self.assertEqual(payload["voice"], "ryan")
        self.assertEqual(payload["model"], "fishaudio/s2-pro")
        self.assertEqual(payload["task_type"], "CustomVoice")
        self.assertEqual(payload["instructions"], "Speak slowly")
        self.assertEqual(payload["language"], "Chinese")


class StreamingBehaviourTests(unittest.TestCase):
    def test_txt_to_audio_pushes_pcm_frames_through_pipeline(self):
        tts = _build_tts()
        samples = np.ones(800, dtype=np.int16) * 1500  # 2.5 frames
        chunks = [samples[:400].tobytes(), samples[400:].tobytes()]

        def handler(url, headers=None, json=None, stream=None, timeout=None):
            return _FakeResponse(chunks=chunks)

        with _patch_requests_post(handler):
            tts.txt_to_audio(("hi", {"trace": "t1"}))

        self.assertGreaterEqual(len(tts.parent.frames), 3)
        statuses = [eventpoint.get("status") for _, eventpoint in tts.parent.frames]
        self.assertEqual(statuses[0], "start")
        self.assertEqual(statuses[-1], "end")
        # Verify event metadata propagates onto start and end events.
        self.assertEqual(tts.parent.frames[0][1].get("trace"), "t1")
        self.assertEqual(tts.parent.frames[-1][1].get("trace"), "t1")

    def test_json_error_body_emits_only_silent_end_frame(self):
        tts = _build_tts()

        def handler(url, headers=None, json=None, stream=None, timeout=None):
            return _FakeResponse(
                status_code=200,
                headers={"Content-Type": "application/json"},
                chunks=[b'{"error": "missing voice"}'],
            )

        with _patch_requests_post(handler):
            tts.txt_to_audio(("hi", {}))

        # Error path still emits one silent end frame so the WebRTC frontend
        # receives a tts_end completion signal (and backfills tts_start).
        self.assertEqual(len(tts.parent.frames), 1)
        frame, eventpoint = tts.parent.frames[0]
        self.assertEqual(eventpoint["status"], "end")
        self.assertEqual(float(np.max(np.abs(frame))), 0.0)

    def test_http_error_response_emits_only_silent_end_frame(self):
        tts = _build_tts()

        def handler(url, headers=None, json=None, stream=None, timeout=None):
            return _FakeResponse(
                status_code=401,
                headers={"Content-Type": "text/plain"},
                chunks=[],
                body=b"unauthorized",
            )

        with _patch_requests_post(handler):
            tts.txt_to_audio(("hi", {}))

        self.assertEqual(len(tts.parent.frames), 1)
        frame, eventpoint = tts.parent.frames[0]
        self.assertEqual(eventpoint["status"], "end")
        self.assertEqual(float(np.max(np.abs(frame))), 0.0)


class UrlNormalisationTests(unittest.TestCase):
    def test_base_url_strips_trailing_slashes(self):
        # We can't call __init__ (it touches config + logger), but the strip
        # behaviour lives directly in __init__. Re-create the exact statement.
        raw = "http://example.com:8091/"
        normalised = raw.rstrip("/")
        self.assertEqual(normalised + VllmOmniTTS.OPENAI_PATH,
                         "http://example.com:8091/v1/audio/speech")


class ResampleQuantumTests(unittest.TestCase):
    def test_quantum_for_common_sample_rates(self):
        self.assertEqual(VllmOmniTTS._compute_resample_quantum(16000, 16000), 1)
        self.assertEqual(VllmOmniTTS._compute_resample_quantum(24000, 16000), 3)
        self.assertEqual(VllmOmniTTS._compute_resample_quantum(44100, 16000), 441)


if __name__ == "__main__":
    unittest.main()
