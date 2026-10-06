"""Pick a small, job-relevant slice of real resume lines for the letter prompts.

Pure functions only: no database, network, model call, filesystem or logging. Lines are passed through as
written (whitespace collapsed, long lines cut at a word boundary); numbers are never computed or changed.
"""
from __future__ import annotations

import re
from typing import Any

MAX_TOTAL_CHARS = 1500
MAX_LINES = 8
MAX_LINE_CHARS = 220
MIN_LINE_CHARS = 25
COVERED_SHARE = 0.6
NGRAM = 4

RESUME_FACTS_HEADER = (
    "RESUME FACTS (real facts from the candidate's own resume; use only if relevant to the chosen angle; "
    "they add to the letters, they do not replace the candidate's writing voice):"
)
RESUME_FACTS_RULES = (
    "Rules for these facts: use only what is written above; copy numbers exactly; do not add a baseline, "
    "a time frame or a result that is not written; if a number has no baseline, state it plainly without a comparison."
)

# Short tool-like tokens worth matching even though they are under 4 letters.
TOOL_TOKENS = {"sql", "rag", "aws", "gcp", "nlp", "llm", "api", "etl", "git", "gpu", "ml", "ai"}
STOP_WORDS = {
    "about", "also", "able", "ability", "across", "after", "based", "being", "both", "candidate", "company", "could",
    "each", "from", "good", "great", "have", "help", "ideal", "including", "into", "join", "looking", "make", "more",
    "most", "must", "other", "over", "plus", "role", "should", "skills", "some", "strong", "such", "team", "teams",
    "than", "that", "their", "them", "then", "there", "these", "they", "this", "those", "very", "well", "what", "when",
    "where", "which", "will", "with", "within", "work", "working", "would", "year", "years", "your", "experience",
    "knowledge", "requirements", "responsibilities",
}

BULLETS = re.compile(r"[•▪●◦‣·]")
LEADING_MARKER = re.compile(r"^\s*(?:[-*–—]\s+)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|org|net|io|dev|ai|co|de)\b(?:/|\s|$)", re.IGNORECASE)
# Phone shapes: +49 170 1234567 / 0049 ... / (555) 010-0199 / 555-010-0199. Year ranges like 2019-2023 do not match.
PHONE = re.compile(r"(?:\+|\b00)\d[\d\s().-]{6,}\d|\(\d{2,5}\)\s?\d|\b\d{3}[\s.-]\d{3}[\s.-]\d{4}\b")
NAME_WORD = re.compile(r"^[^\W\d_][^\W\d_'.-]*(?:['.-][^\W\d_]+)*\.?$")


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(text or "").lower()).split())


def _ngrams(words: list[str]) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + NGRAM]) for i in range(len(words) - NGRAM + 1)}


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = cut.rfind(" ")
    return (cut[:boundary] if boundary > 0 else cut).rstrip()


def _looks_like_name_header(line: str) -> bool:
    """Same idea as extract_candidate_name: 2-4 capitalised name-like words before any separator."""
    words = re.split(r"[|,•·–—]", line, maxsplit=1)[0].split()
    return 2 <= len(words) <= 4 and all(NAME_WORD.match(word) and word[0].isupper() for word in words)


def _is_contact_line(line: str) -> bool:
    return bool(EMAIL.search(line) or URL.search(line) or PHONE.search(line))


def _resume_lines(resume_text: str) -> list[str]:
    raw = [line for chunk in str(resume_text or "").splitlines() for line in BULLETS.split(chunk)]
    lines = [" ".join(LEADING_MARKER.sub("", line).split()) for line in raw]
    lines = [line for line in lines if line]
    if lines and _looks_like_name_header(lines[0]):
        lines = lines[1:]
    return [line for line in lines if len(line) >= MIN_LINE_CHARS and not _is_contact_line(line)]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", str(text or "").lower()))


def _keywords(job_description: str, selected_anchor: dict[str, Any] | None) -> set[str]:
    anchor = selected_anchor or {}
    source = " ".join([job_description or "", *(str(anchor.get(key) or "") for key in ("title", "job_connection", "anchor"))])
    return {token for token in _tokens(source) if (len(token) >= 4 and token not in STOP_WORDS) or token in TOOL_TOKENS}


def select_resume_facts(
    resume_text: str,
    job_description: str,
    selected_anchor: dict[str, Any] | None,
    letters: list[tuple[str, str]],
    max_chars: int = MAX_TOTAL_CHARS,
) -> str:
    """Return up to 8 relevant resume lines as '- ' bullets in resume order, or "" when nothing qualifies."""
    lines = _resume_lines(resume_text)
    if not lines:
        return ""
    keywords = _keywords(job_description, selected_anchor)
    letter_grams = _ngrams(_normalize(" ".join(text for _, text in letters or [])).split())

    candidates: list[tuple[int, int, str]] = []  # (score, resume position, line)
    for position, line in enumerate(lines):
        score = len(keywords & _tokens(line)) + (1 if re.search(r"[\d%]", line) else 0)
        if score < 1:
            continue
        grams = _ngrams(_normalize(line).split())
        if grams and sum(gram in letter_grams for gram in grams) / len(grams) >= COVERED_SHARE:
            continue  # the candidate's own letters already say this
        candidates.append((score, position, _cap(line, MAX_LINE_CHARS)))

    chosen: list[tuple[int, str]] = []
    total = 0
    for score, position, line in sorted(candidates, key=lambda item: (-item[0], item[1])):
        if len(chosen) >= MAX_LINES:
            break
        cost = len(line) + 2 + (1 if chosen else 0)  # "- " prefix and the joining newline
        if total + cost > max_chars:
            continue
        chosen.append((position, line))
        total += cost
    return "\n".join(f"- {line}" for _, line in sorted(chosen))


def format_resume_facts_section(facts: str) -> str:
    """The prompt section for selected resume facts; "" when there are none."""
    if not facts:
        return ""
    return f"{RESUME_FACTS_HEADER}\n{facts}\n{RESUME_FACTS_RULES}"
