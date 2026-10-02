import unittest

from src.cover_letter_generator import is_effectively_unchanged

OPENING = "BCG X needs AI systems that survive real operations; my thesis work built that evaluation discipline."
MIDDLE = "At Fraunhofer I built a reinforcement-learning pipeline that reached 88-96% success across test scenarios."
CLOSING = "I would welcome a conversation about how this experience could support your team."
CURRENT = f"Dear Hiring Manager,\n\n{OPENING}\n\n{MIDDLE}\n\n{CLOSING}\n\nSincerely,\nResham Joshi"


class IsEffectivelyUnchangedTests(unittest.TestCase):
    def test_identical_letter_is_rejected(self):
        self.assertTrue(is_effectively_unchanged(CURRENT, CURRENT))

    def test_whitespace_and_case_only_differences_are_still_rejected(self):
        self.assertTrue(is_effectively_unchanged(CURRENT, CURRENT.upper().replace(" ", "  ")))

    def test_only_opening_changed_is_accepted(self):
        self.assertFalse(is_effectively_unchanged(CURRENT, CURRENT.replace(OPENING, "A rewritten, more specific opening.")))

    def test_only_middle_changed_is_accepted(self):
        self.assertFalse(is_effectively_unchanged(CURRENT, CURRENT.replace(MIDDLE, "A rewritten evidence paragraph.")))

    def test_only_closing_changed_is_accepted(self):
        self.assertFalse(is_effectively_unchanged(CURRENT, CURRENT.replace(CLOSING, "A more confident closing.")))

    def test_middle_and_closing_changed_with_identical_opening_is_accepted(self):
        revised = CURRENT.replace(MIDDLE, "A rewritten evidence paragraph.").replace(CLOSING, "A more confident closing.")
        self.assertFalse(is_effectively_unchanged(CURRENT, revised))


if __name__ == "__main__":
    unittest.main()
