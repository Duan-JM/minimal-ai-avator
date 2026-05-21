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

HTML_FILES = ["index.html", "talk.html", "test.html"]
JS_CLIENT_FILES = ["client.js", "client_talk.js"]


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
        # talk.html and test.html include client(_talk).js after the helpers.
        # index.html has only an inline script that uses the helpers.
        for name in ("talk.html", "test.html"):
            with self.subTest(html=name):
                text = (STATIC / name).read_text(encoding="utf-8")
                api_idx = text.find('src="api.js"')
                client_idx = re.search(r'src="client(?:_talk)?\.js"', text)
                self.assertIsNotNone(client_idx, f"{name} missing client script")
                self.assertLess(
                    api_idx,
                    client_idx.start(),
                    f"{name} should load api.js before client script",
                )


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

    def test_backend_paths_are_routed_through_apiurl(self):
        # For each backend path used in the codebase, ensure the call site uses
        # window.apiUrl(...). This guards against future regressions.
        for path, text in self._files_to_scan():
            for endpoint in self.BACKEND_PATHS:
                if endpoint not in text:
                    continue
                with self.subTest(file=path.name, endpoint=endpoint):
                    pattern = re.compile(
                        r"window\.apiUrl\(\s*['\"]" + re.escape(endpoint) + r"['\"]"
                    )
                    self.assertRegex(
                        text,
                        pattern,
                        f"{path.name} references {endpoint} without window.apiUrl",
                    )

    def test_avatar_image_paths_go_through_mediaurl(self):
        # Avatar images come from the backend (/data/...) and must be rebased.
        index_html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn("window.mediaUrl(avatar.image)", index_html)

        client_talk = (STATIC / "client_talk.js").read_text(encoding="utf-8")
        self.assertIn("window.mediaUrl(avatarConfig.image)", client_talk)


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


if __name__ == "__main__":
    unittest.main()
