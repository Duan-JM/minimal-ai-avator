import json
import sys
import types
import unittest


if "av" not in sys.modules:
    av_module = types.ModuleType("av")
    av_frame = types.ModuleType("av.frame")
    av_packet = types.ModuleType("av.packet")

    class Frame:
        pass

    class Packet:
        pass

    av_frame.Frame = Frame
    av_packet.Packet = Packet
    sys.modules["av"] = av_module
    sys.modules["av.frame"] = av_frame
    sys.modules["av.packet"] = av_packet

if "aiortc" not in sys.modules:
    aiortc_module = types.ModuleType("aiortc")

    class MediaStreamTrack:
        def __init__(self):
            self.readyState = "live"

        def stop(self):
            self.readyState = "ended"

    aiortc_module.MediaStreamTrack = MediaStreamTrack
    sys.modules["aiortc"] = aiortc_module

if "loguru" not in sys.modules:
    loguru_module = types.ModuleType("loguru")

    class DummyLogger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    loguru_module.logger = DummyLogger()
    sys.modules["loguru"] = loguru_module

from src.webrtc import HumanPlayer


class DummyContainer:
    def notify(self, eventpoint):
        self.last_event = eventpoint


class DummyDataChannel:
    def __init__(self):
        self.readyState = "open"
        self.sent = []

    def send(self, message):
        self.sent.append(json.loads(message))


class WebRTCTTSEventTests(unittest.TestCase):
    def test_end_event_backfills_missing_start(self):
        player = HumanPlayer(DummyContainer())
        channel = DummyDataChannel()
        player.set_data_channel(channel)

        player.notify({"status": "end", "text": "你好"})

        self.assertEqual(
            [msg["type"] for msg in channel.sent],
            ["llm", "tts_start", "tts_end"],
        )

    def test_normal_start_end_only_emits_one_start(self):
        player = HumanPlayer(DummyContainer())
        channel = DummyDataChannel()
        player.set_data_channel(channel)

        player.notify({"status": "start", "text": "现在几点啊"})
        player.notify({"status": "end", "text": "现在几点啊"})

        self.assertEqual(
            [msg["type"] for msg in channel.sent],
            ["llm", "tts_start", "tts_end"],
        )

    def test_error_event_uses_data_channel(self):
        player = HumanPlayer(DummyContainer())
        channel = DummyDataChannel()
        player.set_data_channel(channel)

        player.send_error("LLM unavailable")

        self.assertEqual(
            channel.sent,
            [{"type": "error", "message": "LLM unavailable"}],
        )


if __name__ == "__main__":
    unittest.main()
