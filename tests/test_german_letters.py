"""German cover letters: language choice, German prompt rules, revisions keep the language, checks."""
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from src import cover_letter_generator as generator
from src.language import detect_language
from src.letter_checks import fact_issues, style_issues

USER = {"id": "00000000-0000-0000-0000-0000000000d3", "name": "jane.doe", "email": "jane.doe@example.com"}
COMPANY = "https://www.fernwick-labs.example/"
GERMAN_JD = "Wir suchen eine Data Engineerin (m/w/d) für unser Plattform-Team. Ihre Aufgaben: Sie betreuen die Pipelines und die Alarme. Ihre Anforderungen: Erfahrung mit Kafka und Python."
ENGLISH_JD = "We are looking for a data engineer to own our event pipeline. You will work with the platform team on alerting."
ANCHOR = {"title": "Alerts", "company_evidence": "e", "job_connection": "j", "candidate_evidence": "c", "anchor": "a", "source_url": COMPANY}
GERMAN_LETTER = (Path(__file__).parent / "fixtures" / "sample_letter_de.txt").read_text(encoding="utf-8")


class DetectLanguageTests(unittest.TestCase):
    def test_german_english_and_empty(self):
        self.assertEqual(detect_language(GERMAN_JD), "de")
        self.assertEqual(detect_language(GERMAN_LETTER), "de")
        self.assertEqual(detect_language(ENGLISH_JD), "en")
        self.assertEqual(detect_language(""), "en")


class GermanPromptTests(unittest.TestCase):
    def letter_prompt(self, language, name="Jane Doe"):
        return generator.build_cover_letter_prompt(GERMAN_JD, [ANCHOR], {"tone": "x"}, [("a", "b")], COMPANY, {"hook": "h"}, candidate_name=name, language=language)

    def test_german_letters_get_german_rules_instead_of_the_english_greeting(self):
        prompt = self.letter_prompt("de")
        self.assertIn("LANGUAGE: GERMAN", prompt)
        self.assertIn("Sehr geehrte Damen und Herren", prompt)
        self.assertIn("Mit freundlichen Grüßen", prompt)
        self.assertNotIn("Start with: Dear Hiring Manager,", prompt)

    def test_english_prompt_is_unchanged(self):
        prompt = self.letter_prompt("en")
        self.assertIn("Start with: Dear Hiring Manager,", prompt)
        self.assertNotIn("LANGUAGE: GERMAN", prompt)

    def test_unknown_name_signs_off_in_german(self):
        self.assertIn("End with 'Mit freundlichen Grüßen'", self.letter_prompt("de", name=None))

    def test_revision_keeps_the_letters_language(self):
        seen = {}

        def capture(**kwargs):
            seen["language"] = kwargs["language"]
            raise RuntimeError("stop")

        with patch.object(generator, "get_llm_client", return_value=(None, "m")), \
             patch.object(generator, "build_cover_letter_revision_prompt", side_effect=capture):
            with self.assertRaises(RuntimeError):
                generator.generate_cover_letter_revision(GERMAN_LETTER, "kürzer bitte", GERMAN_JD, [ANCHOR], {}, [("a", "b")], COMPANY)
        self.assertEqual(seen["language"], "de")

    def test_german_sign_off_gets_the_account_name(self):
        signed = generator.apply_signature(GERMAN_LETTER, "Janey Doe")
        self.assertTrue(signed.rstrip().endswith("Mit freundlichen Grüßen\nJaney Doe"))


class GermanCheckTests(unittest.TestCase):
    def test_nouns_are_not_reported_as_invented_names(self):
        issues = fact_issues(GERMAN_LETTER, ["Kafka Python AWS Docker 30%"])
        self.assertEqual(issues, [])

    def test_unsupported_numbers_are_still_reported(self):
        self.assertTrue(any('"30%"' in issue for issue in fact_issues(GERMAN_LETTER, ["Kafka Python"])))

    def test_german_greeting_paragraphs_and_sign_off_are_recognised(self):
        issues = style_issues(GERMAN_LETTER)
        self.assertNotIn("The letter has no sign-off.", issues)
        self.assertEqual(generator.count_body_paragraphs(GERMAN_LETTER), 3)

    def test_german_stock_phrases_are_flagged(self):
        letter = "Sehr geehrte Damen und Herren,\n\nhiermit bewerbe ich mich als teamfähig und hochmotiviert.\n\nMit freundlichen Grüßen\nJane"
        flagged = " ".join(style_issues(letter))
        for phrase in ("hiermit bewerbe ich mich", "teamfähig", "hochmotiviert"):
            self.assertIn(phrase, flagged)


class LetterLanguageRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        anchors = [dict(ANCHOR, title=f"Angle {i}") for i in range(3)]
        for name, value in {
            "app_user_for_request": USER,
            "count_recent_ai_actions": 0,
            "get_cover_letters_for_user": [{"filename": "l.txt", "content": "My letter."}],
            "research_company": {"company_url": COMPANY, "company_research": "### Source: x\n" + "word " * 300, "pages": [COMPANY]},
            "generate_anchors": {"anchors": anchors},
        }.items():
            self.stack.enter_context(patch.object(app, name, return_value=value))
        self.save = self.stack.enter_context(patch.object(app, "save_job_application", return_value={"id": "job-d3"}))

    def language_for(self, jd, choice=None):
        data = {"job_description": jd, "company_url": COMPANY}
        if choice is not None:
            data["letter_language"] = choice
        self.assertEqual(self.client.post("/profile/job-input", data=data, follow_redirects=False).status_code, 303)
        return {anchor["letter_language"] for anchor in self.save.call_args.kwargs["anchors"]}

    def test_auto_follows_the_job_description(self):
        self.assertEqual(self.language_for(GERMAN_JD), {"de"})
        self.assertEqual(self.language_for(ENGLISH_JD, "auto"), {"en"})

    def test_explicit_choice_wins(self):
        self.assertEqual(self.language_for(ENGLISH_JD, "de"), {"de"})
        self.assertEqual(self.language_for(GERMAN_JD, "en"), {"en"})

    def test_unknown_values_fall_back_to_auto(self):
        self.assertEqual(self.language_for(GERMAN_JD, "fr"), {"de"})

    def test_choice_is_on_the_form(self):
        page = self.client.get("/profile/job-input").text
        self.assertIn('name="letter_language" value="de"', page)
        self.assertIn("Deutsch", page)


if __name__ == "__main__":
    unittest.main()
