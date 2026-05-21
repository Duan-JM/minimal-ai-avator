import asyncio
import sys
import types
import unittest

import numpy as np

if "resampy" not in sys.modules:
    resampy_module = types.ModuleType("resampy")
    resampy_module.resample = lambda x, sr_orig, sr_new: x
    sys.modules["resampy"] = resampy_module

if "loguru" not in sys.modules:
    loguru_module = types.ModuleType("loguru")

    class DummyLogger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    loguru_module.logger = DummyLogger()
    sys.modules["loguru"] = loguru_module

from src.ttsreal import DoubaoTTS, State, VllmOmniTTS


class DummyParent:
    def __init__(self):
        self.frames = []

    def put_audio_frame(self, frame, eventpoint):
        self.frames.append((frame.copy(), dict(eventpoint)))


async def async_chunk_generator(chunks):
    for chunk in chunks:
        yield chunk


def _make_vllm_tts(*, source_sample_rate: int = 16000) -> VllmOmniTTS:
    """Build a VllmOmniTTS instance without touching network/config helpers."""
    tts = VllmOmniTTS.__new__(VllmOmniTTS)
    tts.chunk = 320
    tts.sample_rate = 16000
    tts.parent = DummyParent()
    tts.state = State.RUNNING
    tts.source_sample_rate = source_sample_rate
    tts._resample_quantum = VllmOmniTTS._compute_resample_quantum(
        source_sample_rate, 16000
    )
    return tts


class TTSStreamOutputTests(unittest.TestCase):
    def test_doubao_stream_keeps_remaining_audio_in_final_frame(self):
        tts = DoubaoTTS.__new__(DoubaoTTS)
        tts.chunk = 320
        tts.parent = DummyParent()

        # 200 samples: fewer than one full 20ms frame, but still real audio data.
        samples = np.ones(200, dtype=np.int16) * 1200
        chunks = [samples.tobytes()]

        asyncio.run(tts.stream_tts(async_chunk_generator(chunks), ("hello", {})))

        self.assertEqual(len(tts.parent.frames), 1)
        frame, eventpoint = tts.parent.frames[0]
        self.assertEqual(eventpoint["status"], "end")
        self.assertGreater(np.max(np.abs(frame)), 0.0)


class VllmOmniStreamTests(unittest.TestCase):
    def test_emits_start_and_end_for_normal_stream(self):
        tts = _make_vllm_tts(source_sample_rate=16000)
        # 800 samples = 2.5 frames of 20ms@16kHz
        samples = np.ones(800, dtype=np.int16) * 1500
        chunks = [samples.tobytes()]

        tts.stream_tts(iter(chunks), ("hi", {"trace": "t1"}))

        statuses = [eventpoint.get("status", "") for _, eventpoint in tts.parent.frames]
        self.assertEqual(statuses[0], "start")
        self.assertEqual(statuses[-1], "end")
        # 2 full frames + 1 trailing (padded) end frame
        self.assertEqual(len(tts.parent.frames), 3)
        # Original event metadata is forwarded on start and end frames.
        self.assertEqual(tts.parent.frames[0][1].get("trace"), "t1")
        self.assertEqual(tts.parent.frames[-1][1].get("trace"), "t1")

    def test_short_audio_emits_only_end_with_real_data(self):
        tts = _make_vllm_tts(source_sample_rate=16000)
        # 200 samples: less than one 20ms frame.
        samples = np.ones(200, dtype=np.int16) * 1800
        chunks = [samples.tobytes()]

        tts.stream_tts(iter(chunks), ("short", {}))

        self.assertEqual(len(tts.parent.frames), 1)
        frame, eventpoint = tts.parent.frames[0]
        self.assertEqual(eventpoint["status"], "end")
        # The WebRTC layer backfills a missing start; ensure real audio is preserved.
        self.assertGreater(float(np.max(np.abs(frame))), 0.0)

    def test_odd_byte_split_does_not_corrupt_alignment(self):
        tts = _make_vllm_tts(source_sample_rate=16000)
        samples = np.ones(640, dtype=np.int16) * 1200
        raw = samples.tobytes()
        # Split the stream at an odd byte boundary to force buffering.
        chunks = [raw[:321], raw[321:]]

        tts.stream_tts(iter(chunks), ("aligned", {}))

        # 640 samples = exactly 2 frames; framing logic stores the trailing
        # remainder when streamlen falls below chunk, so we still get a final
        # padded 'end' frame.
        statuses = [eventpoint.get("status", "") for _, eventpoint in tts.parent.frames]
        self.assertEqual(statuses[0], "start")
        self.assertEqual(statuses[-1], "end")
        self.assertGreater(len(tts.parent.frames), 0)

    def test_state_pause_stops_emitting_frames(self):
        tts = _make_vllm_tts(source_sample_rate=16000)
        # Two big chunks; pause after the first so the second is ignored.
        samples = np.ones(640, dtype=np.int16) * 1500

        def gen():
            yield samples.tobytes()
            tts.state = State.PAUSE
            yield samples.tobytes()

        tts.stream_tts(gen(), ("pause", {}))

        statuses = [eventpoint.get("status", "") for _, eventpoint in tts.parent.frames]
        # No 'end' should be emitted when interrupted.
        self.assertNotIn("end", statuses)


if __name__ == "__main__":
    unittest.main()
