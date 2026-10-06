import inspect
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from src import cover_letter_generator as generator
from src import resume_facts
from src.resume_facts import RESUME_FACTS_HEADER, RESUME_FACTS_RULES, format_resume_facts_section, select_resume_facts

JD = "Backend engineer for a scheduling platform: Python, FastAPI, PostgreSQL, Kubernetes and RAG search."
ANCHOR = {"title": "Reliable scheduling APIs", "job_connection": "The role owns the FastAPI services.", "anchor": "Built the scheduling backend."}
RELEVANT = "Built FastAPI services on Kubernetes that schedule 40,000 clinic shifts per month"
IRRELEVANT = "Organised the regional amateur choir festival and managed volunteer rotas"
RESUME = "\n".join([
    "Jane Alexandra Doe-Whitfield",
    "jane.doe@example.com | +49 170 1234567",
    "https://www.jane-doe.example/portfolio and other links",
    "Phone: (555) 010-0199 for references only please",
    f"• {RELEVANT}",
    f"• {IRRELEVANT}",
    "- Designed PostgreSQL schemas for the rota service used by three hospital groups",
])
LETTERS = [("letter.txt", "Dear Hiring Manager, I enjoy clear APIs and careful data models.")]
SAMPLE_ANCHOR = {"title": "t", "company_evidence": "e", "job_connection": "j", "candidate_evidence": "c", "anchor": "a", "source_url": "https://fernwick-labs.example"}
FACTS = "- Built FastAPI services on Kubernetes that schedule 40,000 clinic shifts per month"


def bullet_lines(facts):
    return [line[2:] for line in facts.splitlines()]


class SelectionTests(unittest.TestCase):
    def test_relevant_line_selected_and_irrelevant_skipped(self):
        lines = bullet_lines(select_resume_facts(RESUME, JD, ANCHOR, LETTERS))
        self.assertIn(RELEVANT, lines)
        self.assertNotIn(IRRELEVANT, lines)

    def test_contact_lines_and_name_header_dropped(self):
        facts = select_resume_facts(RESUME, JD + " alexandra whitfield references portfolio", ANCHOR, LETTERS)
        for dropped in ("@example.com", "+49", "https://", "(555)", "Doe-Whitfield"):
            self.assertNotIn(dropped, facts)

    def test_year_ranges_are_not_mistaken_for_phone_numbers(self):
        line = "Senior backend engineer 2019-2023 building FastAPI scheduling services"
        self.assertIn(line, bullet_lines(select_resume_facts(line, JD, ANCHOR, [])))

    def test_line_already_in_letters_is_skipped(self):
        covered = [("old.txt", f"In my last role I {RELEVANT.lower()}, which taught me a lot.")]
        self.assertNotIn(RELEVANT, bullet_lines(select_resume_facts(RESUME, JD, ANCHOR, covered)))

    def test_number_bonus_breaks_ties_and_output_keeps_resume_order(self):
        plain = "Maintained FastAPI scheduling services for the clinic network team"
        numbered = "Maintained FastAPI scheduling services for 12 clinic network teams"
        text = f"{plain}\n{numbered}"
        only_one = select_resume_facts(text, JD, ANCHOR, [], max_chars=len(numbered) + 2)
        self.assertEqual(bullet_lines(only_one), [numbered])
        both = bullet_lines(select_resume_facts(text, JD, ANCHOR, []))
        self.assertEqual(both, [plain, numbered])  # original resume order

    def test_numbers_are_passed_through_unchanged(self):
        line = "Cut FastAPI response time to 120 ms, a 35% improvement for the scheduling team"
        self.assertEqual(bullet_lines(select_resume_facts(line, JD, ANCHOR, [])), [line])

    def test_caps_total_chars_lines_and_line_length(self):
        many = "\n".join(f"Built FastAPI scheduling service number {i} on Kubernetes with PostgreSQL storage" for i in range(30))
        facts = select_resume_facts(many, JD, ANCHOR, [])
        self.assertLessEqual(len(facts.splitlines()), resume_facts.MAX_LINES)
        self.assertLessEqual(len(facts), resume_facts.MAX_TOTAL_CHARS)
        long_line = "Built FastAPI scheduling services " + " ".join(["on Kubernetes with PostgreSQL"] * 20)
        capped = bullet_lines(select_resume_facts(long_line, JD, ANCHOR, []))[0]
        self.assertLessEqual(len(capped), resume_facts.MAX_LINE_CHARS)
        self.assertTrue(long_line.startswith(capped))  # cut at a word boundary, nothing rewritten

    def test_total_cap_with_long_lines(self):
        long_lines = "\n".join(f"Built FastAPI scheduling service {i} " + "on Kubernetes with PostgreSQL storage " * 6 for i in range(12))
        self.assertLessEqual(len(select_resume_facts(long_lines, JD, ANCHOR, [])), resume_facts.MAX_TOTAL_CHARS)

    def test_empty_or_no_match_returns_empty(self):
        self.assertEqual(select_resume_facts("", JD, ANCHOR, LETTERS), "")
        self.assertEqual(select_resume_facts(None, JD, ANCHOR, LETTERS), "")
        self.assertEqual(select_resume_facts(IRRELEVANT, JD, ANCHOR, LETTERS), "")

    def test_module_is_pure(self):
        source = Path(resume_facts.__file__).read_text(encoding="utf-8")
        imports = {line.split()[1] for line in source.splitlines() if line.startswith(("import ", "from "))}
        self.assertEqual(imports, {"__future__", "re", "typing"})
        self.assertNotIn("open(", source)


