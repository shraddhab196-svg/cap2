import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from src import cover_letter_generator as generator
from src.letter_checks import STOCK_PHRASES, build_retry_note, fact_issues, style_issues

COMPANY_URL = "https://www.fernwick-labs.example/"
SOURCES = [
    "Jane Doe. Cut scheduling errors by 40% across 12 clinics using Python services.",
    "Fernwick Labs is hiring a backend engineer to build fair rota tools for clinics.",
    COMPANY_URL,
]
FILLER = "This work taught me to treat careful planning as part of the engineering itself. "


def paragraph(lead, sentences=5):
    return lead + " " + FILLER * sentences


def letter(*paragraphs, sign_off="Sincerely,\nJane Doe"):
    return "Dear Hiring Manager,\n\n" + "\n\n".join(p.strip() for p in paragraphs) + (f"\n\n{sign_off}" if sign_off else "")


GOOD = letter(
    paragraph("Clinics lose hours to rota mistakes, and Fernwick Labs builds the tools that fix them."),
    paragraph("In my last role I cut scheduling errors by 40% across 12 clinics using Python services."),
    paragraph("That is the problem this role exists to solve, and I would welcome a conversation about it."),
)
INVENTED = GOOD.replace("by 40% across", "by 57% across")


def number_issue(number):
    return f'The letter uses the number "{number}" which is not in the candidate\'s documents or the job description.'


def name_issue(name):
    return f'The letter mentions "{name}" which is not in the candidate\'s documents or the job description.'


class FactIssuesTests(unittest.TestCase):
    def test_number_in_sources_passes_and_unknown_number_flagged(self):
        self.assertEqual(fact_issues("We grew by 40% last year.", SOURCES), [])
        self.assertEqual(fact_issues("We grew by 57% last year.", SOURCES), [number_issue("57%")])

    def test_percent_and_thousands_normalisation(self):
        self.assertEqual(fact_issues("It reached 70 % of users.", ["reached 70% of users"]), [])
        self.assertEqual(fact_issues("It reached 70% of users.", ["reached 70 % of users"]), [])
        self.assertEqual(fact_issues("We served 1,000 clinics.", ["served 1000 clinics"]), [])
        self.assertEqual(fact_issues("We served 1000 clinics.", ["served 1,000 clinics"]), [])

    def test_single_digit_whole_numbers_ignored_other_shapes_checked(self):
        self.assertEqual(fact_issues("We had 3 teams and 7 tools.", []), [])
        self.assertEqual(fact_issues("A 2.5x speedup for 10k users at 4% cost.", []), [number_issue("2.5x"), number_issue("10k"), number_issue("4%")])

    def test_unknown_name_flagged_known_name_passes(self):
        self.assertEqual(fact_issues("I worked closely with Brightvale on rotas.", SOURCES), [name_issue("Brightvale")])
        self.assertEqual(fact_issues("I admire how fernwick works, and Fernwick ships fast.", [s.lower() for s in SOURCES]), [])

    def test_sentence_start_greeting_and_sign_off_ignored(self):
        text = "Dear Hiring Manager,\n\nBrightvale taught me rotas. Yesterday was busy.\n\nSincerely,\nJane Doe"
        self.assertEqual(fact_issues(text, []), [])

    def test_allow_list_words_ignored(self):
        self.assertEqual(fact_issues("I moved to Germany in March and speak German and English.", []), [])

    def test_at_most_five_names(self):
        text = "I met Alvarin, Brosk, Cendra, Dovell, Elmira, Fausto and Gerrit at work."
        issues = fact_issues(text, [])
        self.assertEqual(len(issues), 5)
        self.assertEqual(issues[0], name_issue("Alvarin"))

    def test_odd_input_never_raises(self):
        for args in ((None, None), (123, [None, 5]), ("", []), ("x" * 50000, ["y"]), (GOOD, None)):
            with self.subTest(args=str(args)[:40]):
                self.assertIsInstance(fact_issues(*args), list)


