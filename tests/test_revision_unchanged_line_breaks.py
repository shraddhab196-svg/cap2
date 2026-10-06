"""Regression: the unchanged check compares text, not paragraph layout (letters with single newlines were always "unchanged")."""
import unittest

from src.cover_letter_generator import is_effectively_unchanged

OPENING = "Fernwick Labs needs rota tools that nurses trust; I built a planner that cut shift swaps by 30%."
MIDDLE = "I wrote the scheduling rules and tested them with the clinic teams who used them every week."
CLOSING = "I would welcome a conversation about how this work could help your scheduling product."
NEW_OPENING = "Fair night shifts are the problem this role names first, and they are the problem I know best."


def letter(opening, sep):
    return sep.join(["Dear Hiring Manager,", opening, MIDDLE, CLOSING, "Sincerely,\nJane Doe"])


BLANK_LINES = letter(OPENING, "\n\n")
SINGLE_NEWLINES = letter(OPENING, "\n")


class UnchangedComparesTextTests(unittest.TestCase):
    def test_identical_letters_are_unchanged(self):
        self.assertTrue(is_effectively_unchanged(BLANK_LINES, BLANK_LINES))
        self.assertTrue(is_effectively_unchanged(SINGLE_NEWLINES, SINGLE_NEWLINES))

    def test_whitespace_and_case_only_differences_are_unchanged(self):
        self.assertTrue(is_effectively_unchanged(BLANK_LINES, BLANK_LINES.upper().replace(" ", "  ")))
        self.assertTrue(is_effectively_unchanged(BLANK_LINES, SINGLE_NEWLINES))  # only the line breaks differ

    def test_empty_input_is_unchanged(self):
        self.assertTrue(is_effectively_unchanged("", BLANK_LINES))
        self.assertTrue(is_effectively_unchanged(BLANK_LINES, ""))

    def test_changed_letter_with_blank_lines_is_changed(self):
        self.assertFalse(is_effectively_unchanged(BLANK_LINES, letter(NEW_OPENING, "\n\n")))

    def test_changed_letter_with_single_newlines_is_changed(self):
        self.assertFalse(is_effectively_unchanged(BLANK_LINES, letter(NEW_OPENING, "\n")))

    def test_single_newline_current_letter_with_changed_revision_is_changed(self):
        self.assertFalse(is_effectively_unchanged(SINGLE_NEWLINES, letter(NEW_OPENING, "\n\n")))
        self.assertFalse(is_effectively_unchanged(SINGLE_NEWLINES, letter(NEW_OPENING, "\n")))

    def test_literal_backslash_n_text_with_changed_content_is_changed(self):
        self.assertFalse(is_effectively_unchanged(BLANK_LINES, letter(NEW_OPENING, "\\n\\n")))


if __name__ == "__main__":
    unittest.main()
