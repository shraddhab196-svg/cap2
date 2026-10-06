import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import app
from src import llm_client
from src.send_checks import send_check_items

RESUME = "Jane Doe. Built a rota planner that cut shift swaps by 30% across three clinics."
SOURCES = [RESUME, "Scheduling engineer role. Own rota quality."]
CLEAN = """Dear Hiring Manager,

Rota quality is the need this role names first, and it is the problem I have worked on most. In my last team I built a rota planner that cut shift swaps by 30% across three clinics.

I wrote the scheduling rules, tested them with the nurses who used them, and kept the planner running for two years. That work taught me to explain trade-offs plainly to people who are not engineers.

I would like to bring the same care to your scheduling product, and I am happy to walk through the planner and what I would change next.

Sincerely,
Jane Doe"""


class SendCheckItemsTests(unittest.TestCase):
    def test_clean_letter_has_no_notes(self):
        self.assertEqual(send_check_items(CLEAN, SOURCES), [])

    def test_unsupported_number_and_name_are_listed(self):
        letter = CLEAN.replace("by 30%", "by 45% at Brightwell")
        items = send_check_items(letter, SOURCES)
        self.assertEqual(len(items), 1)
        self.assertIn("Not found in your resume, past letters or the job description", items[0])
        self.assertIn("45%", items[0])
        self.assertIn("Brightwell", items[0])
        self.assertNotIn(RESUME, items[0])  # never echoes the source text

    def test_without_sources_the_fact_note_is_skipped(self):
        self.assertEqual(send_check_items(CLEAN.replace("30%", "45%"), None), [])

    def test_no_concrete_number(self):
        self.assertEqual(send_check_items(CLEAN.replace(" by 30%", ""), SOURCES), ["No concrete number or result. Consider adding one."])

    def test_stock_phrase(self):
        items = send_check_items(CLEAN.replace("I would like to bring", "I am passionate about bringing"), SOURCES)
        self.assertEqual(items, ["Stock phrases: “passionate about”. Consider saying it in your own words."])

    def test_wrong_body_paragraph_count(self):
        items = send_check_items(CLEAN.replace("clinics.\n\nI wrote", "clinics. I wrote"), SOURCES)
        self.assertEqual(items, ["2 body paragraphs. Three usually reads best."])

    def test_over_300_words(self):
        letter = CLEAN.replace("change next.", "change next" + " and keep it simple" * 60 + ".")
        items = send_check_items(letter, SOURCES)
        self.assertEqual(len(items), 1)
        self.assertRegex(items[0], r"^\d{3} words\. Keep it to 300 or fewer\.$")

    def test_multiple_issues_are_shown_together(self):
        letter = CLEAN.replace(" by 30%", "").replace("I would like to bring", "I am passionate about bringing")
        letter = letter.replace("clinics.\n\nI wrote", "clinics. I wrote") + " extra" * 300
        items = send_check_items(letter, SOURCES)
        self.assertEqual(len(items), 4)
        self.assertTrue(items[0].startswith("No concrete number"))
        self.assertTrue(items[1].startswith("Stock phrases"))
        self.assertTrue(items[2].startswith("2 body paragraphs"))
        self.assertTrue(items[3].endswith("Keep it to 300 or fewer."))

    def test_empty_letter_has_no_notes(self):
        self.assertEqual(send_check_items("", SOURCES), [])


USER = {"id": "00000000-0000-0000-0000-000000000001", "name": "jane.doe"}
JOB = {"id": "job-1", "user_id": USER["id"], "job_description": "Scheduling engineer role. Own rota quality.", "company_url": "https://fernwick-labs.example", "selected_anchor": {"title": "Fair rotas"}}


def chain(content, last_revision=0):
    rows = [{"id": f"r{n}", "revision_number": n, "content": CLEAN, "feedback": f"feedback {n}" if n else None, "is_final": False} for n in range(last_revision + 1)]
    rows[-1]["content"] = content
    return rows


class LetterPageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(app, "app_user_for_request", return_value=USER))
        self.mock_chain = stack.enter_context(patch.object(app, "get_generated_cover_letters_for_job", return_value=chain(CLEAN)))
        stack.enter_context(patch.object(app, "get_job_application", return_value=JOB))
        stack.enter_context(patch.object(app, "get_resumes_for_user", return_value=[{"extracted_text": RESUME}]))
        stack.enter_context(patch.object(app, "get_cover_letters_for_user", return_value=[{"filename": "l.txt", "content": "An earlier letter."}]))
        stack.enter_context(patch.object(app, "get_style_profile", return_value={"profile": {"tone": "x"}}))
        stack.enter_context(patch.object(app, "mark_generated_cover_letter_final", return_value={}))
        stack.enter_context(patch.object(app, "save_letter_satisfaction", return_value={}))
        self.mock_save = stack.enter_context(patch.object(app, "save_generated_cover_letter", return_value={"id": "r1"}))
        self.mock_revise = stack.enter_context(patch.object(app, "generate_cover_letter_revision"))
        # Any model call during these page loads fails the test.
        self.llm = stack.enter_context(patch.object(llm_client, "complete", side_effect=AssertionError("no LLM call expected")))

    def open_letter_page(self):
        """GET /cover-letter (the id comes from the session, which the satisfaction route sets)."""
        stored = self.mock_chain.return_value
        self.mock_chain.return_value = chain(CLEAN, last_revision=3)
        self.client.post("/cover-letter/satisfaction", data={"job_application_id": "job-1", "satisfied": "no", "feedback": "x"})
        self.mock_chain.return_value = stored
        return self.client.get("/cover-letter").text

    def test_clean_letter_page_has_no_warning_section(self):
        html = self.open_letter_page()
        self.assertIn("GENERATED COVER LETTER", html)
        self.assertNotIn("Check before sending", html)
        self.llm.assert_not_called()

    def test_page_load_shows_issues_using_loaded_sources(self):
        self.mock_chain.return_value = chain(CLEAN.replace("by 30%", "by 45%"))
        html = self.open_letter_page()
        self.assertIn("Check before sending", html)
        self.assertIn("45%", html.split("Check before sending", 1)[1])
        self.llm.assert_not_called()

    def test_revision_page_is_checked_with_the_routes_own_data(self):
        self.mock_revise.return_value = CLEAN.replace("I would like to bring", "I am passionate about bringing").replace("by 30%", "by 45%")
        html = self.client.post("/cover-letter/revise", data={"job_application_id": "job-1", "feedback": "Warmer"}).text
        self.assertIn("Revision 1 of 3", html)
        notes = html.split("Check before sending", 1)[1]
        self.assertIn("45%", notes)
        self.assertNotIn("30%", notes)  # supported by the resume, so not flagged
        self.assertIn("passionate about", notes)
        self.assertEqual(self.mock_revise.call_count, 1)  # the revision itself; the checks add nothing
        self.llm.assert_not_called()

    def test_accept_page_is_checked_too(self):
        self.mock_chain.return_value = chain(CLEAN.replace(" by 30%", ""))
        html = self.client.post("/cover-letter/accept", data={"job_application_id": "job-1", "cover_letter_id": "r0"}).text
        self.assertIn("Final version accepted.", html)
        self.assertIn("No concrete number or result.", html)

    def test_source_load_failure_still_shows_the_page_and_other_checks(self):
        self.mock_chain.return_value = chain(CLEAN.replace(" by 30%", ""))
        with patch.object(app, "get_resumes_for_user", side_effect=RuntimeError("db down")), self.assertLogs("app", "WARNING"):
            html = self.open_letter_page()
        self.assertIn("No concrete number or result.", html)
        self.assertNotIn("db down", html)


if __name__ == "__main__":
    unittest.main()
