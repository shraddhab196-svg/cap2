"""Cover Letter Writing Framework: the single source of truth for what a generated letter must accomplish.

This is a removable business-logic layer. The generator and evaluator consume it only through the
functions below. To disable it, set FRAMEWORK_ENABLED = False: every function then returns an empty
value and the previous generation/evaluation behaviour applies. To remove it entirely, delete this
file and the call sites marked "writing framework" in cover_letter_generator.py and
cover_letter_evaluator.py.

The framework text is used only inside LLM prompts and backend checks; it is never shown in the UI.
"""

from __future__ import annotations

FRAMEWORK_ENABLED = True

MAX_WORDS = 300
TARGET_MIN_WORDS = 250
BODY_PARAGRAPHS = 3

# Generic phrases the framework explicitly names as things to avoid.
GENERIC_OPENINGS = ("i am writing to apply for", "i am excited to apply", "with my experience")
EMPTY_CLAIMS = ("i am results-driven", "i am passionate")

GENERIC_FAILURE_MESSAGE = "A cover letter meeting the quality requirements could not be produced. Please try again."

LENGTH_REQUIREMENT = (
    f'The ENTIRE letter, including "Dear Hiring Manager,", all body paragraphs, the sign-off and the candidate\'s name, '
    f"must be no more than {MAX_WORDS} words. Target {TARGET_MIN_WORDS}-{MAX_WORDS}; absolute maximum {MAX_WORDS}."
)

FRAMEWORK_RULES = f"""
A. OPENING (highest priority): very strong, highly personalized, specific, unique, relevant to the actual company/job. Ideally name a real problem/need/challenge/priority from the job/company and immediately connect it to what the candidate can contribute, so the reader feels "This person understands what we need." No generic openings ("I am writing to apply for...", "I am excited to apply...", "With my experience..."). No fixed formula: vary wording to the actual JD, company research and candidate evidence. Never invent a company problem or candidate capability to strengthen the opening.
B. RELEVANCE: show the job requirement was understood and the experience aligns. Focus on the 2-3 most relevant JD needs with evidence chosen for them; do not repeat the whole resume.
C. EVIDENCE: specific facts over generic claims; real numbers/results only when supported by candidate evidence; show what the candidate actually did; no empty claims ("I am results-driven", "I am passionate").
D. COMPANY: use a real company/product/project/detail from company research when available and relevant; the letter must not feel reusable for every company; address the company's actual need/problem, not only the candidate's desire for the job.
E. GAPS: the candidate need not meet 100% of the JD. For a missing requirement use genuine adjacent/transferable experience, explain the connection, and where appropriate show credible ability to learn it quickly. Never claim a tool/technology without supporting candidate evidence.
F. MOTIVATION: tie interest to the actual work, problem, product and role; no generic company praise or empty statements.
G. STYLE: simple, direct, human, easy to read, specific, concise. Avoid complex language, buzzword stuffing, long paragraphs, resume repetition, excessive flattery and template-like language.
H. STRUCTURE: concise three-paragraph structure (greeting and sign-off are not paragraphs): 1) strong personalized hook + immediate candidate connection; 2) 2-3 strongest pieces of evidence aligned with the JD; 3) why this company/role + what the candidate could contribute + simple close.
LENGTH: {LENGTH_REQUIREMENT}
STYLE PROFILE: it describes HOW the candidate writes (voice); this framework defines WHAT the letter must accomplish and wins any conflict. Previous letters may not be a perfectly original human style, so neither is the source of truth for structure or quality.
NO FABRICATION (hard requirement): never invent experience, employers, projects, responsibilities, achievements, metrics, technologies used, qualifications or company facts; handle JD gaps with truthful transferable skills and credible learning ability.
""".strip()


def length_instruction() -> str:
    """Return the length rule for the generation prompt, or "" when the framework is disabled."""
    return LENGTH_REQUIREMENT if FRAMEWORK_ENABLED else ""


def generation_section() -> str:
    """Framework block appended to the first-draft generation prompt."""
    if not FRAMEWORK_ENABLED:
        return ""
    return (
        "COVER LETTER WRITING FRAMEWORK (overrides the style profile and any conflicting instruction above; never mention it in the letter)\n"
        f"{FRAMEWORK_RULES}"
    )


