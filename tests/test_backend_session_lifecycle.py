from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer


BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import main  # noqa: E402
from src.llm import add_to_history, get_conversation_history  # noqa: E402


class FakeTrack:
    def __init__(self):
        self.readyState = "live"

    def stop(self):
        self.readyState = "ended"


class FakePlayer:
    def __init__(self):
        self.audio = FakeTrack()
        self.video = FakeTrack()
        self.errors = []

    def send_error(self, message):
        self.errors.append(message)


class FakePeerConnection:
    def __init__(self):
        self.connectionState = "connected"

    async def close(self):
        self.connectionState = "closed"


class FakeReal:
    def __init__(self):
        self.flush_count = 0
        self.messages = []

    def flush_talk(self):
        self.flush_count += 1

    def put_msg_txt(self, message):
        self.messages.append(message)

    def is_speaking(self):
        return False


def make_session(sessionid=123):
    real = FakeReal()
    player = FakePlayer()
    pc = FakePeerConnection()
    state = main.SessionState(sessionid, real, player, pc)
    return state


class BackendApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.sessions.clear()
        main.llm_tasks.clear()
        app = main.build_app(
            serve_static=False,
            serve_data_static=False,
            max_sessions=2,
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        main.sessions.clear()
        main.llm_tasks.clear()

    async def test_health_endpoints_report_capacity(self):
        live = await self.client.get("/health/live")
        self.assertEqual(live.status, 200)
        self.assertEqual(await live.json(), {"status": "ok"})

        ready = await self.client.get("/health/ready")
        self.assertEqual(ready.status, 200)
        self.assertEqual(
            await ready.json(),
            {"status": "ready", "active_sessions": 0, "max_sessions": 2},
        )

    async def test_not_ready_health_returns_503(self):
        app = main.build_app(
            serve_static=False,
            serve_data_static=False,
            ready=False,
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.get("/health/ready")
            self.assertEqual(response.status, 503)
            self.assertEqual((await response.json())["status"], "not_ready")
        finally:
            await client.close()

    async def test_unknown_session_returns_404_without_internal_details(self):
        response = await self.client.post(
            "/human",
            json={"sessionid": 999, "type": "echo", "text": "hello"},
        )

        self.assertEqual(response.status, 404)
        payload = await response.json()
        self.assertEqual(payload["error"], "session_not_found")
        self.assertNotIn("KeyError", payload["msg"])

    async def test_invalid_json_object_returns_400(self):
        response = await self.client.post("/human", json=["not", "an", "object"])

        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["error"], "invalid_json")

    async def test_internal_error_does_not_leak_exception(self):
        with patch.object(main, "get_avatars_config", side_effect=RuntimeError("secret detail")):
            response = await self.client.get("/api/avatars")

        self.assertEqual(response.status, 500)
        payload = await response.json()
        self.assertEqual(payload["error"], "internal_error")
        self.assertEqual(payload["msg"], "Internal server error")
        self.assertNotIn("secret detail", payload["msg"])

    async def test_echo_uses_valid_session(self):
        state = make_session()
        main.sessions[state.sessionid] = state

        response = await self.client.post(
            "/human",
            json={"sessionid": state.sessionid, "type": "echo", "text": "hello"},
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(state.nerfreal.messages, ["hello"])

    async def test_interrupt_must_be_boolean(self):
        state = make_session()
        main.sessions[state.sessionid] = state

        response = await self.client.post(
            "/human",
            json={
                "sessionid": state.sessionid,
                "type": "echo",
                "text": "hello",
                "interrupt": "yes",
            },
        )

        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["error"], "invalid_interrupt")


class BackendCorsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.sessions.clear()
        main.llm_tasks.clear()

    async def asyncTearDown(self):
        main.sessions.clear()
        main.llm_tasks.clear()

    async def request_live(self, cors_origins, origin):
        app = main.build_app(
            serve_static=False,
            serve_data_static=False,
            cors_origins=cors_origins,
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            return await client.get("/health/live", headers={"Origin": origin})
        finally:
            await client.close()

    async def test_same_origin_default_does_not_emit_cors_header(self):
        response = await self.request_live([], "https://unexpected.example")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    async def test_origin_parser_normalizes_and_deduplicates(self):
        self.assertEqual(
            main.parse_cors_origins(
                "https://frontend.example/, http://localhost:8011,"
                "https://frontend.example"
            ),
            ["https://frontend.example", "http://localhost:8011"],
        )

    async def test_origin_parser_rejects_paths(self):
        with self.assertRaisesRegex(ValueError, "without paths"):
            main.parse_cors_origins("https://frontend.example/app")

    async def test_allowed_origin_is_echoed(self):
        response = await self.request_live(
            ["https://frontend.example"],
            "https://frontend.example",
        )
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"],
            "https://frontend.example",
        )
        self.assertNotIn("Access-Control-Allow-Credentials", response.headers)

    async def test_unlisted_origin_is_rejected(self):
        response = await self.request_live(
            ["https://frontend.example"],
            "https://unexpected.example",
        )
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)


class SessionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.sessions.clear()
        main.llm_tasks.clear()

    async def asyncTearDown(self):
        await main.on_shutdown({})
        main.sessions.clear()
        main.llm_tasks.clear()

    async def test_session_limit_is_enforced_before_allocation(self):
        main.sessions[1] = None

        with self.assertRaises(main.ApiError) as raised:
            main.reserve_session(1)

        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.error, "session_limit_reached")

    async def test_cleanup_releases_media_and_conversation_history(self):
        state = make_session()
        main.sessions[state.sessionid] = state
        main.pcs.add(state.pc)
        add_to_history(state.sessionid, "user", "hello")

        await main.cleanup_session(state.sessionid)

        self.assertNotIn(state.sessionid, main.sessions)
        self.assertNotIn(state.pc, main.pcs)
        self.assertEqual(state.pc.connectionState, "closed")
        self.assertEqual(state.player.audio.readyState, "ended")
        self.assertEqual(state.player.video.readyState, "ended")
        self.assertEqual(state.nerfreal.flush_count, 1)
        self.assertEqual(list(get_conversation_history(state.sessionid)), [])

    async def test_llm_failure_is_reported_to_active_session(self):
        state = make_session()
        main.sessions[state.sessionid] = state
        task = asyncio.get_running_loop().create_future()
        state.llm_tasks.add(task)
        main.llm_tasks.add(task)
        task.set_exception(RuntimeError("provider failed"))

        main.on_llm_task_done(state.sessionid, task)

        self.assertEqual(
            state.player.errors,
            ["LLM service is unavailable. Please try again."],
        )
        self.assertNotIn(task, state.llm_tasks)
        self.assertNotIn(task, main.llm_tasks)

    async def test_finished_orphan_task_clears_recreated_history(self):
        sessionid = 456
        add_to_history(sessionid, "user", "stale")
        task = asyncio.get_running_loop().create_future()
        main.llm_tasks.add(task)
        task.set_result(None)

        main.on_llm_task_done(sessionid, task)

        self.assertEqual(list(get_conversation_history(sessionid)), [])
        self.assertNotIn(task, main.llm_tasks)

    async def test_build_app_rejects_zero_capacity(self):
        with self.assertRaisesRegex(ValueError, "at least 1"):
            main.build_app(
                serve_static=False,
                serve_data_static=False,
                max_sessions=0,
            )


if __name__ == "__main__":
    unittest.main()
