"""Tests for backend ``build_app`` factory and the split-deployment toggles.

The integrated mode (serving frontend static + /data) must keep working. In
API-only mode (``--no-static``) the factory should skip the corresponding
``add_static`` registrations so the backend can be deployed without the
frontend repo on disk.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import main  # noqa: E402  (must follow sys.path manipulation)


API_ROUTES = {
    "/health/live",
    "/health/ready",
    "/offer",
    "/human",
    "/humanaudio",
    "/set_audiotype",
    "/record",
    "/interrupt_talk",
    "/is_speaking",
    "/api/avatars",
}


def _collect_route_summaries(app):
    """Return a list of (path-or-prefix, kind) tuples for each registered route."""
    summaries = []
    for resource in app.router.resources():
        info = resource.get_info()
        if "path" in info:
            for route in resource:
                summaries.append((info["path"], route.method, "plain"))
        elif "prefix" in info:
            summaries.append((info["prefix"], "GET", "static"))
        elif "formatter" in info:
            summaries.append((info["formatter"], "GET", "dynamic"))
    return summaries


class BuildAppApiOnlyTests(unittest.TestCase):
    """With both static toggles off, only the JSON API routes should remain."""

    def test_no_static_routes_when_disabled(self):
        app = main.build_app(serve_static=False, serve_data_static=False)
        summaries = _collect_route_summaries(app)
        static_prefixes = [path for path, _method, kind in summaries if kind == "static"]
        self.assertFalse(
            static_prefixes,
            f"expected no static routes, got: {static_prefixes}",
        )

    def test_all_api_routes_registered(self):
        app = main.build_app(serve_static=False, serve_data_static=False)
        plain_paths = {
            path for path, _method, kind in _collect_route_summaries(app) if kind == "plain"
        }
        for endpoint in API_ROUTES:
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, plain_paths)


class BuildAppIntegratedTests(unittest.TestCase):
    """Default mode serves both the frontend and the /data folder."""

    def setUp(self):
        # add_static requires the directory to exist; provide tmp stand-ins
        # so we don't need the real ``data/`` and ``frontend/static/`` trees.
        self.tmp_static = Path(self.id() + "-static").resolve()
        self.tmp_data = Path(self.id() + "-data").resolve()
        self.tmp_static.mkdir(exist_ok=True)
        self.tmp_data.mkdir(exist_ok=True)
        self._orig_static = main.STATIC_DIR
        self._orig_data = main.DATA_DIR
        main.STATIC_DIR = self.tmp_static
        main.DATA_DIR = self.tmp_data

    def tearDown(self):
        main.STATIC_DIR = self._orig_static
        main.DATA_DIR = self._orig_data
        for path in (self.tmp_static, self.tmp_data):
            if path.exists():
                path.rmdir()

    def test_static_routes_registered_by_default(self):
        app = main.build_app()
        prefixes = {
            path for path, _method, kind in _collect_route_summaries(app) if kind == "static"
        }
        # aiohttp normalizes a root prefix of '/' to '' in resource.get_info().
        self.assertTrue(
            {"/", ""}.intersection(prefixes),
            f"expected root-mounted static, got: {prefixes}",
        )
        self.assertIn("/data", prefixes)

    def test_only_data_static_when_frontend_disabled(self):
        app = main.build_app(serve_static=False, serve_data_static=True)
        prefixes = {
            path for path, _method, kind in _collect_route_summaries(app) if kind == "static"
        }
        self.assertEqual(prefixes, {"/data"})
        # And the root prefix specifically must not appear.
        self.assertNotIn("", prefixes)
        self.assertNotIn("/", prefixes)


class BuildAppCorsTests(unittest.TestCase):
    """The split-deployment scenario relies on permissive CORS without credentials."""

    def test_cors_does_not_allow_credentials(self):
        # Read the source to assert the policy. Inspecting aiohttp_cors at
        # runtime would require touching its private attributes.
        src = (BACKEND / "main.py").read_text(encoding="utf-8")
        self.assertIn("allow_credentials=False", src)
        self.assertNotIn("allow_credentials=True", src)


if __name__ == "__main__":
    unittest.main()
