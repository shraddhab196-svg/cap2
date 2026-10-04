import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from src import cover_letter_generator as generator
from src.profile_builder import extract_candidate_name

ROOT = Path(__file__).resolve().parents[1]
ANCHORS = [{"title": "Angle", "company_evidence": "c", "job_connection": "j", "candidate_evidence": "e", "anchor": "a"}]
USER_A = {"id": "aaaaaaaa-0000-0000-0000-00000000000a", "name": "usera"}
USER_B = {"id": "bbbbbbbb-0000-0000-0000-00000000000b", "name": "userb"}
RESUMES = {
    USER_A["id"]: [{"id": "ra", "extracted_text": "Resham Joshi\nMunich, Germany\nresham@example.com\nMachine learning engineer"}],
    USER_B["id"]: [{"id": "rb", "extracted_text": "RAHUL SHARMA\n+49 170 1234567 | rahul@example.com\nData analyst"}],
}


def letter_signed_by(name):
    body = " ".join(["delivered"] * 240)
    return f"Dear Hiring Manager,\n\n{body}\n\nSincerely,\n{name}"


def resumes_for(user_id, **_):
    return RESUMES.get(user_id, [])


class NoHardCodedIdentityTests(unittest.TestCase):
    def test_production_code_does_not_contain_resham(self):
        for relative in ("src/cover_letter_generator.py", "src/profile_builder.py", "src/database.py", "src/writing_framework.py", "app.py"):
            with self.subTest(relative):
                self.assertNotIn("resham", (ROOT / relative).read_text(encoding="utf-8").lower())
        for template in (ROOT / "templates").rglob("*.html"):
            self.assertNotIn("resham", template.read_text(encoding="utf-8").lower(), str(template))

    def test_prompts_without_a_known_name_never_default_to_resham(self):
        prompts = [
            generator.build_cover_letter_plan_prompt("JD", ANCHORS, {"tone": "x"}, [("a", "b")], "https://x"),
            generator.build_cover_letter_prompt("JD", ANCHORS, {"tone": "x"}, [("a", "b")], "https://x", {"hook": "h"}),
            generator.build_cover_letter_revision_prompt("letter", "shorter", "JD", ANCHORS, {"tone": "x"}, [("a", "b")], "https://x"),
        ]
        for prompt in prompts:
            self.assertNotIn("resham", prompt.lower())
            self.assertIn("Candidate identity: not provided. Do not invent or guess a name", prompt)

    def test_prompts_use_the_current_candidates_name(self):
        for name in ("Rahul Sharma", "Resham Joshi"):
            with self.subTest(name):
                plan = generator.build_cover_letter_plan_prompt("JD", ANCHORS, {"tone": "x"}, [("a", "b")], "https://x", candidate_name=name)
                draft = generator.build_cover_letter_prompt("JD", ANCHORS, {"tone": "x"}, [("a", "b")], "https://x", {"hook": "h"}, candidate_name=name)
                revision = generator.build_cover_letter_revision_prompt("letter", "shorter", "JD", ANCHORS, {"tone": "x"}, [("a", "b")], "https://x", candidate_name=name)
                for prompt in (plan, draft, revision):
                    self.assertIn(f"Candidate identity: {name}.", prompt)
                for prompt in (draft, revision):
                    self.assertIn(f"Sign the letter with the candidate's name exactly as: {name}", prompt)
                if name != "Resham Joshi":
                    self.assertNotIn("resham", (plan + draft + revision).lower())


class ValidationTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(generator, "semantic_anchor_validation", return_value={"reflected": True})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_validation_does_not_require_resham(self):
        generator.validate_generated_letter(letter_signed_by("Rahul Sharma"), ANCHORS, "JD")
        generator.validate_generated_letter(letter_signed_by("Rahul Sharma"), ANCHORS, "JD", candidate_name="Rahul Sharma")
        generator.validate_generated_letter(letter_signed_by(""), ANCHORS, "JD")  # no name known -> no name rule

    def test_validation_uses_current_candidates_name(self):
        generator.validate_generated_letter(letter_signed_by("Resham Joshi"), ANCHORS, "JD", candidate_name="Resham Joshi")
        with self.assertRaises(ValueError):
            generator.validate_generated_letter(letter_signed_by("Resham Joshi"), ANCHORS, "JD", candidate_name="Rahul Sharma")


