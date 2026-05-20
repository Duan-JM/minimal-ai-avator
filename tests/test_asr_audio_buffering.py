import threading
import time
import types
import unittest
import sys

import numpy as np


if "src.basereal" not in sys.modules:
    basereal_module = types.ModuleType("src.basereal")

    class BaseReal:
        pass

    basereal_module.BaseReal = BaseReal
    sys.modules["src.basereal"] = basereal_module

from src.baseasr import BaseASR


class DummyOpt:
    fps = 50
    batch_size = 1
    l = 0
    r = 0


class DummyParent:
    curr_state = 0


class ASRAudioBufferingTests(unittest.TestCase):
    def test_waits_long_enough_for_nearby_tts_frame(self):
        asr = BaseASR(DummyOpt(), DummyParent())
        expected = np.ones(asr.chunk, dtype=np.float32) * 0.25

        def delayed_put():
            time.sleep(0.03)
            asr.put_audio_frame(expected, {"status": "start", "text": "hello"})

        threading.Thread(target=delayed_put, daemon=True).start()
        frame, frame_type, eventpoint = asr.get_audio_frame()

        self.assertEqual(frame_type, 0)
        self.assertEqual(eventpoint["status"], "start")
        self.assertTrue(np.allclose(frame, expected))


if __name__ == "__main__":
    unittest.main()
