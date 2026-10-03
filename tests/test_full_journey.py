"""The whole user journey through real routes and templates, with only Supabase and Groq faked.

Catches wiring bugs unit tests miss: a template reading a key the backend never sets, a redirect to the wrong
page, a form posting a field the route doesn't read.
"""
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from scripts.fake_backend import SAMPLE_ANCHORS, fakes

RESUME = b"Jane Doe. Machine learning engineer. Built Python and Docker pipelines on AWS. Improved model precision by 30%. Led a team of 4."
LETTER = b"Dear team, I built and delivered a monitoring system that improved alert precision by 30%. I led the rollout with the product team. Thank you."


def hidden(html: str, name: str) -> str:
    return re.search(rf'name="{name}" value="([^"]+)"', html).group(1)


class FullJourneyTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.multiple(app, **fakes(latency=0))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app.app)

    def assert_page(self, response, *expected):
        self.assertEqual(response.status_code, 200, response.text[:500])
        for text in expected:
            self.assertIn(text, response.text)
        for leak in ("Traceback", "{{", "{%", "undefined"):
            self.assertNotIn(leak, response.text)
        return response.text

    def sign_up_and_build_profile(self, email="jane@example.com"):
        page = self.client.post("/signup", data={"email": email, "password": "pw-123456"})
        self.assert_page(page, "Build your", 'cta-button" disabled')
        page = self.client.post("/profile/resume", files={"resume": ("resume.txt", RESUME, "text/plain")})
        self.assert_page(page, "resume.txt", "Uploaded")
        page = self.client.post("/profile/cover-letters", files={"files": ("letter1.txt", LETTER, "text/plain")})
        self.assert_page(page, "letter1.txt", "1 of 5 uploaded")
        return self.client.post("/profile/build")

    def test_signup_to_accepted_letter(self):
        ready = self.assert_page(self.sign_up_and_build_profile(), "Your profile is", "How you sound")
        # The voice card must show traits the real profile builder produces, not just one line.
        self.assertGreaterEqual(ready.count("<dt>"), 2)

        self.assert_page(self.client.get("/profile/job-input"), "Paste the role details")
        angles = self.client.post("/profile/job-input", data={"job_description": "Senior AI engineer", "company_url": "https://example.com"})
        angles_html = self.assert_page(angles, "Generated angles", SAMPLE_ANCHORS["anchors"][0]["title"])

        letter = self.client.post("/company/angles", data={"selected_anchor": "0", "job_application_id": hidden(angles_html, "job_application_id")})
        letter_html = self.assert_page(letter, "GENERATED COVER LETTER", "Original draft", "Revise letter")

        job_id = hidden(letter_html, "job_application_id")
        revised = self.client.post("/cover-letter/revise", data={"job_application_id": job_id, "feedback": "Warmer tone."})
        revised_html = self.assert_page(revised, "Revision 1 of 3", "[Revised for: Warmer tone.]", "Feedback applied so far:")

        accepted = self.client.post("/cover-letter/accept", data={"job_application_id": job_id, "cover_letter_id": hidden(revised_html, "cover_letter_id")})
        accepted_html = self.assert_page(accepted, "Final version accepted.", "Write another letter")
        self.assertNotIn("Revise letter", accepted_html)

    def test_service_failures_show_a_message_not_a_crash(self):
        self.sign_up_and_build_profile()
        research_down = self.client.post("/profile/job-input", data={"job_description": "Senior AI engineer", "company_url": "https://fail.example.com"})
        self.assert_page(research_down, "Request timed out while fetching the company website")

        angles = self.client.post("/profile/job-input", data={"job_description": "FAIL please", "company_url": "https://example.com"})
        groq_down = self.client.post("/company/angles", data={"selected_anchor": "0", "job_application_id": hidden(angles.text, "job_application_id")})
        self.assert_page(groq_down, "Groq API is unavailable", "Generated angles")

        angles = self.client.post("/profile/job-input", data={"job_description": "BUSY role", "company_url": "https://example.com"})
        busy = self.client.post("/company/angles", data={"selected_anchor": "0", "job_application_id": hidden(angles.text, "job_application_id")})
        busy_html = self.assert_page(busy, "Lots of people are writing right now. Please try again in a minute.")
        self.assertNotIn("org_FAKE", busy_html)

    def test_bad_login_and_logout(self):
        self.assert_page(self.client.post("/login", data={"email": "fail@example.com", "password": "x"}), "Invalid login credentials")
        self.sign_up_and_build_profile()
        self.client.get("/logout")
        response = self.client.get("/profile/setup", follow_redirects=False)
        self.assertEqual(response.headers["location"], "/login")


if __name__ == "__main__":
    unittest.main()