class GenerationTests(unittest.TestCase):
    def generate(self, name, letter):
        with patch.object(generator, "load_environment"), \
             patch.object(generator, "get_groq_client", return_value=(None, "model")), \
             patch.object(generator, "generate_cover_letter_plan", return_value={"hook": "h"}) as mock_plan, \
             patch.object(generator, "semantic_anchor_validation", return_value={"reflected": True}), \
             patch.object(generator, "call_groq_json", return_value=json.dumps({"cover_letter": letter})) as mock_call:
            result = generator.generate_cover_letter("JD", ANCHORS, {"tone": "x"}, [("a", "b")], "https://x", candidate_name=name)
        return result, mock_plan, mock_call.call_args.args[3]

    def test_candidate_named_resham_still_generates(self):
        result, mock_plan, prompt = self.generate("Resham Joshi", letter_signed_by("Resham Joshi"))
        self.assertTrue(result.endswith("Resham Joshi"))
        self.assertEqual(mock_plan.call_args.kwargs["candidate_name"], "Resham Joshi")
        self.assertIn("Candidate identity: Resham Joshi.", prompt)

    def test_different_candidate_generates_with_their_own_name(self):
        result, mock_plan, prompt = self.generate("Rahul Sharma", letter_signed_by("Rahul Sharma"))
        self.assertTrue(result.endswith("Rahul Sharma"))
        self.assertEqual(mock_plan.call_args.kwargs["candidate_name"], "Rahul Sharma")
        self.assertIn("Candidate identity: Rahul Sharma.", prompt)
        self.assertNotIn("resham", prompt.lower())


class ExtractCandidateNameTests(unittest.TestCase):
    def test_extracts_name_from_resume_header(self):
        cases = {
            "Rahul Sharma\nBerlin\nrahul@example.com": "Rahul Sharma",
            "PRIYA NAIR\nSoftware Engineer": "Priya Nair",
            "Curriculum Vitae\nAnna Müller\nHamburg": "Anna Müller",
            "Rahul Sharma | Berlin | rahul@example.com": "Rahul Sharma",
            "Mary-Jane O'Neil\n+1 555 0100": "Mary-Jane O'Neil",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_candidate_name(text), expected)

    def test_returns_none_when_no_clear_name(self):
        for text in ("", "rahul@example.com\n+49 170 1234567", "Resume\nProfessional Experience\n2019-2024 Analyst"):
            with self.subTest(text=text):
                self.assertIsNone(extract_candidate_name(text))


class UserIsolationTests(unittest.TestCase):
    """User B must get User B's identity, resolved server-side from B's own resume — never A's, never from the form."""

    def setUp(self):
        self.client = TestClient(app.app)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.mock_resumes = self.stack.enter_context(patch.object(app, "get_resumes_for_user", side_effect=resumes_for))
        self.stack.enter_context(patch.object(app, "get_style_profile", return_value={"profile": {"tone": "x"}}))
        self.stack.enter_context(patch.object(app, "get_candidate_profile", return_value={"profile": {}}))
        self.stack.enter_context(patch.object(app, "get_cover_letters_for_user", return_value=[{"filename": "l.txt", "content": "B's own letter"}]))
        self.stack.enter_context(patch.object(app, "update_job_application_anchor", return_value={}))
        self.stack.enter_context(patch.object(app, "save_generated_cover_letter", return_value={"id": "new"}))
        self.job = {"id": "job-b", "user_id": USER_B["id"], "job_description": "JD", "company_url": "https://x", "selected_anchor": ANCHORS[0]}
        self.stack.enter_context(patch.object(app, "get_job_application", return_value=self.job))

    def test_first_draft_uses_user_bs_name_not_user_as(self):
        self.stack.enter_context(patch.object(app, "app_user_for_request", return_value=USER_B))
        self.stack.enter_context(patch.object(app, "get_generated_cover_letters_for_job", return_value=[]))
        mock_generate = self.stack.enter_context(patch.object(app, "generate_cover_letter", return_value=letter_signed_by("Rahul Sharma")))
        self.client.post("/company/angles", data={"selected_anchor": "0", "job_application_id": "job-b", "candidate_name": "Resham Joshi"})
        self.assertEqual(mock_generate.call_args.kwargs["candidate_name"], "Rahul Sharma")
        self.assertEqual(self.mock_resumes.call_args.args[0], USER_B["id"])

    def test_revision_uses_user_bs_name(self):
        self.stack.enter_context(patch.object(app, "app_user_for_request", return_value=USER_B))
        chain = [{"id": "r0", "revision_number": 0, "content": letter_signed_by("Rahul Sharma"), "feedback": None, "is_final": False}]
        self.stack.enter_context(patch.object(app, "get_generated_cover_letters_for_job", return_value=chain))
        mock_revise = self.stack.enter_context(patch.object(app, "generate_cover_letter_revision", return_value=letter_signed_by("Rahul Sharma")))
        self.client.post("/cover-letter/revise", data={"job_application_id": "job-b", "feedback": "Shorter", "candidate_name": "Resham Joshi"})
        self.assertEqual(mock_revise.call_args.kwargs["candidate_name"], "Rahul Sharma")
        self.assertEqual(self.mock_resumes.call_args.args[0], USER_B["id"])

    def test_user_a_still_gets_user_as_name(self):
        self.stack.enter_context(patch.object(app, "app_user_for_request", return_value=USER_A))
        self.stack.enter_context(patch.object(app, "get_generated_cover_letters_for_job", return_value=[]))
        mock_generate = self.stack.enter_context(patch.object(app, "generate_cover_letter", return_value=letter_signed_by("Resham Joshi")))
        self.client.post("/company/angles", data={"selected_anchor": "0", "job_application_id": "job-b"})
        self.assertEqual(mock_generate.call_args.kwargs["candidate_name"], "Resham Joshi")


if __name__ == "__main__":
    unittest.main()
