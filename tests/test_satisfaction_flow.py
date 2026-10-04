import unittest
from contextlib import ExitStack
from unittest.mock import patch

import fitz
from fastapi.testclient import TestClient

import app

USER = {"id": "00000000-0000-0000-0000-000000000001", "name": "resham.joshi"}
JOB = {"id": "job-1", "user_id": USER["id"], "job_description": "Senior AI engineer", "company_url": "https://example.com", "selected_anchor": {"title": "Angle"}}
FINAL_TEXT = "Dear Hiring Manager,\n\nRevision three letter body.\n\nSincerely,\nResham Joshi"


def chain(last_revision, final=False, satisfaction=None):
    rows = [{"id": f"r{n}", "revision_number": n, "content": f"Letter revision {n}", "feedback": f"feedback {n}" if n else None, "is_final": False} for n in range(last_revision + 1)]
    rows[-1].update({"content": FINAL_TEXT, "is_final": final, "satisfaction": satisfaction})
    return rows


class SatisfactionFlowTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(app, "app_user_for_request", return_value=USER))
        self.mock_chain = self.stack.enter_context(patch.object(app, "get_generated_cover_letters_for_job", return_value=chain(3)))
        self.mock_satisfaction = self.stack.enter_context(patch.object(app, "save_letter_satisfaction", return_value={}))
        self.mock_final = self.stack.enter_context(patch.object(app, "mark_generated_cover_letter_final", return_value={}))
        self.mock_revise = self.stack.enter_context(patch.object(app, "generate_cover_letter_revision", return_value="Revised letter text"))
        self.mock_save_letter = self.stack.enter_context(patch.object(app, "save_generated_cover_letter", return_value={"id": "r3"}))
        self.stack.enter_context(patch.object(app, "get_job_application", return_value=JOB))
        self.stack.enter_context(patch.object(app, "get_style_profile", return_value={"profile": {"tone": "x"}}))
        self.stack.enter_context(patch.object(app, "get_cover_letters_for_user", return_value=[{"filename": "l.txt", "content": "evidence"}]))
        self.stack.enter_context(patch.object(app, "get_resumes_for_user", return_value=[]))

    def answer(self, satisfied, feedback=""):
        return self.client.post("/cover-letter/satisfaction", data={"job_application_id": "job-1", "satisfied": satisfied, "feedback": feedback}, follow_redirects=False)

    # A. After revision 3 the satisfaction dialog appears (and no "maximum reached" warning).
    def test_dialog_appears_after_third_revision(self):
        self.mock_chain.return_value = chain(2)
        html = self.client.post("/cover-letter/revise", data={"job_application_id": "job-1", "feedback": "Make it tighter"}).text
        self.assertEqual(self.mock_revise.call_count, 1)
        self.assertEqual(self.mock_save_letter.call_args.kwargs["revision_number"], 3)
        self.assertIn("Your cover letter is ready 🎉", html)
        self.assertIn("You've completed 3 revisions. Are you satisfied with your cover letter?", html)
        self.assertIn(">YES</button>", html)
        self.assertIn(">NO</button>", html)
        self.assertNotIn("has been reached", html)
        self.assertNotIn('action="/cover-letter/revise"', html)
        self.assertNotIn('action="/cover-letter/accept"', html)

    # B. YES marks the current letter final, records "satisfied", generates nothing.
    def test_yes_accepts_current_letter_without_new_revision(self):
        response = self.answer("yes")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/cover-letter")
        self.assertEqual(self.mock_satisfaction.call_args.args[1:4], ("r3", "satisfied", None))
        self.assertEqual(self.mock_final.call_args.args[1:3], ("job-1", "r3"))
        self.assertEqual(self.mock_revise.call_count, 0)

        # D. Refresh: the page is rebuilt from the database and keeps the accepted state.
        self.mock_chain.return_value = chain(3, final=True, satisfaction="satisfied")
        html = self.client.get("/cover-letter").text
        self.assertIn("Great! Your cover letter is ready.", html)
        self.assertIn('action="/cover-letter/pdf"', html)

        pdf = self.client.get("/cover-letter/pdf")
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        self.assertIn("attachment", pdf.headers["content-disposition"])
        with fitz.open(stream=pdf.content, filetype="pdf") as document:
            text = "".join(page.get_text() for page in document)
        self.assertIn("Revision three letter body.", text)
        self.assertIn("Resham Joshi", text)

    def test_pdf_requires_an_accepted_letter(self):
        self.answer("no", "x")  # sets the workflow in the session without accepting
        response = self.client.get("/cover-letter/pdf", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/cover-letter")

    # C. NO stores product feedback on the current letter; no final, no 4th revision.
    def test_no_saves_feedback_without_new_revision(self):
        response = self.answer("no", "  The tone was too formal.  ")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.mock_satisfaction.call_args.args[1:4], ("r3", "not_satisfied", "The tone was too formal."))
        self.assertEqual(self.mock_final.call_count, 0)
        self.assertEqual(self.mock_revise.call_count, 0)
        self.assertEqual(self.mock_save_letter.call_count, 0)

        self.mock_chain.return_value = chain(3, satisfaction="not_satisfied")
        html = self.client.get("/cover-letter").text
        self.assertIn("Thank you for your feedback.", html)
        self.assertNotIn(">YES</button>", html)  # the YES/NO question does not come back
        self.assertNotIn('<form class="feedback-form" data-step="feedback"', html)
        self.assertIn(FINAL_TEXT.split("\n")[2], html)  # letter stays visible

    # D. Database failure: no false success/download, and the dialog stays recoverable.
    def test_yes_save_failure_shows_recoverable_error_without_download(self):
        self.mock_satisfaction.side_effect = Exception("{'code': 'PGRST204', 'message': \"Could not find the 'satisfaction' column\"}")
        response = self.answer("yes")
        html = response.text
        self.assertEqual(response.status_code, 200)
        self.assertIn("We couldn&#39;t save your response. Please try again.", html)
        self.assertNotIn("PGRST204", html)
        self.assertNotIn('action="/cover-letter/pdf"', html)
        self.assertNotIn("Great! Your cover letter is ready.", html)
        self.assertIn(">YES</button>", html)  # user can retry
        self.assertEqual(self.mock_final.call_count, 0)

    def test_no_save_failure_reopens_feedback_with_draft(self):
        self.mock_satisfaction.side_effect = Exception("database unavailable")
        html = self.answer("no", "Too formal").text
        self.assertIn("We couldn&#39;t save your response. Please try again.", html)
        self.assertNotIn("Thank you for your feedback.", html)
        self.assertIn('data-step="question" hidden', html)
        self.assertIn('action="/cover-letter/satisfaction" >', html)  # feedback step visible (no hidden attr)
        self.assertIn(">Too formal</textarea>", html)
        self.assertEqual(self.mock_revise.call_count, 0)

    def test_accept_failure_after_saving_answer_shows_no_download(self):
        self.mock_final.side_effect = Exception("update failed")
        html = self.answer("yes").text
        self.assertIn("We couldn&#39;t save your response. Please try again.", html)
        self.assertNotIn('action="/cover-letter/pdf"', html)
        self.assertIn(">YES</button>", html)

    def test_saves_against_current_users_letter_from_database(self):
        self.answer("yes")
        self.assertEqual(self.mock_chain.call_args.args[:2], (USER["id"], "job-1"))
        self.assertEqual(self.mock_satisfaction.call_args.args[0], USER["id"])
        self.assertEqual(self.mock_final.call_args.args[0], USER["id"])

    def test_no_thank_you_survives_duplicate_third_revisions(self):
        # Two revision-3 rows (e.g. a double-submitted revise request); the answer was saved on the one that is not last.
        rows = chain(3)
        duplicate = {**rows[-1], "id": "r3-dup", "satisfaction": None}
        rows[-1]["satisfaction"] = "not_satisfied"
        self.mock_chain.return_value = rows + [duplicate]

        self.answer("no", "test 1")  # puts the workflow in the session
        html = self.client.get("/cover-letter").text
        self.assertIn("Thank you for your feedback.", html)
        self.assertIn("Your feedback has been saved and will help us improve Cover Letter AI.", html)
        self.assertNotIn(">YES</button>", html)
        self.assertNotIn(">NO</button>", html)
        self.assertNotIn('<form class="feedback-form" data-step="feedback"', html)
        self.assertEqual(self.mock_revise.call_count, 0)

    def test_no_with_empty_feedback_is_rejected(self):
        html = self.answer("no", "   ").text
        self.assertIn("Please write your feedback before submitting.", html)
        self.assertEqual(self.mock_satisfaction.call_count, 0)

    def test_satisfaction_is_only_available_after_last_revision(self):
        self.mock_chain.return_value = chain(2)
        html = self.answer("yes").text
        self.assertIn("only available after the final revision", html)
        self.assertEqual(self.mock_satisfaction.call_count, 0)
        self.assertEqual(self.mock_final.call_count, 0)

    # E. Revisions 1-2 unchanged; no 4th revision is possible.
    def test_earlier_revisions_unchanged_and_no_fourth_revision(self):
        self.mock_chain.return_value = chain(1)
        html = self.client.post("/cover-letter/revise", data={"job_application_id": "job-1", "feedback": "More specific"}).text
        self.assertIn('action="/cover-letter/revise"', html)
        self.assertIn('action="/cover-letter/accept"', html)
        self.assertNotIn("satisfaction-dialog", html)

        self.mock_chain.return_value = chain(3)
        self.mock_revise.reset_mock()
        html = self.client.post("/cover-letter/revise", data={"job_application_id": "job-1", "feedback": "One more"}).text
        self.assertEqual(self.mock_revise.call_count, 0)
        self.assertIn("Your cover letter is ready 🎉", html)


if __name__ == "__main__":
    unittest.main()
