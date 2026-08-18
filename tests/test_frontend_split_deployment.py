"""Tests for the frontend/backend split-deployment helpers.

These tests assert the static frontend files have the runtime config + API
helper modules wired up correctly, so the frontend can be deployed to a
different origin than the backend. We don't have a Node toolchain in this
repo, so we use the same file-substring strategy as ``test_talk_frontend_audio.py``.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "frontend" / "static"

HTML_FILES = ["index.html", "talk.html"]
JS_CLIENT_FILES = ["client_talk.js"]


class FrontendConfigModuleTests(unittest.TestCase):
    def test_config_js_defines_app_config_with_required_keys(self):
        text = (STATIC / "config.js").read_text(encoding="utf-8")
        self.assertIn("window.APP_CONFIG", text)
        self.assertIn("apiBaseUrl", text)
        self.assertIn("mediaBaseUrl", text)
        self.assertIn("iceServers", text)

    def test_config_js_default_is_same_origin(self):
        # Empty apiBaseUrl preserves the integrated (single-port) deployment.
        text = (STATIC / "config.js").read_text(encoding="utf-8")
        self.assertRegex(text, r"apiBaseUrl\s*:\s*['\"]\s*['\"]")
        self.assertRegex(text, r"mediaBaseUrl\s*:\s*['\"]\s*['\"]")

    def test_api_js_exposes_helpers_on_window(self):
        text = (STATIC / "api.js").read_text(encoding="utf-8")
        self.assertIn("window.apiUrl", text)
        self.assertIn("window.mediaUrl", text)
        self.assertIn("window.apiFetch", text)
        self.assertIn("window.apiJson", text)
        self.assertIn("window.ApiError", text)
        self.assertIn("AbortController", text)
        self.assertIn("response.ok", text)

    def test_api_js_passes_through_absolute_urls(self):
        text = (STATIC / "api.js").read_text(encoding="utf-8")
        # Sanity-check there is an absolute-URL detection so cross-origin
        # links in backend responses don't get accidentally double-prefixed.
        self.assertIn("isAbsoluteUrl", text)

    def test_api_js_returns_empty_for_null_media(self):
        text = (STATIC / "api.js").read_text(encoding="utf-8")
        # mediaUrl(null|undefined|'') -> '' to avoid <img src="undefined">.
        self.assertRegex(text, r"path\s*===\s*null")


class FrontendHtmlIncludesHelpersTests(unittest.TestCase):
    def test_each_html_loads_config_before_api(self):
        for name in HTML_FILES:
            with self.subTest(html=name):
                text = (STATIC / name).read_text(encoding="utf-8")
                cfg_idx = text.find('src="config.js"')
                api_idx = text.find('src="api.js"')
                self.assertGreater(cfg_idx, -1, f"{name} missing config.js include")
                self.assertGreater(api_idx, -1, f"{name} missing api.js include")
                self.assertLess(
                    cfg_idx,
                    api_idx,
                    f"{name} should load config.js before api.js",
                )

    def test_html_loads_helpers_before_client_scripts(self):
        text = (STATIC / "talk.html").read_text(encoding="utf-8")
        api_idx = text.find('src="api.js"')
        client_idx = text.find('src="client_talk.js"')
        self.assertGreater(client_idx, -1, "talk.html missing client script")
        self.assertLess(api_idx, client_idx)


class FrontendUsesHelpersForBackendCallsTests(unittest.TestCase):
    """Reject any leftover hardcoded same-origin backend calls."""

    BACKEND_PATHS = [
        "/offer",
        "/human",
        "/humanaudio",
        "/record",
        "/api/avatars",
        "/interrupt_talk",
        "/is_speaking",
        "/set_audiotype",
    ]

    def _files_to_scan(self):
        files = [STATIC / name for name in HTML_FILES + JS_CLIENT_FILES]
        return [(p, p.read_text(encoding="utf-8")) for p in files]

    def test_no_raw_fetch_to_backend_paths(self):
        # Every fetch() call to a backend path must be wrapped in apiUrl(...).
        bad_pattern = re.compile(r"""fetch\(\s*['\"]/[^'\"]+['\"]""")
        for path, text in self._files_to_scan():
            with self.subTest(file=path.name):
                matches = bad_pattern.findall(text)
                self.assertFalse(
                    matches,
                    f"{path.name} has raw same-origin fetch calls: {matches}",
                )

    def test_backend_paths_are_routed_through_api_helpers(self):
        # Backend paths must use apiUrl(...) or the higher-level apiJson(...).
        for path, text in self._files_to_scan():
            for endpoint in self.BACKEND_PATHS:
                if endpoint not in text:
                    continue
                with self.subTest(file=path.name, endpoint=endpoint):
                    pattern = re.compile(
                        r"window\.(?:apiUrl|apiJson)\(\s*['\"]"
                        + re.escape(endpoint)
                        + r"['\"]"
                    )
                    self.assertRegex(
                        text,
                        pattern,
                        f"{path.name} references {endpoint} without an API helper",
                    )

    def test_avatar_image_paths_go_through_mediaurl(self):
        # Avatar images come from the backend (/data/...) and must be rebased.
        index_html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn("window.mediaUrl(avatar.image)", index_html)

        client_talk = (STATIC / "client_talk.js").read_text(encoding="utf-8")
        self.assertIn("window.mediaUrl(avatarConfig.image)", client_talk)

    def test_frontend_uses_structured_json_requests(self):
        index_html = (STATIC / "index.html").read_text(encoding="utf-8")
        client_talk = (STATIC / "client_talk.js").read_text(encoding="utf-8")
        self.assertIn("window.apiJson('/api/avatars')", index_html)
        self.assertIn("window.apiJson('/offer'", client_talk)
        self.assertIn("window.apiJson('/human'", client_talk)

    def test_no_external_avatar_placeholder_request(self):
        index_html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("picsum.photos", index_html)
        self.assertIn("favicon.svg", index_html)


class FrontendIceServersAreConfigurableTests(unittest.TestCase):
    """Allow operators to supply STUN/TURN servers via config.js for NAT/HTTPS."""

    def test_client_reads_iceservers_from_app_config(self):
        for name in JS_CLIENT_FILES:
            with self.subTest(js=name):
                text = (STATIC / name).read_text(encoding="utf-8")
                self.assertRegex(
                    text,
                    r"window\.APP_CONFIG\s*&&\s*window\.APP_CONFIG\.iceServers",
                    f"{name} should read iceServers from window.APP_CONFIG",
                )


class FrontendLegacySurfaceTests(unittest.TestCase):
    def test_legacy_test_client_is_removed(self):
        self.assertFalse((STATIC / "test.html").exists())
        self.assertFalse((STATIC / "client.js").exists())


if __name__ == "__main__":
    unittest.main()