class SectionTests(unittest.TestCase):
    def test_section_text(self):
        self.assertEqual(format_resume_facts_section(""), "")
        self.assertEqual(format_resume_facts_section(FACTS), f"{RESUME_FACTS_HEADER}\n{FACTS}\n{RESUME_FACTS_RULES}")
        self.assertIn("copy numbers exactly; do not add a baseline, a time frame or a result that is not written", RESUME_FACTS_RULES)


class PromptTests(unittest.TestCase):
    def letter(self, **kwargs):
        return generator.build_cover_letter_prompt("JD", [SAMPLE_ANCHOR], {"tone": "x"}, [("a", "b")], "https://fernwick-labs.example", {"hook": "h"}, **kwargs)

    def revision(self, **kwargs):
        return generator.build_cover_letter_revision_prompt("letter", "shorter", "JD", [SAMPLE_ANCHOR], {"tone": "x"}, [("a", "b")], "https://fernwick-labs.example", **kwargs)

    def test_letter_and_revision_prompts_unchanged_without_facts(self):
        for build in (self.letter, self.revision):
            with self.subTest(build=build.__name__):
                self.assertEqual(build(resume_facts=""), build())
                self.assertNotIn("RESUME FACTS", build())
        self.assertIn("Source URL: https://fernwick-labs.example\n\n\nCurrent job description:", self.letter())
        self.assertIn("Source URL: https://fernwick-labs.example\n\n\nCANDIDATE EVIDENCE:", self.revision())

    def test_section_appears_once_right_after_the_selected_angle(self):
        section = format_resume_facts_section(FACTS)
        letter, revision = self.letter(resume_facts=FACTS), self.revision(resume_facts=FACTS)
        self.assertEqual(letter.count(RESUME_FACTS_HEADER), 1)
        self.assertEqual(revision.count(RESUME_FACTS_HEADER), 1)
        self.assertIn(f"Source URL: https://fernwick-labs.example\n\n{section}\n\nCurrent job description:", letter)
        self.assertIn(f"Source URL: https://fernwick-labs.example\n\n{section}\n\nCANDIDATE EVIDENCE:", revision)

    def test_plan_prompt_has_no_resume_facts(self):
        self.assertNotIn("resume_facts", inspect.signature(generator.build_cover_letter_plan_prompt).parameters)
        plan = generator.build_cover_letter_plan_prompt("JD", [SAMPLE_ANCHOR], {"tone": "x"}, [("a", "b")], "https://fernwick-labs.example")
        self.assertNotIn("RESUME FACTS", plan)


class AppFlowTests(unittest.TestCase):
    USER = {"id": "00000000-0000-0000-0000-0000000000e5", "name": "jane.doe", "email": "jane.doe@example.com"}

    def setUp(self):
        self.client = TestClient(app.app)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        anchor = dict(SAMPLE_ANCHOR, **ANCHOR)
        job = {"id": "job-e5", "user_id": self.USER["id"], "job_description": JD, "company_url": "https://fernwick-labs.example",
               "anchors": [anchor], "selected_anchor": anchor}
        for name, value in {
            "app_user_for_request": self.USER,
            "count_recent_ai_actions": 0,
            "get_job_application": job,
            "get_style_profile": {"profile": {"tone": "x"}},
            "get_candidate_profile": {"profile": {}},
            "get_cover_letters_for_user": [{"filename": "l.txt", "content": LETTERS[0][1]}],
            "update_job_application_anchor": {},
            "save_generated_cover_letter": {"id": "new"},
        }.items():
            self.stack.enter_context(patch.object(app, name, return_value=value))
        self.resumes = self.stack.enter_context(patch.object(app, "get_resumes_for_user", return_value=[{"id": "r1", "extracted_text": RESUME}]))

    def test_first_draft_receives_relevant_resume_facts(self):
        self.stack.enter_context(patch.object(app, "get_generated_cover_letters_for_job", return_value=[]))
        generate = self.stack.enter_context(patch.object(app, "generate_cover_letter", return_value="Dear Hiring Manager,\n\nLetter."))
        self.client.post("/company/angles", data={"selected_anchor": "0", "job_application_id": "job-e5"})
        facts = generate.call_args.kwargs["resume_facts"]
        self.assertIn(RELEVANT, facts)
        self.assertNotIn(IRRELEVANT, facts)
        self.assertEqual(generate.call_args.kwargs["candidate_name"], "Jane Alexandra Doe-Whitfield")

    def test_revision_receives_resume_facts_with_a_single_resume_query(self):
        chain = [{"id": "r0", "revision_number": 0, "content": "Dear Hiring Manager,\n\nDraft.", "feedback": None, "is_final": False}]
        self.stack.enter_context(patch.object(app, "get_generated_cover_letters_for_job", return_value=chain))
        revise = self.stack.enter_context(patch.object(app, "generate_cover_letter_revision", return_value="Revised."))
        self.client.post("/cover-letter/revise", data={"job_application_id": "job-e5", "feedback": "Shorter"})
        self.assertIn(RELEVANT, revise.call_args.kwargs["resume_facts"])
        self.assertEqual(revise.call_args.kwargs["candidate_name"], "Jane Alexandra Doe-Whitfield")
        self.assertEqual(self.resumes.call_count, 1)

    def test_selection_failure_still_produces_a_letter(self):
        self.stack.enter_context(patch.object(app, "get_generated_cover_letters_for_job", return_value=[]))
        self.stack.enter_context(patch.object(app, "select_resume_facts", side_effect=RuntimeError("boom")))
        generate = self.stack.enter_context(patch.object(app, "generate_cover_letter", return_value="Dear Hiring Manager,\n\nLetter body."))
        response = self.client.post("/company/angles", data={"selected_anchor": "0", "job_application_id": "job-e5"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(generate.call_args.kwargs["resume_facts"], "")
        self.assertIn("Letter body.", response.text)


if __name__ == "__main__":
    unittest.main()
