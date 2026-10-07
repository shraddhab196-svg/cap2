"""Reader-service fallback for company research (Jina Reader, then Firecrawl). Never touches the network."""
import os
import unittest
from unittest.mock import MagicMock, patch

import requests

import src.company_researcher as cr

URL = "https://blocked.example/"
LONG = "Blocked Example builds fair scheduling software for clinics. " * 20  # well over READER_MIN_CHARS


def http(text="", payload=None, status=200):
    response = MagicMock(text=text, status_code=status)
    response.json.return_value = payload or {}
    response.raise_for_status.side_effect = None if status == 200 else requests.HTTPError(str(status))
    return response


class ReaderFallbackTests(unittest.TestCase):
    def setUp(self):
        patches = [
            patch.dict(os.environ, {"COMPANY_RESEARCH_READERS": "1", "JINA_API_KEY": "", "FIRECRAWL_API_KEY": ""}),
            patch.object(cr, "check_public_url"),
            patch.object(cr, "fetch_company_html", side_effect=ValueError("We couldn't open that website.")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.get = self.enterContext(patch.object(cr.requests, "get"))
        self.post = self.enterContext(patch.object(cr.requests, "post"))

    def test_blocked_site_is_read_through_jina_without_a_key(self):
        self.get.return_value = http(LONG)
        result = cr.research_company(URL)
        self.assertIn("fair scheduling software", result["company_research"])
        self.assertEqual(result["pages"], [URL])
        self.assertEqual(self.get.call_args.args[0], f"https://r.jina.ai/{URL}")
        self.assertNotIn("Authorization", self.get.call_args.kwargs["headers"])
        self.post.assert_not_called()  # no Firecrawl key, and Jina was enough

    def test_jina_key_is_sent_when_set(self):
        self.get.return_value = http(LONG)
        with patch.dict(os.environ, {"JINA_API_KEY": "jk"}):
            cr.research_company(URL)
        self.assertEqual(self.get.call_args.kwargs["headers"]["Authorization"], "Bearer jk")

    def test_firecrawl_takes_over_when_jina_fails(self):
        self.get.return_value = http(status=451)
        self.post.return_value = http(payload={"data": {"markdown": "![logo](x.png)\n[About us](/about)\n" + LONG}})
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "fk"}), self.assertLogs(cr.logger, "WARNING"):
            result = cr.research_company(URL)
        text = result["company_research"]
        self.assertIn("About us", text)
        self.assertNotIn("x.png", text)
        self.assertNotIn("(/about)", text)
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer fk")

    def test_thin_own_page_is_replaced_by_a_longer_reader_page(self):
        with patch.object(cr, "fetch_company_html", return_value="<p>Loading…</p>"):
            self.get.return_value = http(LONG)
            result = cr.research_company(URL)
        self.assertIn("fair scheduling software", result["company_research"])

    def test_all_failing_raises_the_original_error(self):
        self.get.return_value = http(status=500)
        with self.assertLogs(cr.logger, "WARNING"), self.assertRaisesRegex(ValueError, "couldn't open that website"):
            cr.research_company(URL)

    def test_switched_off_means_no_reader_calls(self):
        with patch.dict(os.environ, {"COMPANY_RESEARCH_READERS": "0"}), self.assertRaises(ValueError):
            cr.research_company(URL)
        self.get.assert_not_called()

    def test_private_addresses_never_go_to_a_reader(self):
        with patch.object(cr, "check_public_url", side_effect=ValueError("not a public website")), self.assertRaises(ValueError):
            cr.research_company("http://127.0.0.1/")
        self.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
