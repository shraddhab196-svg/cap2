"""Change G: letter filenames (which can name companies) never reach the angle prompt."""
import unittest

from src.anchor_generator import build_anchor_prompt

LETTERS = [("FernwickLabs_CoverLetter.pdf", "First letter text."), ("Lowmere-Analytics-letter.txt", "Second letter text.")]


class AnchorPromptFilenameTests(unittest.TestCase):
    def test_filenames_are_replaced_by_positions(self):
        prompt = build_anchor_prompt("https://fernwick-labs.example", "JD", "### Source: https://fernwick-labs.example\nResearch.", LETTERS)
        self.assertNotIn("FernwickLabs_CoverLetter", prompt)
        self.assertNotIn("Lowmere-Analytics-letter", prompt)
        self.assertIn("--- LETTER 1 ---\nFirst letter text.", prompt)
        self.assertIn("--- LETTER 2 ---\nSecond letter text.", prompt)
        self.assertLess(prompt.index("--- LETTER 1 ---"), prompt.index("--- LETTER 2 ---"))


if __name__ == "__main__":
    unittest.main()
