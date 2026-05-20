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

from src.ttsreal import DoubaoTTS


class DummyParent:
    def __init__(self):
        self.frames = []

    def put_audio_frame(self, frame, eventpoint):
        self.frames.append((frame.copy(), dict(eventpoint)))


async def async_chunk_generator(chunks):
    for chunk in chunks:
        yield chunk


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


if __name__ == "__main__":
    unittest.main()
