import socket
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient

import app

USER = {"id": "00000000-0000-0000-0000-0000000000f1", "name": "jane.doe", "email": "jane.doe@example.com"}
COMPANY = "https://www.fernwick-labs.example/"
ALT_URL = "https://stories.fernwick-labs.example/clinics"
JD = "Scheduling engineer at Fernwick Labs."
SHORT_RESEARCH = {"company_url": COMPANY, "company_research": f"### Source: {COMPANY}\nTiny site.", "pages": [COMPANY]}
MAIN_ERROR = "We couldn't open that website. Check the address, or try the company's main page: " + COMPANY
ANCHORS = [{"title": f"Angle {i}", "company_evidence": "e", "job_connection": "j", "candidate_evidence": "c", "anchor": "a", "source_url": COMPANY} for i in range(3)]


class CompanyFallbackTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in {
            "app_user_for_request": USER,
            "count_recent_ai_actions": 0,
            "get_cover_letters_for_user": [{"filename": "l.txt", "content": "My letter."}],
            "save_job_application": {"id": "job-f1"},
            "get_job_application": {"id": "job-f1", "user_id": USER["id"], "job_description": JD, "company_url": COMPANY, "anchors": ANCHORS},
        }.items():
            self.stack.enter_context(patch.object(app, name, return_value=value))
        self.generate = self.stack.enter_context(patch.object(app, "generate_anchors", return_value={"anchors": ANCHORS}))
        self.research = self.stack.enter_context(patch.object(app, "research_company", return_value=dict(SHORT_RESEARCH)))

    def submit(self, company_extra=""):
        return self.client.post("/profile/job-input", data={"job_description": JD, "company_url": COMPANY, "company_extra": company_extra}, follow_redirects=False)

    def research_sent(self):
        return self.generate.call_args.kwargs["company_research"]

    def test_alternative_url_is_fetched_extracted_and_labeled(self):
        page = "<html><body><main><p>Fernwick Labs pilots fair rota tools with clinics in Lowmere.</p></main></body></html>"
        with patch.object(app, "fetch_company_html", return_value=page) as fetch:
            response = self.submit(f"  {ALT_URL}  ")
        self.assertEqual(response.status_code, 303)
        fetch.assert_called_once_with(ALT_URL)
        research = self.research_sent()
        self.assertIn(f"### Source: {ALT_URL}\nFernwick Labs pilots fair rota tools", research)
        self.assertTrue(research.startswith(f"### Source: {COMPANY}"))
        self.assertEqual(self.generate.call_args.kwargs["sources"], [COMPANY, ALT_URL])

    def test_alternative_url_to_private_address_is_refused_by_existing_checks(self):
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))]
        with patch("socket.getaddrinfo", return_value=private), patch("requests.get") as get:
            response = self.submit("https://intranet.fernwick-labs.example/")
        get.assert_not_called()
        self.assertEqual(response.status_code, 303)  # main research still worked; the fallback is simply skipped
        self.assertNotIn("intranet", self.research_sent())

    def test_pasted_text_is_stripped_of_html_and_labeled(self):
        response = self.submit("<p>Fernwick Labs is <b>employee-owned</b>.</p>\n<script>steal()</script>Founded in Lowmere.")
        self.assertEqual(response.status_code, 303)
        research = self.research_sent()
        self.assertIn("### Source: pasted by the user\n", research)
        self.assertIn("Fernwick Labs is employee-owned .", research)
        self.assertNotIn("<p>", research)
        self.assertNotIn("<script>", research)

    def test_long_paste_is_capped(self):
        self.submit("\n".join(["Fernwick Labs builds rota tools for clinics."] * 400))
        section = self.research_sent().split("### Source: pasted by the user\n", 1)[1]
        self.assertLessEqual(len(section), app.FALLBACK_TEXT_MAX_CHARS)

    def test_main_fetch_fails_but_fallback_succeeds(self):
        self.research.side_effect = ValueError(MAIN_ERROR)
        response = self.submit("Fernwick Labs is employee-owned and builds rota tools for clinics.")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.research_sent(), "### Source: pasted by the user\nFernwick Labs is employee-owned and builds rota tools for clinics.")

    def test_both_fail_shows_existing_error_and_keeps_fields(self):
        self.research.side_effect = ValueError(MAIN_ERROR)
        with patch.object(app, "fetch_company_html", side_effect=ValueError("nope")):
            response = self.submit(ALT_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn("We couldn&#39;t open that website", response.text)
        self.assertIn(JD, response.text)
        self.assertIn(f'value="{COMPANY}"', response.text)
        self.assertIn(f">{ALT_URL}</textarea>", response.text)
        self.generate.assert_not_called()

    def test_thin_research_sets_notice_shown_once(self):
        response = self.submit()
        self.assertEqual(response.status_code, 303)
        first = self.client.get("/company/angles")
        second = self.client.get("/company/angles")
        notice = "We found little text on this company's site, so these angles lean on the job description."
        self.assertIn(notice, first.text)
        self.assertNotIn(notice, second.text)

    def test_no_thin_notice_when_fallback_text_was_supplied(self):
        self.submit("Some extra company text.")
        self.assertNotIn("We found little text", self.client.get("/company/angles").text)

    def test_no_thin_notice_for_rich_research(self):
        self.research.return_value = dict(SHORT_RESEARCH, company_research="### Source: x\n" + "word " * 300)
        self.submit()
        self.assertNotIn("We found little text", self.client.get("/company/angles").text)


if __name__ == "__main__":
    unittest.main()
