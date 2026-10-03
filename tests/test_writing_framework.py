import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src import cover_letter_evaluator as evaluator
from src import cover_letter_generator as generator
from src import writing_framework

ANCHORS = [{"title": "Production ML", "company_evidence": "c", "job_connection": "j", "candidate_evidence": "e", "anchor": "a"}]
FRAMEWORK_HEADER = "COVER LETTER WRITING FRAMEWORK"


def make_letter(paragraph_sizes, word="delivered", opening=""):
    """Greeting (3 words) + body paragraphs of the given sizes + sign-off and name (3 words)."""
    paragraphs = []
    for index, size in enumerate(paragraph_sizes):
        lead = (opening.split() if index == 0 and opening else []) + ["Resham"]
        paragraphs.append(" ".join(lead + [word] * (size - len(lead))))
    return "Dear Hiring Manager,\n\n" + "\n\n".join(paragraphs) + "\n\nSincerely,\nResham Joshi"


def letter_json(letter):
    return json.dumps({"cover_letter": letter})


class WordLimitTests(unittest.TestCase):
    def test_complete_letter_including_greeting_and_signoff_is_counted(self):
        exactly_300 = make_letter([98, 98, 98])
        over_by_greeting = make_letter([99, 98, 98])
        self.assertEqual(writing_framework.count_words(exactly_300), 300)
        self.assertIsNone(writing_framework.word_limit_problem(exactly_300))
        # Body alone is 295 words; only the greeting and sign-off push it to 301.
        self.assertEqual(writing_framework.count_words(over_by_greeting), 301)
        self.assertIn("301 words", writing_framework.word_limit_problem(over_by_greeting))

    def test_explicit_word_request_is_capped_at_maximum(self):
        self.assertEqual(writing_framework.cap_feedback_constraints({"words": 1500, "paragraphs": 3}), {"words": 300, "paragraphs": 3})
        self.assertEqual(writing_framework.cap_feedback_constraints({"words": 150, "paragraphs": None}), {"words": 150, "paragraphs": None})


class GenerationTests(unittest.TestCase):
    def test_generation_prompt_contains_framework_and_new_length(self):
        prompt = generator.build_cover_letter_prompt("JD", ANCHORS, {"tone": "direct"}, [("a", "b")], "https://example.com", {"hook": "h"})
        self.assertIn(FRAMEWORK_HEADER, prompt)
        self.assertIn(writing_framework.LENGTH_REQUIREMENT, prompt)
        self.assertNotIn("350-500", prompt)

    def test_generation_prompt_has_no_hard_coded_company(self):
        prompt = generator.build_cover_letter_prompt("JD", ANCHORS, {"tone": "direct"}, [("a", "b")], "https://acme.example", {"hook": "h"})
        self.assertNotIn("BCG", prompt)
        self.assertIn("https://acme.example", prompt)
        self.assertIn("specific to the actual company", prompt)

    def test_revision_prompt_contains_framework(self):
        prompt = generator.build_cover_letter_revision_prompt("letter", "shorter", "JD", ANCHORS, {"tone": "x"}, [("a", "b")], "https://example.com")
        self.assertIn(FRAMEWORK_HEADER, prompt)
        self.assertIn("NEVER the no-fabrication rule", prompt)

    def run_generation(self, responses):
        with patch.object(generator, "load_environment"), \
             patch.object(generator, "get_llm_client", return_value=(None, "model")), \
             patch.object(generator, "generate_cover_letter_plan", return_value={"hook": "h"}), \
             patch.object(generator, "semantic_anchor_validation", return_value={"reflected": True}), \
             patch.object(generator, "call_groq_json", side_effect=responses) as mock_call:
            try:
                return generator.generate_cover_letter("JD", ANCHORS, {"tone": "x"}, [("a", "b")], "https://example.com"), mock_call
            except ValueError as exc:
                return exc, mock_call

    def test_generation_retries_until_complete_letter_is_within_limit(self):
        too_long, good = make_letter([110, 110, 100]), make_letter([95, 95, 90])
        result, mock_call = self.run_generation([letter_json(too_long), letter_json(good)])
        self.assertEqual(result, good)
        self.assertEqual(mock_call.call_count, 2)
        self.assertIn(FRAMEWORK_HEADER, mock_call.call_args_list[0].args[3])
        self.assertIn("IMPORTANT: The previous draft had 326 words", mock_call.call_args_list[1].args[3])

    def test_generation_never_returns_letter_over_limit(self):
        too_long = make_letter([110, 110, 100])
        result, mock_call = self.run_generation([letter_json(too_long)] * 3)
        self.assertIsInstance(result, ValueError)
        self.assertEqual(str(result), writing_framework.GENERIC_FAILURE_MESSAGE)
        self.assertEqual(mock_call.call_count, 3)

    def test_revision_enforces_limit_and_caps_large_word_request(self):
        current = make_letter([150, 150, 100], word="original")
        revised = make_letter([95, 95, 90], word="revised")
        with patch.object(generator, "load_environment"), \
             patch.object(generator, "get_llm_client", return_value=(None, "model")), \
             patch.object(generator, "semantic_anchor_validation", return_value={"reflected": True}), \
             patch.object(generator, "call_groq_json", side_effect=[letter_json(make_letter([110, 110, 100], word="revised")), letter_json(revised)]) as mock_call:
            result = generator.generate_cover_letter_revision(current, "make it 1500 words", "JD", ANCHORS, {"tone": "x"}, [("a", "b")], "https://example.com")
        self.assertEqual(result, revised)
        self.assertEqual(mock_call.call_count, 2)
        self.assertIn("approximately 300 words", mock_call.call_args_list[0].args[3])
        self.assertIn("must be no more than 300 words", mock_call.call_args_list[1].args[3])


