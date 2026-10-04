import re
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import app
from scripts import build_site


class WebsiteBuildTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name)
        build_site.build(self.out)

    def test_every_page_and_file_is_built(self):
        for name in ("index.html", "privacy/index.html", "terms/index.html", "imprint/index.html", "404.html",
                     "robots.txt", "sitemap.xml", ".nojekyll", "static/css/base.css", "static/img/og.png"):
            self.assertTrue((self.out / name).is_file(), name)

    def test_links_stay_on_the_site_or_go_to_the_app(self):
        config = build_site.load_config()
        base = build_site.urlsplit(config["site_url"]).path
        index = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn(f'href="{config["app_url"]}/signup"', index)
        self.assertIn(f'href="{config["app_url"]}/login"', index)
        self.assertIn(f'content="{config["site_url"]}static/img/og.png"', index)
        for page in self.out.rglob("*.html"):
            html = page.read_text(encoding="utf-8")
            for leak in ("{{", "{%", "Traceback"):
                self.assertNotIn(leak, html, page)
            # Every local link must exist in the build (catches a page or asset the site forgot to include).
            for link in re.findall(r'(?:href|src)="' + re.escape(base) + r'([^"?#]*)', html):
                target = self.out / link
                self.assertTrue(target.is_file() or (target / "index.html").is_file(), f"{page.name} -> {link}")

    def test_404_copy_matches_the_app(self):
        self.assertEqual(build_site.NOT_FOUND, app.ERROR_COPY[404])


class LegalPagesInAppTests(unittest.TestCase):
    def test_legal_pages_are_served_and_linked_from_signup(self):
        client = TestClient(app.app)
        for path, heading in (("/privacy", "Privacy policy"), ("/terms", "Terms of service"), ("/imprint", "Imprint")):
            with self.subTest(path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(heading, response.text)
        signup = client.get("/signup").text
        self.assertIn('href="/terms"', signup)
        self.assertIn('href="/privacy"', signup)
        self.assertEqual(client.get("/login").status_code, 200)  # the legal routes don't swallow other pages


if __name__ == "__main__":
    unittest.main()