class StyleIssuesTests(unittest.TestCase):
    def test_good_letter_has_no_issues(self):
        self.assertEqual(style_issues(GOOD), [])

    def test_no_digit(self):
        self.assertIn("The letter has no concrete number or result.", style_issues(GOOD.replace("40% across 12", "many across several")))

    def test_two_and_four_paragraphs(self):
        two = letter(paragraph("One fine paragraph with 12 clinics."), paragraph("Two fine paragraphs."))
        four = letter(*(paragraph(f"Paragraph about 12 clinics, part {i}.") for i in range(4)))
        self.assertIn("The letter has 2 body paragraphs; use 3.", style_issues(two))
        self.assertIn("The letter has 4 body paragraphs; use 3.", style_issues(four))

    def test_each_stock_phrase(self):
        for phrase in STOCK_PHRASES:
            with self.subTest(phrase=phrase):
                text = GOOD.replace("That is the problem", f"Honestly, {phrase} the problem")
                self.assertIn(f'The letter uses a stock phrase: "{phrase}".', style_issues(text))

    def test_missing_sign_off(self):
        self.assertIn("The letter has no sign-off.", style_issues(letter(*GOOD.split("\n\n")[1:4], sign_off="")))
        self.assertNotIn("The letter has no sign-off.", style_issues(GOOD.replace("Sincerely,", "Mit freundlichen Grüßen,")))

    def test_short_letter_is_not_an_issue(self):
        short = letter("I cut errors by 40% for clinics.", "Fernwick builds rota tools.", "I would welcome a talk.")
        self.assertEqual(style_issues(short), [])


class RetryNoteTests(unittest.TestCase):
    def test_structure_and_limits(self):
        issues = [f"Issue number {i} is here." for i in range(10)]
        note = build_retry_note(issues)
        self.assertTrue(note.startswith("IMPORTANT: fix only these problems and keep everything else: Issue number 0 is here; "))
        self.assertTrue(note.endswith(". Do not add any fact, number or name that is not in the supplied documents."))
        self.assertIn("Issue number 5", note)
        self.assertNotIn("Issue number 6", note)
        self.assertLessEqual(len(build_retry_note(["word " * 200] * 6)), 600)
        self.assertLessEqual(len(build_retry_note(["x" * 900])), 600)


class GenerationLoopTests(unittest.TestCase):
    ANCHORS = [{"title": "t", "company_evidence": "e", "job_connection": "j", "candidate_evidence": "c", "anchor": "a"}]

    def run_generate(self, letters, source_texts=SOURCES):
        responses = [json.dumps({"cover_letter": text}) for text in letters]
        with patch.object(generator, "load_environment"), \
             patch.object(generator, "get_llm_client", return_value=(None, "model")), \
             patch.object(generator, "generate_cover_letter_plan", return_value={"hook": "h"}), \
             patch.object(generator, "semantic_anchor_validation", return_value={"reflected": True}), \
             patch.object(generator, "call_groq_json", side_effect=responses) as call:
            try:
                result = generator.generate_cover_letter("JD", self.ANCHORS, {}, [("a", "b")], COMPANY_URL, source_texts=source_texts)
            except ValueError as exc:
                result = exc
        return result, [c.args[3] for c in call.call_args_list]

    def test_clean_letter_one_call(self):
        result, prompts = self.run_generate([GOOD])
        self.assertEqual(result, GOOD)
        self.assertEqual(len(prompts), 1)

    def test_invented_number_retries_once_with_note(self):
        result, prompts = self.run_generate([INVENTED, GOOD])
        self.assertEqual(result, GOOD)
        self.assertEqual(len(prompts), 2)
        self.assertIn("IMPORTANT: fix only these problems and keep everything else:", prompts[1])
        self.assertIn('"57%"', prompts[1])

    def test_issues_remaining_after_retry_still_return_letter(self):
        result, prompts = self.run_generate([INVENTED, INVENTED])
        self.assertEqual(result, INVENTED)
        self.assertEqual(len(prompts), 2)

    def test_word_limit_behaviour_unchanged(self):
        too_long = letter(*(paragraph(f"Fernwick Labs and 12 clinics, part {i}.", sentences=8) for i in range(3)))
        self.assertGreater(len(too_long.split()), 300)
        result, prompts = self.run_generate([too_long, GOOD])
        self.assertEqual(result, GOOD)
        self.assertIn("IMPORTANT: The previous draft had", prompts[1])
        self.assertNotIn("fix only these problems", prompts[1])
        result, prompts = self.run_generate([too_long] * 3)
        self.assertIsInstance(result, ValueError)
        self.assertEqual(len(prompts), 3)

    def test_no_source_texts_means_no_new_checks(self):
        result, prompts = self.run_generate([INVENTED], source_texts=None)
        self.assertEqual(result, INVENTED)
        self.assertEqual(len(prompts), 1)


