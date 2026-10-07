"""A short draft is retried instead of shown as an error; German letters have a lower word floor."""
import json
import unittest
from unittest.mock import patch

from src import cover_letter_generator as generator

ANCHOR = {"title": "Alerts", "company_evidence": "e", "job_connection": "j", "candidate_evidence": "c", "anchor": "a"}


def letter(words: int, german: bool = False) -> str:
    greeting, closing = ("Sehr geehrte Damen und Herren,", "Mit freundlichen Grüßen") if german else ("Dear Hiring Manager,", "Sincerely,")
    filler = ("die Pipeline und das Team " if german else "the pipeline and the team ") * (words // 5)
    return f"{greeting}\n\n{filler.strip()}\n\n{closing}\nJane Doe"


class ShortLetterTests(unittest.TestCase):
    def generate(self, drafts, language="en"):
        responses = iter(json.dumps({"cover_letter": draft}) for draft in drafts)
        with patch.object(generator, "get_llm_client", return_value=(None, "m")), \
             patch.object(generator, "generate_cover_letter_plan", return_value={"hook": "h"}), \
             patch.object(generator, "semantic_anchor_validation", return_value={"reflected": True}), \
             patch.object(generator, "call_groq_json", side_effect=lambda *a, **k: next(responses)) as calls:
            result = generator.generate_cover_letter("JD", [ANCHOR], {}, [("a", "b")], "https://x.example", candidate_name="Jane Doe", language=language)
        return result, calls

    def test_short_draft_is_retried_with_a_length_note(self):
        with self.assertLogs(generator.logger, "WARNING"):
            result, calls = self.generate([letter(120), letter(240)])
        self.assertEqual(calls.call_count, 2)
        self.assertIn("only", calls.call_args_list[1].args[3])
        self.assertGreater(len(result.split()), 200)

    def test_german_letter_of_180_words_is_accepted(self):
        result, calls = self.generate([letter(180, german=True)], language="de")
        self.assertEqual(calls.call_count, 1)
        self.assertTrue(result.startswith("Sehr geehrte"))

    def test_last_attempt_accepts_a_slightly_short_letter_instead_of_an_error(self):
        with self.assertLogs(generator.logger, "WARNING"):
            result, calls = self.generate([letter(150), letter(150), letter(150)])
        self.assertEqual(calls.call_count, 3)
        self.assertIn("Jane Doe", result)

    def test_far_too_short_still_fails_at_the_end(self):
        with self.assertLogs(generator.logger, "WARNING"), self.assertRaisesRegex(ValueError, "too short"):
            self.generate([letter(60), letter(60), letter(60)])


if __name__ == "__main__":
    unittest.main()
