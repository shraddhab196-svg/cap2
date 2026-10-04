"""Fixes from the security review: one per test class."""
import io
import json
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

import app
from src import anchor_generator, cover_letter_generator
from src.profile_builder import MAX_UPLOAD_BYTES, read_uploaded_text

ANCHORS = json.loads((app.BASE_DIR / "tests" / "fixtures" / "sample_anchors.json").read_text(encoding="utf-8"))["anchors"]
LETTER = (app.BASE_DIR / "tests" / "fixtures" / "sample_letter.txt").read_text(encoding="utf-8")
USER = {"id": "u1", "email": "jane@example.com"}


class CandidateNameTests(unittest.TestCase):
    """Every user's letters used to be written and signed as one hardcoded real person."""

    def test_name_comes_from_the_top_of_the_resume(self):
        cases = {
            "Jane Doe\nML Engineer": "Jane Doe",
            "JANE MARIE DOE\njane@x.com": "Jane Marie Doe",
            "Curriculum Vitae\nJosé Núñez-García": "José Núñez-García",
            "Mary-Jane O'Brien | Berlin": "Mary-Jane O'Brien",
            "Jane Doe, M.Sc.": "Jane Doe",
            "Senior Data Scientist\nJane Doe": "Jane Doe",
            "Resume\nPython, SQL, AWS\n2019 – 2024": None,
            "": None,
        }
        for resume, expected in cases.items():
            with self.subTest(resume=resume):
                self.assertEqual(cover_letter_generator.extract_candidate_name(resume), expected)

    def test_prompts_use_this_users_name_or_none(self):
        args = ("Senior ML engineer", ANCHORS[:1], {"tone": "warm"}, [("l.txt", LETTER)], "https://northwind.example")
        named = cover_letter_generator.build_cover_letter_plan_prompt(*args, "Jane Doe")
        final = cover_letter_generator.build_cover_letter_prompt(*args, {"hook": "x"}, "Jane Doe")
        unnamed = cover_letter_generator.build_cover_letter_prompt(*args, {"hook": "x"}, None)
        self.assertIn("Candidate name: Jane Doe.", named)
        self.assertIn("Candidate name: Jane Doe. Sign the letter with exactly this name", final)
        self.assertNotIn("Candidate name:", unnamed)
        self.assertIn("never invent or guess a name", unnamed)

    def test_a_letter_without_a_particular_name_is_accepted(self):
        cover_letter_generator.validate_generated_letter(LETTER.replace("Jane Doe", "Sam Lee"), ANCHORS[:1], "ML engineer")


class ErrorMessageTests(unittest.TestCase):
    def test_our_messages_are_shown_and_library_errors_are_not(self):
        self.assertEqual(app.user_error(ValueError("Company URL is required.")), "Company URL is required.")
        with self.assertLogs("app", "ERROR"):
            self.assertEqual(app.user_error(RuntimeError('column "anchors" does not exist')), app.GENERIC_ERROR)
        with self.assertLogs("app", "ERROR"):
            self.assertEqual(app.user_error(json.JSONDecodeError("Expecting value", "doc", 0)), app.GENERIC_ERROR)


class NoUserTextInLogsTests(unittest.TestCase):
    def test_generating_angles_prints_and_logs_no_letter_or_resume_text(self):
        secret = "UNIQUE-RESUME-DETAIL-42"
        anchors = [{**anchor, "candidate_evidence": secret} for anchor in ANCHORS]
        reply = json.dumps({"anchors": anchors})
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])
        stdout = io.StringIO()
        with patch.object(anchor_generator, "get_llm_client", return_value=(client, "m")), \
             patch("sys.stdout", stdout), self.assertLogs(anchor_generator.logger, logging.INFO) as logs:
            anchor_generator.generate_anchors("https://northwind.example", "ML engineer", "research", [("l.txt", "letter")])
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(secret, "\n".join(logs.output))

    def test_bad_model_json_is_logged_by_size_only(self):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not json UNIQUE-RESUME-DETAIL-42"))])
        with patch.object(anchor_generator, "get_llm_client", return_value=(client, "m")), self.assertLogs(anchor_generator.logger, logging.ERROR) as logs:
            with self.assertRaises(Exception):
                anchor_generator.generate_anchors("https://northwind.example", "ML engineer", "research", [("l.txt", "letter")])
        self.assertNotIn("UNIQUE-RESUME-DETAIL-42", "\n".join(logs.output))


class DailyLimitTests(unittest.TestCase):
    def test_job_lookups_stop_at_the_daily_limit(self):
        client = TestClient(app.app)
        with patch.object(app, "app_user_for_request", return_value=USER), \
             patch.object(app, "get_request_session_tokens", return_value=("a", "r")), \
             patch.object(app, "count_recent_ai_actions", return_value=app.DAILY_AI_LIMIT), \
             patch.object(app, "research_company") as research:
            response = client.post("/profile/job-input", data={"job_description": "ML engineer", "company_url": "https://example.com"})
        self.assertIn("reached today&#39;s limit", response.text)
        research.assert_not_called()


class UploadSizeTests(unittest.TestCase):
    def test_files_over_5_mb_are_refused(self):
        big = UploadFile(file=io.BytesIO(b"x" * (MAX_UPLOAD_BYTES + 1)), filename="letter.txt")
        with self.assertRaisesRegex(ValueError, "larger than 5 MB"):
            read_uploaded_text(big)
        small = UploadFile(file=io.BytesIO(b"Dear team"), filename="letter.txt")
        self.assertEqual(read_uploaded_text(small), "Dear team")


class SessionAndHeaderTests(unittest.TestCase):
    def setUp(self):
        self.supabase = MagicMock()
        patcher = patch.object(app, "get_supabase_client", return_value=self.supabase)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app.app, follow_redirects=False)

    def login(self):
        claims = "eyJzdWIiOiJ1MSIsImV4cCI6NDEwMjQ0NDgwMH0"  # {"sub":"u1","exp":4102444800}
        session = SimpleNamespace(access_token=f"h.{claims}.s", refresh_token="r1")
        self.supabase.auth.sign_in_with_password.return_value = SimpleNamespace(user=SimpleNamespace(id="auth-1"), session=session)
        self.client.post("/login", data={"email": "jane@example.com", "password": "letters123"})
        return session.access_token

    def test_logout_also_ends_the_session_at_supabase(self):
        token = self.login()
        self.client.get("/logout")
        self.supabase.auth.admin.sign_out.assert_called_once_with(token, "local")

    def test_security_headers_and_no_caching_once_signed_in(self):
        public = self.client.get("/")
        for header in ("Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy"):
            self.assertIn(header, public.headers)
        self.assertIn("frame-ancestors 'none'", public.headers["Content-Security-Policy"])
        self.assertNotEqual(public.headers.get("Cache-Control"), "no-store")
        self.login()
        self.assertEqual(self.client.get("/").headers.get("Cache-Control"), "no-store")

    def test_cdn_scripts_are_pinned_by_hash(self):
        html = self.client.get("/").text
        for script in ("gsap.min.js", "ScrollTrigger.min.js", "SplitText.min.js", "lenis.min.js"):
            tag = next(line for line in html.splitlines() if script in line)
            self.assertIn('integrity="sha384-', tag)


if __name__ == "__main__":
    unittest.main()
