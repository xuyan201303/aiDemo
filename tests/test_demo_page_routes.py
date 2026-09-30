"""The page URLs must work on a fresh HTTP request, without a hash route."""
from __future__ import annotations

import tempfile
import unittest

from fastapi.testclient import TestClient

from sanzo_demo.app import create_app


class PageRouteTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory(prefix="sanzo_page_routes_")
        self.addCleanup(self.storage.cleanup)
        # Page routes do not start the application lifespan or access PostgreSQL.
        self.client = TestClient(create_app("postgresql://unused@localhost/unused", self.storage.name))

    def test_root_redirects_to_case_list(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/cases")

    def test_case_list_is_directly_accessible(self):
        response = self.client.get("/cases")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="case-list-view"', response.text)
        self.assertIn('id="sidebar-cases"', response.text)

    def test_case_detail_is_directly_accessible_and_refreshable(self):
        for _ in range(2):
            response = self.client.get("/cases/synthetic-existing-case")
            self.assertEqual(response.status_code, 200)
            self.assertIn('id="case-detail-view"', response.text)

    def test_missing_case_path_returns_safe_page_shell(self):
        response = self.client.get("/cases/not-existing-id")
        self.assertEqual(response.status_code, 200)
        self.assertIn('src="/static/app.js"', response.text)


if __name__ == "__main__":
    unittest.main()
