import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app


class LandingPageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)

    def test_root_renders_landing_with_signup_cta(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("in your own", response.text)
        self.assertIn('href="/signup"', response.text)
        self.assertIn('href="/login"', response.text)

    def test_signup_and_login_still_render_forms(self):
        for path in ("/signup", "/login"):
            with self.subTest(path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'action="{path}"', response.text)
                self.assertIn('name="email"', response.text)
                self.assertIn('name="password"', response.text)

    def test_every_static_asset_referenced_by_templates_exists(self):
        import re
        for template in (app.BASE_DIR / "templates").rglob("*.html"):
            for asset in re.findall(r'"/static/([^"?]+)', template.read_text(encoding="utf-8")):
                with self.subTest(template=template.name, asset=asset):
                    self.assertTrue((app.BASE_DIR / "static" / asset).is_file())

    def test_health_check_answers_get_and_head(self):
        with patch.dict("os.environ", {"RENDER_GIT_COMMIT": "abc1234def"}):
            self.assertEqual(self.client.get("/healthz").json(), {"ok": True, "commit": "abc1234"})
        self.assertEqual(self.client.head("/healthz").status_code, 200)

    def test_public_pages_answer_head_for_link_checkers_and_fetchers(self):
        for path in ("/", "/privacy", "/terms", "/imprint", "/robots.txt"):
            with self.subTest(path):
                self.assertEqual(self.client.head(path).status_code, 200)

    def test_robots_txt_allows_crawlers(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertIn("User-agent: *\nAllow: /", response.text)


if __name__ == "__main__":
    unittest.main()
