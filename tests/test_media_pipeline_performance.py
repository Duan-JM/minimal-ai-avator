from __future__ import annotations

import asyncio
import base64
import importlib
import queue
import sys
import threading
import time
import types
import unittest

import numpy as np

if "src.wav2lip.models" not in sys.modules:
    wav2lip_models = types.ModuleType("src.wav2lip.models")

    class _StubWav2Lip:
        pass

    wav2lip_models.Wav2Lip = _StubWav2Lip
    sys.modules["src.wav2lip.models"] = wav2lip_models

_basereal = sys.modules.get("src.basereal")
if _basereal is None or not hasattr(_basereal, "_try_enqueue_track_item"):
    sys.modules.pop("src.basereal", None)
    _basereal = importlib.import_module("src.basereal")

_enqueue_track_item_with_backpressure = _basereal._enqueue_track_item_with_backpressure
_get_pipeline_queue_item = _basereal._get_pipeline_queue_item
_put_pipeline_queue_item = _basereal._put_pipeline_queue_item
_try_enqueue_track_item = _basereal._try_enqueue_track_item

import main as backend_main
from src import gpu_wav2lip_service, lipreal_remote


class _FakeResponse:
    def __init__(self, *, headers=None, content=b"", payload=None, status_code=200):
        self.headers = headers or {}
        self.content = content
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


class TrackQueueBackpressureTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=1)
        self.loop.close()

    def _put(self, target_queue, item):
        asyncio.run_coroutine_threadsafe(target_queue.put(item), self.loop).result(timeout=1)

    def _get(self, target_queue):
        return asyncio.run_coroutine_threadsafe(target_queue.get(), self.loop).result(timeout=1)

    def test_full_queue_does_not_leave_a_pending_put(self):
        target_queue = asyncio.Queue(maxsize=1)
        self._put(target_queue, "existing")

        self.assertFalse(
            _try_enqueue_track_item(self.loop, target_queue, "late", timeout=0.02)
        )
        self.assertEqual(self._get(target_queue), "existing")
        time.sleep(0.05)
        self.assertTrue(target_queue.empty())

    def test_timed_out_loop_callback_cannot_enqueue_later(self):
        target_queue = asyncio.Queue(maxsize=1)
        started = threading.Event()
        release = threading.Event()

        def block_loop():
            started.set()
            release.wait(timeout=0.2)

        self.loop.call_soon_threadsafe(block_loop)
        self.assertTrue(started.wait(timeout=1))
        self.assertFalse(
            _try_enqueue_track_item(self.loop, target_queue, "late", timeout=0.01)
        )
        release.set()
        time.sleep(0.05)
        self.assertTrue(target_queue.empty())

    def test_backpressure_retries_until_capacity_is_available(self):
        target_queue = asyncio.Queue(maxsize=1)
        self._put(target_queue, "existing")

        async def delayed_consume():
            await asyncio.sleep(0.03)
            return await target_queue.get()

        consumed = asyncio.run_coroutine_threadsafe(delayed_consume(), self.loop)
        queued, retries = _enqueue_track_item_with_backpressure(
            threading.Event(),
            self.loop,
            target_queue,
            "next",
            timeout=0.02,
            retry_interval=0.005,
        )

        self.assertTrue(queued)
        self.assertGreater(retries, 0)
        self.assertEqual(consumed.result(timeout=1), "existing")
        self.assertEqual(self._get(target_queue), "next")

    def test_pipeline_queue_operations_stop_without_deadlock(self):
        target_queue = queue.Queue(maxsize=1)
        target_queue.put("existing")
        quit_event = threading.Event()
        timer = threading.Timer(0.03, quit_event.set)
        timer.start()
        try:
            self.assertFalse(
                _put_pipeline_queue_item(
                    quit_event,
                    target_queue,
                    "blocked",
                    timeout=0.01,
                )
            )
        finally:
            timer.cancel()

        self.assertEqual(target_queue.get_nowait(), "existing")
        received, item = _get_pipeline_queue_item(
            quit_event,
            target_queue,
            timeout=0.01,
        )
        self.assertFalse(received)
        self.assertIsNone(item)


class RemoteInferenceTransportTests(unittest.TestCase):
    def setUp(self):
        self.frames = np.arange(2 * 4 * 4 * 3, dtype=np.uint8).reshape(2, 4, 4, 3)
        self.metrics = {
            'prep_time': 0.01,
            'transfer_time': 0.02,
            'infer_time': 0.03,
            'post_time': 0.04,
            'total_time': 0.10,
            'fps': 20.0,
        }

    def test_binary_response_round_trip(self):
        with gpu_wav2lip_service.app.test_request_context(
            headers={'Accept': gpu_wav2lip_service.BINARY_INFERENCE_MEDIA_TYPE}
        ):
            response = gpu_wav2lip_service._build_inference_response(
                self.frames,
                self.metrics,
            )

        decoded, fps = lipreal_remote._decode_inference_response(
            _FakeResponse(
                headers=dict(response.headers),
                content=response.get_data(),
            )
        )

        self.assertEqual(response.mimetype, lipreal_remote.BINARY_INFERENCE_MEDIA_TYPE)
        self.assertEqual(fps, 20.0)
        self.assertEqual(decoded.dtype, np.float32)
        np.testing.assert_array_equal(decoded, self.frames)

    def test_legacy_json_response_remains_supported(self):
        with gpu_wav2lip_service.app.test_request_context(
            headers={'Accept': 'application/json'}
        ):
            response = gpu_wav2lip_service._build_inference_response(
                self.frames,
                self.metrics,
            )
            payload = response.get_json()

        decoded, fps = lipreal_remote._decode_inference_response(
            _FakeResponse(
                headers={'Content-Type': 'application/json'},
                payload=payload,
            )
        )

        self.assertEqual(fps, 20.0)
        self.assertEqual(
            len(base64.b64decode(payload['batch_data'])),
            self.frames.nbytes,
        )
        np.testing.assert_array_equal(decoded, self.frames)

    def test_remote_client_requests_binary_response(self):
        captured = {}

        def fake_post(url, **kwargs):
            captured['url'] = url
            captured.update(kwargs)
            return _FakeResponse(
                headers={
                    'Content-Type': lipreal_remote.BINARY_INFERENCE_MEDIA_TYPE,
                    'X-Batch-Shape': '2,4,4,3',
                    'X-Batch-Dtype': 'uint8',
                    'X-Inference-FPS': '20.0',
                },
                content=self.frames.tobytes(),
            )

        client = lipreal_remote.RemoteGPUClient("http://gpu.example", 123, [])
        client.session_initialized = True
        original_post = lipreal_remote.requests.post
        lipreal_remote.requests.post = fake_post
        try:
            result = client.inference_batch(
                np.zeros((2, 80, 16), dtype=np.float32),
                [0, 1],
            )
        finally:
            lipreal_remote.requests.post = original_post

        self.assertEqual(
            captured['headers']['Accept'],
            lipreal_remote.BINARY_INFERENCE_MEDIA_TYPE,
        )
        np.testing.assert_array_equal(result, self.frames)

    def test_remote_session_init_failure_returns_fallback_signal(self):
        client = lipreal_remote.RemoteGPUClient("http://gpu.example", 123, [])
        client.init_session = lambda: False

        result = client.inference_batch(
            np.zeros((2, 80, 16), dtype=np.float32),
            [0, 1],
        )

        self.assertIsNone(result)

    def test_default_realtime_batch_is_latency_oriented(self):
        self.assertEqual(backend_main.DEFAULT_INFERENCE_BATCH_SIZE, 16)


if __name__ == "__main__":
    unittest.main()
