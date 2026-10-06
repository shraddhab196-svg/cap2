"""Short "Check before sending" notes for the letter page, built on letter_checks (plain code, no model calls).

Computed when the page is shown, never stored. Callers must not log the notes: they quote words from the letter.
"""
from __future__ import annotations

import re

from src.letter_checks import fact_issues, style_issues
from src.writing_framework import MAX_WORDS, count_words

MAX_UNSUPPORTED_SHOWN = 8
QUOTED = re.compile(r'"([^"]+)"')
PARAGRAPHS = re.compile(r"has (\d+) body paragraphs")


def _quoted(issues: list[str]) -> list[str]:
    return [match.group(1) for match in (QUOTED.search(issue) for issue in issues) if match]


def send_check_items(letter: str, source_texts: list[str] | None) -> list[str]:
    """User-facing notes for the letter; [] when nothing needs a look. Unsupported facts are checked only with sources."""
    if not str(letter or "").strip():
        return []
    items = []

    if source_texts:
        unsupported = _quoted(fact_issues(letter, source_texts))[:MAX_UNSUPPORTED_SHOWN]
        if unsupported:
            items.append(f"Not found in your resume, past letters or the job description: {', '.join(unsupported)}. Make sure these are correct.")

    issues = style_issues(letter)
    if any("no concrete number" in issue for issue in issues):
        items.append("No concrete number or result. Consider adding one.")
    phrases = _quoted([issue for issue in issues if "stock phrase" in issue])
    if phrases:
        items.append(f"Stock phrases: {', '.join(f'“{phrase}”' for phrase in phrases)}. Consider saying it in your own words.")
    counts = [int(match.group(1)) for match in (PARAGRAPHS.search(issue) for issue in issues) if match]
    if counts:
        items.append(f"{counts[0]} body paragraph{'' if counts[0] == 1 else 's'}. Three usually reads best.")

    words = count_words(letter)
    if words > MAX_WORDS:
        items.append(f"{words} words. Keep it to {MAX_WORDS} or fewer.")
    return items