def revision_section() -> str:
    """Framework block appended to the revision prompt."""
    if not FRAMEWORK_ENABLED:
        return ""
    return (
        "COVER LETTER WRITING FRAMEWORK\n"
        "The revised letter must still meet these requirements. Explicit user feedback may override the framework's "
        f"structure and style guidance, but NEVER the no-fabrication rule or the {MAX_WORDS}-word maximum for the entire letter. "
        "Never mention this framework in the letter.\n\n"
        f"{FRAMEWORK_RULES}"
    )


def evaluation_section() -> str:
    """Framework block appended to the evaluation prompt."""
    if not FRAMEWORK_ENABLED:
        return ""
    return (
        "COVER LETTER WRITING FRAMEWORK\n"
        "Also evaluate the letter against this framework. Where the style profile conflicts with the framework "
        "(structure, length), judge against the framework; voice fidelity covers tone and voice only.\n\n"
        f"{FRAMEWORK_RULES}\n\n"
        "Check in particular: strong/personalized opening; non-generic opening; JD relevance; specific evidence; "
        "company-specific relevance; unsupported/fabricated claims; handling of experience gaps; motivation/reason for interest; "
        f"three-paragraph structure; complete-letter word count (maximum {MAX_WORDS}, including greeting and sign-off); "
        "generic/buzzword-heavy writing.\n"
        'In addition to the JSON fields below, include a top-level "framework_issues" array of strings, one per unmet '
        "framework requirement (an empty array if every requirement is met)."
    )


def count_words(letter: str) -> int:
    """Count every word in the complete letter, including greeting, sign-off, and name."""
    return len((letter or "").split())


def word_limit_problem(letter: str) -> str | None:
    """Return a correction instruction if the complete letter exceeds the word limit, else None."""
    if not FRAMEWORK_ENABLED:
        return None
    total = count_words(letter)
    if total <= MAX_WORDS:
        return None
    return (
        f"The previous draft had {total} words in total. The ENTIRE letter, including the greeting, sign-off and name, "
        f"must be no more than {MAX_WORDS} words (target {TARGET_MIN_WORDS}-{MAX_WORDS}). Rewrite the full letter to satisfy this limit."
    )


def cap_feedback_constraints(constraints: dict[str, int | None]) -> dict[str, int | None]:
    """Cap an explicit user word-count request at the framework maximum."""
    if not FRAMEWORK_ENABLED or not constraints.get("words") or constraints["words"] <= MAX_WORDS:
        return constraints
    return {**constraints, "words": MAX_WORDS}


def deterministic_issues(letter: str) -> list[str]:
    """Rule-based framework checks used by the evaluator (no LLM)."""
    if not FRAMEWORK_ENABLED or not (letter or "").strip():
        return []

    try:
        from src.cover_letter_generator import count_body_paragraphs
    except ImportError:  # running as a script from inside src/
        from cover_letter_generator import count_body_paragraphs

    issues: list[str] = []
    total = count_words(letter)
    if total > MAX_WORDS:
        issues.append(f"The complete letter has {total} words; the maximum is {MAX_WORDS} including greeting and sign-off.")

    paragraphs = count_body_paragraphs(letter)
    if paragraphs != BODY_PARAGRAPHS:
        issues.append(f"The letter has {paragraphs} body paragraphs; the framework requires {BODY_PARAGRAPHS}.")

    lowered = letter.lower()
    blocks = [block.strip() for block in lowered.split("\n\n") if block.strip()]
    if blocks and blocks[0].startswith("dear"):
        blocks[0] = "\n".join(blocks[0].splitlines()[1:]).strip()
    opening = next((block for block in blocks if block), "")
    if any(phrase in opening for phrase in GENERIC_OPENINGS):
        issues.append("The opening uses a generic formula instead of a personalized, company/job-specific hook.")
    if any(phrase in lowered for phrase in EMPTY_CLAIMS):
        issues.append('The letter uses empty claims such as "I am results-driven" or "I am passionate".')

    return issues
