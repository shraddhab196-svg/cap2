import importlib.util
import os
import unittest
from pathlib import Path

from dotenv import load_dotenv


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "cover_letter_evaluator.py"
load_dotenv(MODULE_PATH.parents[1] / ".env")


class CoverLetterEvaluatorTests(unittest.TestCase):
    # Calls the live Groq API.
    @unittest.skipUnless(os.getenv("GROQ_API_KEY"), "GROQ_API_KEY not set")
    def test_module_exists_and_exports_evaluation_function(self):
        self.assertTrue(MODULE_PATH.exists(), "Expected cover_letter_evaluator.py to exist")

        spec = importlib.util.spec_from_file_location("cover_letter_evaluator", MODULE_PATH)
        self.assertIsNotNone(spec, "Could not load evaluator module from src")

        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        self.assertTrue(hasattr(module, "evaluate_cover_letter"), "Evaluator should expose evaluate_cover_letter")

        result = module.evaluate_cover_letter(
            letter_text="I built a production system for manufacturing anomaly detection using Python and Docker.",
            job_description="We need a scientist with Python and ML engineering experience.",
            selected_anchors=[{"title": "Production ML"}],
            style_profile={"tone": "Direct and evidence-based"},
            candidate_evidence=["I built a Python ML production pipeline in manufacturing."],
            company_url="https://example.com",
        )

        self.assertIn("overall_status", result)
        self.assertIn("scores", result)
        self.assertIn("issues", result)
        self.assertIn("unsupported_claims", result)
        self.assertIn("revision_suggestions", result)


if __name__ == "__main__":
    unittest.main()