class PromptLabelTests(unittest.TestCase):
    LETTERS = [("Brightvale_CoverLetter.pdf", "First letter text."), ("Old-Employer-Letter.txt", "Second letter text."), ("Third.pdf", "Third letter text.")]
    ANCHOR = {"title": "t", "company_evidence": "e", "job_connection": "j", "candidate_evidence": "c", "anchor": "a"}

    def test_previous_letters_labelled_by_position_without_filenames(self):
        prompts = {
            "plan": generator.build_cover_letter_plan_prompt("JD", [self.ANCHOR], {}, self.LETTERS, COMPANY_URL),
            "letter": generator.build_cover_letter_prompt("JD", [self.ANCHOR], {}, self.LETTERS, COMPANY_URL, {"hook": "h"}),
            "revision": generator.build_cover_letter_revision_prompt("current", "shorter", "JD", [self.ANCHOR], {}, self.LETTERS, COMPANY_URL),
        }
        for name, prompt in prompts.items():
            with self.subTest(prompt=name):
                self.assertIn("--- Letter 1 ---\nFirst letter text.", prompt)
                self.assertIn("--- Letter 2 ---\nSecond letter text.", prompt)
                for filename, _ in self.LETTERS:
                    self.assertNotIn(filename, prompt)
        self.assertIn("--- Letter 3 ---\nThird letter text.", prompts["letter"])  # letter prompt keeps 3, plan/revision 2


class AppSourceTextsTests(unittest.TestCase):
    USER = {"id": "00000000-0000-0000-0000-0000000000f6", "name": "jane.doe", "email": "jane.doe@example.com"}
    RESUME = "Jane Doe\nCut scheduling errors by 40% across 12 clinics using Python services."
    FULL_LETTER = "Dear Hiring Manager, " + "I planned rotas for clinics. " * 60

    def setUp(self):
        self.client = TestClient(app.app)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        anchor = {"title": "Fair rotas", "company_evidence": "Fernwick builds rota tools.", "job_connection": "j", "candidate_evidence": "c", "anchor": "a", "source_url": COMPANY_URL}
        job = {"id": "job-f6", "user_id": self.USER["id"], "job_description": "Backend engineer at Fernwick Labs.", "company_url": COMPANY_URL, "anchors": [anchor]}
        for name, value in {
            "app_user_for_request": self.USER,
            "count_recent_ai_actions": 0,
            "get_job_application": job,
            "get_generated_cover_letters_for_job": [],
            "get_style_profile": {"profile": {"tone": "x"}},
            "get_candidate_profile": {"profile": {}},
            "get_cover_letters_for_user": [{"filename": "Brightvale_CoverLetter.pdf", "content": self.FULL_LETTER}],
            "get_resumes_for_user": [{"id": "r1", "extracted_text": self.RESUME}],
            "update_job_application_anchor": {},
            "save_generated_cover_letter": {"id": "new"},
        }.items():
            self.stack.enter_context(patch.object(app, name, return_value=value))
        self.generate = self.stack.enter_context(patch.object(app, "generate_cover_letter", return_value="Dear Hiring Manager,\n\nLetter body."))

    def post(self):
        return self.client.post("/company/angles", data={"selected_anchor": "0", "job_application_id": "job-f6"})

    def test_source_texts_passed_with_full_documents(self):
        self.post()
        sources = self.generate.call_args.kwargs["source_texts"]
        self.assertIn(self.RESUME, sources)
        self.assertIn(self.FULL_LETTER, sources)  # full content, not the prompt snippet
        self.assertIn("Backend engineer at Fernwick Labs.", sources)
        self.assertIn("Fernwick builds rota tools.", sources)
        self.assertIn(COMPANY_URL, sources)
        self.assertIn("Jane Doe", sources)

    def test_source_building_failure_still_produces_letter(self):
        self.stack.enter_context(patch.object(app, "build_source_texts", side_effect=RuntimeError("boom")))
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.generate.call_args.kwargs["source_texts"])
        self.assertIn("Letter body.", response.text)


if __name__ == "__main__":
    unittest.main()
