"""The name signed under letters, and editing a letter by hand."""
import base64
import json
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app

USER = {"id": "app-user-1", "email": "jane@example.com"}
CHAIN = [{"id": "l0", "revision_number": 0, "content": "Dear team,\n\nBody.\n\nSincerely,\nJane Doe", "is_final": False, "feedback": None}]


def jwt() -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"sub": "auth-1", "exp": int(time.time()) + 3600}).encode()).decode().rstrip("=")
    return f"h.{claims}.s"


class LetterNameTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app, follow_redirects=False)
        self.supabase = MagicMock()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(app, "get_supabase_client", return_value=self.supabase))
        stack.enter_context(patch.object(app, "get_or_create_app_user", return_value={"id": USER["id"], "name": "jane"}))
        stack.enter_context(patch.object(app, "get_resumes_for_user", return_value=[{"id": "r1", "extracted_text": "Software Engineer\nJANE Q DOE\njane@x.com"}]))
        for name in ("get_cover_letters_for_user", "get_candidate_profile", "get_style_profile"):
            stack.enter_context(patch.object(app, name, return_value=[] if name == "get_cover_letters_for_user" else None))
        self.save_name = stack.enter_context(patch.object(app, "save_user_full_name"))

    def login(self, metadata):
        user = SimpleNamespace(id="auth-1", user_metadata=metadata)
        self.supabase.auth.sign_in_with_password.return_value = SimpleNamespace(user=user, session=SimpleNamespace(access_token=jwt(), refresh_token="r1"))
        self.client.post("/login", data={"email": "jane@example.com", "password": "letters123"})

    def test_google_name_beats_the_resume(self):
        self.login({"full_name": "Jane Doe"})
        page = self.client.get("/profile/setup").text
        self.assertIn('value="Jane Doe"', page)
        self.assertIn(">Saved<", page)

    def test_without_an_account_name_the_resume_is_used_and_job_titles_are_skipped(self):
        self.login({})
        page = self.client.get("/profile/setup").text
        self.assertIn('value="Jane Q Doe"', page)
        self.assertIn("We read this from your resume", page)

    def test_saved_name_is_stored_on_the_login_and_used_from_then_on(self):
        self.login({})
        response = self.client.post("/profile/name", data={"full_name": "  Jane   Doe "})
        self.assertEqual(response.headers["location"], "/profile/setup")
        self.assertEqual(self.save_name.call_args.args[0], "Jane Doe")
        self.assertIn('value="Jane Doe"', self.client.get("/profile/setup").text)

    def test_names_with_digits_or_symbols_are_refused(self):
        self.login({})
        response = self.client.post("/profile/name", data={"full_name": "<b>Jane</b> 2"})
        self.assertIn("Use letters only for your name", response.text)
        self.save_name.assert_not_called()

    def test_saving_failure_is_a_friendly_message(self):
        self.login({})
        self.save_name.side_effect = RuntimeError("auth down")
        with self.assertLogs("app", "ERROR"):
            response = self.client.post("/profile/name", data={"full_name": "Jane Doe"})
        self.assertIn(app.GENERIC_ERROR, response.text)

    def test_letters_are_signed_with_the_account_name(self):
        self.login({"full_name": "Jane Doe"})
        request = SimpleNamespace(session={"auth_user": {"full_name": "Jane Doe"}})
        self.assertEqual(app.letter_name_for(request, USER["id"], "t", "r"), "Jane Doe")
        request = SimpleNamespace(session={"auth_user": {}})
        self.assertEqual(app.letter_name_for(request, USER["id"], "t", "r"), "Jane Q Doe")


class EditLetterTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app, follow_redirects=False)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(app, "app_user_for_request", return_value=USER))
        stack.enter_context(patch.object(app, "get_request_session_tokens", return_value=("t", "r")))
        stack.enter_context(patch.object(app, "get_generated_cover_letters_for_job", return_value=[dict(row) for row in CHAIN]))
        self.update = stack.enter_context(patch.object(app, "update_generated_cover_letter_content"))

    def post(self, content, letter_id="l0"):
        return self.client.post("/cover-letter/edit", data={"job_application_id": "job-1", "cover_letter_id": letter_id, "content": content})

    def test_edits_are_saved_without_using_a_revision(self):
        response = self.post("Dear team,\r\n\r\nBetter body.\r\n\r\nSincerely,\r\nJane Doe\r\n")
        self.assertEqual(response.headers["location"], "/cover-letter")
        self.update.assert_called_once_with(USER["id"], "l0", "Dear team,\n\nBetter body.\n\nSincerely,\nJane Doe", access_token="t", refresh_token="r")

    def test_unchanged_text_is_not_rewritten(self):
        self.post(CHAIN[0]["content"])
        self.update.assert_not_called()

    def test_empty_or_foreign_letters_are_refused(self):
        self.assertIn("can&#39;t be empty", self.post("   ").text)
        self.assertIn("does not belong to this job application", self.post("text", letter_id="someone-elses").text)
        self.update.assert_not_called()

    def test_letter_page_offers_the_editor(self):
        with patch.object(app, "get_job_application", return_value={"id": "job-1"}):
            page = app.render_cover_letter_page(SimpleNamespace(session={}), USER, "job-1", [dict(row) for row in CHAIN]).body.decode()
        self.assertIn('action="/cover-letter/edit"', page)
        self.assertIn("data-edit-letter", page)
        self.assertIn("Your edits don't use up a revision.", page)


if __name__ == "__main__":
    unittest.main()
