import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src import anchor_generator


def anchor(index):
    return {
        "title": f"Anchor {index}",
        "company_evidence": "fact",
        "job_connection": "need",
        "candidate_evidence": "evidence",
        "anchor": "connection",
        "source_url": "https://example.com",
    }


class AnchorGeneratorRequestTests(unittest.TestCase):
    def test_anchor_request_output_limit_is_below_groq_otpm_limit(self):
        client = MagicMock()
        content = json.dumps({"anchors": [anchor(i) for i in range(1, 4)]})
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

        with patch.object(anchor_generator, "load_environment"), \
             patch.object(anchor_generator, "get_llm_client", return_value=(client, "model")):
            payload = anchor_generator.generate_anchors("https://example.com", "JD", "research", [("letter", "text")])

        self.assertEqual(client.chat.completions.create.call_args.kwargs["max_tokens"], 950)
        self.assertLess(client.chat.completions.create.call_args.kwargs["max_tokens"], 1000)
        self.assertEqual(len(payload["anchors"]), 3)

    def test_prompt_asks_for_company_evidence_copied_from_research(self):
        prompt = anchor_generator.build_anchor_prompt("https://example.com", "JD", "### Source: https://example.com\nResearch text.", [("letter", "text")])
        self.assertIn("company_evidence must use wording taken directly from the company research", prompt)
        self.assertIn("copy a complete sentence from it", prompt)
        self.assertIn("instead of paraphrasing", prompt)
        self.assertIn("not a requirement from the job description or evidence about the candidate", prompt)


def run(anchors):
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"anchors": anchors})))])
    with patch.object(anchor_generator, "load_environment"), patch.object(anchor_generator, "get_llm_client", return_value=(client, "model")):
        return anchor_generator.generate_anchors("https://example.com", "JD", "research", [("letter", "text")])["anchors"]


class FewAnglesTests(unittest.TestCase):
    """Users always reach the angles page: fewer than 3 angles, or thin ones, are fine."""

    def test_one_or_two_angles_are_kept(self):
        self.assertEqual(len(run([anchor(1)])), 1)
        self.assertEqual(len(run([anchor(1), anchor(2)])), 2)

    def test_angles_missing_evidence_are_kept_and_filled_in(self):
        thin = {"title": "Fit", "anchor": "Connect your pipeline work to their data needs."}  # no evidence fields at all
        kept = run([thin])
        self.assertEqual(kept[0]["title"], "Fit")
        self.assertEqual(kept[0]["company_evidence"], "")
        self.assertEqual(kept[0]["source_url"], "https://example.com")

    def test_no_usable_angle_falls_back_to_a_general_one(self):
        for anchors in ([], [{"title": "", "anchor": ""}], ["not an object"]):
            with self.subTest(anchors=anchors):
                kept = run(anchors)
                self.assertEqual(len(kept), 1)
                self.assertEqual(kept[0]["title"], anchor_generator.FALLBACK_ANCHOR["title"])
                self.assertEqual(kept[0]["source_url"], "https://example.com")


if __name__ == "__main__":
    unittest.main()