class EvaluationTests(unittest.TestCase):
    def test_evaluation_prompt_contains_framework(self):
        prompt = evaluator.build_evaluation_prompt("letter", "JD", ANCHORS, {"tone": "x"}, ["evidence"], "https://example.com")
        self.assertIn(FRAMEWORK_HEADER, prompt)
        self.assertIn('"framework_issues"', prompt)

    def test_deterministic_framework_issues(self):
        bad = make_letter([110, 110, 60, 40], opening="I am writing to apply for this role and")
        issues = writing_framework.deterministic_issues(bad)
        self.assertTrue(any("326 words" in issue for issue in issues))
        self.assertTrue(any("4 body paragraphs" in issue for issue in issues))
        self.assertTrue(any("generic formula" in issue for issue in issues))
        self.assertEqual(writing_framework.deterministic_issues(make_letter([95, 95, 90])), [])

    def test_evaluate_cover_letter_merges_framework_issues(self):
        llm_payload = {
            "overall_status": "PASS",
            "scores": {key: 9 for key in ["factual_grounding", "job_relevance", "company_specificity", "selected_angle_usage", "persuasiveness", "voice_fidelity", "human_likeness"]},
            "strengths": [], "issues": [], "unsupported_claims": [], "generic_or_ai_like_phrases": [], "revision_suggestions": [],
            "framework_issues": ["The opening does not name a real company need."],
        }
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(llm_payload)))])
        letter = make_letter([95, 95, 90])
        with patch.object(evaluator, "load_environment"), patch.object(evaluator, "get_llm_client", return_value=client):
            result = evaluator.evaluate_cover_letter(letter, "JD", ANCHORS, {"tone": "x"}, ["evidence"], "https://example.com")
        sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn(FRAMEWORK_HEADER, sent_prompt)
        self.assertEqual(result["framework_issues"], ["The opening does not name a real company need."])
        self.assertEqual(result["overall_status"], "NEEDS_REVISION")


class RemovabilityTests(unittest.TestCase):
    def test_disabling_framework_restores_previous_behaviour(self):
        with patch.object(writing_framework, "FRAMEWORK_ENABLED", False):
            prompt = generator.build_cover_letter_prompt("JD", ANCHORS, {"tone": "direct"}, [("a", "b")], "https://example.com", {"hook": "h"})
            self.assertNotIn(FRAMEWORK_HEADER, prompt)
            self.assertIn("Target approximately 350-500 words.", prompt)
            self.assertIn("The opening must be an evidence-backed hook", prompt)
            self.assertIn("Do not artificially over-polish", prompt)
            self.assertIsNone(writing_framework.word_limit_problem(make_letter([150, 150, 100])))
            self.assertEqual(writing_framework.deterministic_issues(make_letter([150, 150, 100, 50])), [])
            self.assertEqual(writing_framework.cap_feedback_constraints({"words": 1500, "paragraphs": None})["words"], 1500)


if __name__ == "__main__":
    unittest.main()
