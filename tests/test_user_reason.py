import unittest
from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from src import cover_letter_generator as generator

USER = {"id": "00000000-0000-0000-0000-0000000000c3", "name": "jane.doe", "email": "jane.doe@example.com"}
COMPANY = "https://www.fernwick-labs.example/"
REASON = "My grandmother's clinic struggled with night rotas, and Fernwick fixes exactly that."
REASON_LINE = "Candidate's own reason for choosing this company (their words; base the why-this-company part on it, do not embellish or add motives): "
BASE_ANCHOR = {"title": "Fair rotas", "company_evidence": "e", "job_connection": "j", "candidate_evidence": "c", "anchor": "a", "source_url": COMPANY}


def build_all(anchor):
    style, letters = {"tone": "x"}, [("a", "b")]
    return {
        "plan": generator.build_cover_letter_plan_prompt("JD", [anchor], style, letters, COMPANY),
        "letter": generator.build_cover_letter_prompt("JD", [anchor], style, letters, COMPANY, {"hook": "h"}),
        "revision": generator.build_cover_letter_revision_prompt("current letter", "shorter", "JD", [anchor], style, letters, COMPANY),
    }


class PromptTests(unittest.TestCase):
    def test_prompts_unchanged_when_reason_absent_or_blank(self):
        without = build_all(dict(BASE_ANCHOR))
        for blank in ("", "   ", None):
            with self.subTest(blank=blank):
                self.assertEqual(build_all(dict(BASE_ANCHOR, user_reason=blank)), without)
        for prompt in without.values():
            self.assertNotIn("Candidate's own reason", prompt)

    def test_reason_line_in_all_three_builders(self):
        prompts = build_all(dict(BASE_ANCHOR, user_reason=REASON))
        for name, prompt in prompts.items():
            with self.subTest(builder=name):
                self.assertIn(REASON_LINE + REASON + "\n", prompt)


class JobInputReasonTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        anchors = [dict(BASE_ANCHOR, title=f"Angle {i}") for i in range(3)]
        for name, value in {
            "app_user_for_request": USER,
            "count_recent_ai_actions": 0,
            "get_cover_letters_for_user": [{"filename": "l.txt", "content": "My letter."}],
            "research_company": {"company_url": COMPANY, "company_research": "### Source: x\n" + "word " * 300, "pages": [COMPANY]},
            "generate_anchors": {"anchors": anchors},
        }.items():
            self.stack.enter_context(patch.object(app, name, return_value=value))
        self.save = self.stack.enter_context(patch.object(app, "save_job_application", return_value={"id": "job-c3"}))

    def submit(self, user_reason):
        return self.client.post("/profile/job-input", data={"job_description": "JD", "company_url": COMPANY, "user_reason": user_reason}, follow_redirects=False)

    def saved_anchors(self):
        return self.save.call_args.kwargs["anchors"]

    def test_reason_is_stored_on_every_anchor(self):
        self.assertEqual(self.submit(f"  {REASON}  ").status_code, 303)
        self.assertEqual([a["user_reason"] for a in self.saved_anchors()], [REASON] * 3)

    def test_reason_is_single_line_and_capped_at_300(self):
        self.submit("first line\nsecond line\r\n" + "x" * 500)
        stored = self.saved_anchors()[0]["user_reason"]
        self.assertEqual(len(stored), app.USER_REASON_MAX_CHARS)
        self.assertTrue(stored.startswith("first line second line x"))
        self.assertNotIn("\n", stored)

    def test_no_reason_leaves_anchors_untouched(self):
        self.submit("   ")
        self.assertTrue(all("user_reason" not in a for a in self.saved_anchors()))

    def test_reason_field_present_and_not_shown_on_angles_page(self):
        page = self.client.get("/profile/job-input").text
        self.assertIn('name="user_reason"', page)
        self.assertIn('maxlength="300"', page)
        angles = app.templates.env.get_template("company_angles.html").render(
            request=None, user=USER, company_url=COMPANY, job_description="JD", job_application_id="job-c3",
            anchors=[dict(BASE_ANCHOR, user_reason=REASON)], error=None,
        )
        self.assertNotIn("grandmother", angles)


class RevisionCarriesReasonTests(unittest.TestCase):
    def test_reason_reaches_revision_prompt_via_selected_anchor(self):
        selected = dict(BASE_ANCHOR, user_reason=REASON)
        job = {"id": "job-c3", "user_id": USER["id"], "job_description": "JD", "company_url": COMPANY, "selected_anchor": selected, "anchors": [selected]}
        chain = [{"id": "r0", "revision_number": 0, "content": "Dear Hiring Manager,\n\nDraft.\n\nSincerely,", "feedback": None, "is_final": False}]
        client = TestClient(app.app)
        with ExitStack() as stack:
            for name, value in {
                "app_user_for_request": USER,
                "count_recent_ai_actions": 0,
                "get_job_application": job,
                "get_generated_cover_letters_for_job": chain,
                "get_style_profile": {"profile": {"tone": "x"}},
                "get_cover_letters_for_user": [{"filename": "l.txt", "content": "My letter."}],
                "get_resumes_for_user": [],
                "save_generated_cover_letter": {"id": "r1"},
            }.items():
                stack.enter_context(patch.object(app, name, return_value=value))
            revise = stack.enter_context(patch.object(app, "generate_cover_letter_revision", return_value="Revised letter"))
            client.post("/cover-letter/revise", data={"job_application_id": "job-c3", "feedback": "Shorter please"})

        passed_anchors = revise.call_args.kwargs["selected_anchors"]
        self.assertEqual(passed_anchors[0]["user_reason"], REASON)
        prompt = generator.build_cover_letter_revision_prompt("current", "Shorter please", "JD", passed_anchors, {"tone": "x"}, [("a", "b")], COMPANY)
        self.assertIn(REASON_LINE + REASON, prompt)


if __name__ == "__main__":
    unittest.main()
