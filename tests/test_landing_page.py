import unittest

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
        self.assertEqual(self.client.get("/healthz").json(), {"ok": True})
        self.assertEqual(self.client.head("/healthz").status_code, 200)


if __name__ == "__main__":
    unittest.main()
