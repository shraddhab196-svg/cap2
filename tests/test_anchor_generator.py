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
             patch.object(anchor_generator, "get_groq_client", return_value=(client, "model")):
            payload = anchor_generator.generate_anchors("https://example.com", "JD", "research", [("letter", "text")])

        self.assertEqual(client.chat.completions.create.call_args.kwargs["max_tokens"], 950)
        self.assertLess(client.chat.completions.create.call_args.kwargs["max_tokens"], 1000)
        self.assertEqual(len(payload["anchors"]), 3)


if __name__ == "__main__":
    unittest.main()
