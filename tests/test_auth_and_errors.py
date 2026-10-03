import asyncio
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

import app

PROTECTED_ROUTES = [
    ("GET", "/profile/setup"),
    ("GET", "/profile/ready"),
    ("GET", "/profile/job-input"),
    ("GET", "/company/angles"),
    ("POST", "/profile/resume"),
    ("POST", "/profile/resume/delete"),
    ("POST", "/profile/cover-letters"),
    ("POST", "/profile/cover-letters/delete"),
    ("POST", "/profile/build"),
    ("POST", "/profile/job-input"),
    ("POST", "/company/angles"),
    ("POST", "/cover-letter/revise"),
    ("POST", "/cover-letter/accept"),
]
FORM = {"resume_id": "x", "cover_letter_id": "x", "job_description": "jd", "company_url": "https://example.com",
        "selected_anchor": "0", "job_application_id": "j", "feedback": "f"}
FILES = {"resume": ("a.txt", b"x"), "files": ("a.txt", b"x")}


class AuthGuardTests(unittest.TestCase):
    def test_every_app_route_redirects_to_login_without_a_session(self):
        client = TestClient(app.app, follow_redirects=False)
        for method, path in PROTECTED_ROUTES:
            with self.subTest(f"{method} {path}"):
                is_upload = path in ("/profile/resume", "/profile/cover-letters")
                response = client.request(method, path, data=FORM if method == "POST" else None, files=FILES if is_upload else None)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["location"], "/login")

    def test_session_secret_is_never_the_old_public_default(self):
        self.assertNotEqual(app.SESSION_SECRET, "cover-letter-ai-dev-secret")
        self.assertGreaterEqual(len(app.SESSION_SECRET), 32)


class ErrorPageTests(unittest.TestCase):
    def test_unknown_page_renders_custom_404(self):
        response = TestClient(app.app).get("/no-such-page")
        self.assertEqual(response.status_code, 404)
        self.assertIn("This page isn&#39;t in the draft.", response.text)
        self.assertIn('href="/"', response.text)

    def test_unexpected_error_renders_custom_500(self):
        client = TestClient(app.app, raise_server_exceptions=False)
        with patch.object(app, "app_user_for_request", side_effect=RuntimeError("boom")):
            response = client.get("/profile/ready")
        self.assertEqual(response.status_code, 500)
        self.assertIn("Something smudged the ink.", response.text)
        self.assertNotIn("boom", response.text)

    def test_uncaught_auth_error_redirects_to_login(self):
        # Covers routes that don't catch HTTPException themselves (e.g. the angles fallback path).
        request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
        response = asyncio.run(app.http_error_page(request, app.HTTPException(status_code=401)))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

if __name__ == "__main__":
    unittest.main()
