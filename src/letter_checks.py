"""Plain-code checks on a generated letter: facts not found in the candidate's documents, and style rules.

Pure functions only (no I/O, no logging, no model calls). Messages name the offending number or word so the
model can fix it; callers must not log them.
"""
from __future__ import annotations

import re

try:
    from src.language import detect_language
except ImportError:  # running as a script from inside src/
    from language import detect_language

MAX_NAMES_REPORTED = 5
MAX_RETRY_ISSUES = 6
MAX_RETRY_NOTE_CHARS = 600
TARGET_BODY_PARAGRAPHS = 3

STOCK_PHRASES = (
    "i am writing to",
    "i am excited",
    "i am thrilled",
    "passionate about",
    "i am a passionate",
    "team player",
    "fast learner",
    "hard-working",
    "hardworking",
    "proven track record",
    "perfect fit",
    "perfect candidate",
    "leverage my",
    "synergy",
    "dynamic environment",
    "results-driven",
    "with my experience",
    "i believe i would be",
    # German
    "hiermit bewerbe ich mich",
    "mit großem interesse",
    "ich bin leidenschaftlich",
    "teamfähig",
    "hochmotiviert",
    "belastbar",
    "ideale kandidat",
    "perfekte kandidat",
)

SIGN_OFFS = (
    "sincerely", "best regards", "kind regards", "regards",
    "mit freundlichen grüßen", "mit besten grüßen", "freundliche grüße", "beste grüße", "viele grüße", "herzliche grüße",
)
GREETING = re.compile(r"^(dear|hello|hi|to whom|sehr geehrte|liebe|lieber|hallo|guten tag)\b", re.IGNORECASE)
NAME_ALLOW_LIST = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "dear", "hiring", "manager", "team", "sincerely", "regards", "best", "kind", "thank", "germany", "german", "english",
}
# Numbers that need support: percentages, decimals, 2+ digit numbers, and k/m/x suffixes. Lone digits are ignored.
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?:%|[kmx](?![a-z]))?")
WORD = re.compile(r"[^\W\d_][\w'’-]*")


def _normalize_numbers(text: str) -> str:
    text = " ".join(str(text or "").lower().split())
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)  # 1,000 -> 1000
    return re.sub(r"(\d)\s+%", r"\1%", text)  # 70 % -> 70%


def _checked_numbers(text: str) -> list[str]:
    numbers = []
    for token in NUMBER.findall(_normalize_numbers(text)):
        whole = token.rstrip("%kmx")
        if "%" in token or "." in token or token[-1] in "kmx" or len(whole) >= 2:
            numbers.append(token)
    return list(dict.fromkeys(numbers))


def _body_lines(letter: str) -> list[str]:
    """Letter lines without the greeting line and without the sign-off and anything after it."""
    lines = [line.strip() for line in str(letter or "").splitlines()]
    nonempty = [index for index, line in enumerate(lines) if line]
    if nonempty and GREETING.match(lines[nonempty[0]]):
        lines[nonempty[0]] = ""
    for index in reversed(nonempty):
        if lines[index].lower().startswith(SIGN_OFFS):
            lines = lines[:index]
            break
    return lines


def _capitalized_names(letter: str) -> list[str]:
    names = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", "\n".join(_body_lines(letter))):
        for position, word in enumerate(WORD.findall(sentence)):
            word = re.sub(r"['’]s$", "", word).strip("'’-")
            if position == 0 or not word[:1].isupper() or sum(char.isalpha() for char in word) < 3:
                continue
            if word.lower() not in NAME_ALLOW_LIST:
                names.append(word)
    return list(dict.fromkeys(names))


def fact_issues(letter: str, source_texts: list[str]) -> list[str]:
    """Numbers and capitalised names in the letter that appear in none of the source texts."""
    try:
        sources = [str(text) for text in (source_texts or []) if text]
        source_numbers = {token for text in sources for token in _checked_numbers(text)}
        lowered_sources = [text.lower() for text in sources]
        issues = [
            f'The letter uses the number "{number}" which is not in the candidate\'s documents or the job description.'
            for number in _checked_numbers(letter)
            if number not in source_numbers
        ]
        # German capitalises every noun, so capitalised words aren't names there: only numbers are checked.
        names = [] if detect_language(letter) == "de" else _capitalized_names(letter)
        unknown = [name for name in names if not any(name.lower() in text for text in lowered_sources)]
        issues += [
            f'The letter mentions "{name}" which is not in the candidate\'s documents or the job description.'
            for name in unknown[:MAX_NAMES_REPORTED]
        ]
        return issues
    except Exception:
        return []


def style_issues(letter: str) -> list[str]:
    """Writing-framework style rules checked in code (length over 300 is handled elsewhere)."""
    try:
        from src.cover_letter_generator import count_body_paragraphs
    except ImportError:  # running as a script from inside src/
        from cover_letter_generator import count_body_paragraphs

    issues = []
    if not re.search(r"\d", "\n".join(_body_lines(letter))):
        issues.append("The letter has no concrete number or result.")
    paragraphs = count_body_paragraphs(letter)
    if paragraphs != TARGET_BODY_PARAGRAPHS:
        issues.append(f"The letter has {paragraphs} body paragraphs; use {TARGET_BODY_PARAGRAPHS}.")
    flat = " ".join(str(letter or "").lower().split())
    issues += [f'The letter uses a stock phrase: "{phrase}".' for phrase in STOCK_PHRASES if phrase in flat]
    last_lines = [line.strip().lower() for line in str(letter or "").splitlines() if line.strip()][-4:]
    if not any(line.startswith(SIGN_OFFS) for line in last_lines):
        issues.append("The letter has no sign-off.")
    return issues


def build_retry_note(issues: list[str]) -> str:
    """One correction note for a single soft retry (at most 6 issues, 600 characters)."""
    head = "IMPORTANT: fix only these problems and keep everything else: "
    tail = ". Do not add any fact, number or name that is not in the supplied documents."
    chosen = [issue.rstrip(". ") for issue in issues[:MAX_RETRY_ISSUES] if issue]
    while chosen and len(head + "; ".join(chosen) + tail) > MAX_RETRY_NOTE_CHARS:
        if len(chosen) > 1:
            chosen.pop()
        else:
            room = MAX_RETRY_NOTE_CHARS - len(head) - len(tail)
            chosen[0] = chosen[0][:room].rsplit(" ", 1)[0] if " " in chosen[0][:room] else chosen[0][:room]
            break
    return head + "; ".join(chosen) + tail
