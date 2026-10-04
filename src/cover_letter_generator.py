from __future__ import annotations

import argparse
import json
import logging
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import APIConnectionError, APIStatusError, Groq, RateLimitError

try:
    from src import writing_framework
except ImportError:  # running as a script from inside src/
    import writing_framework

logger = logging.getLogger(__name__)


def load_environment() -> None:
    """Load environment variables from the root .env file if present."""
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()


def get_groq_client() -> tuple[Groq, str]:
    """Return the configured Groq client and model name."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or not api_key.strip():
        raise ValueError("Missing GROQ_API_KEY. Add it to your .env file before generating the cover letter.")

    model_name = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    return Groq(api_key=api_key), model_name


def load_style_profile(path: Path) -> dict[str, Any]:
    """Load the user's writing-style profile from JSON."""
    if not path.exists():
        raise FileNotFoundError(f"Style profile not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ValueError("style_profile.json must contain a JSON object.")

    return payload


def load_anchor_data(path: Path) -> dict[str, Any]:
    """Load the company anchors produced by Chunk 4."""
    if not path.exists():
        raise FileNotFoundError(f"Company anchors file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ValueError("company_anchors.json must contain a JSON object.")

    anchors = payload.get("anchors")
    if not isinstance(anchors, list):
        raise ValueError("company_anchors.json does not contain an anchors list.")

    return payload


def load_candidate_letters(extracted_dir: Path) -> list[tuple[str, str]]:
    """Read the previous cover letters as supporting evidence."""
    if not extracted_dir.exists():
        raise FileNotFoundError(f"Extracted letters directory not found: {extracted_dir}")

    files = sorted(extracted_dir.glob("*.txt"))
    if not files:
        raise ValueError(f"No extracted cover-letter text files were found in {extracted_dir}.")

    letters: list[tuple[str, str]] = []
    for path in files:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            letters.append((path.stem, text))

    if not letters:
        raise ValueError("No usable previous cover-letter evidence was found.")

    return letters


def select_anchors(anchors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prompt the user to choose one or more anchors by index."""
    print("Available anchors:")
    for index, anchor in enumerate(anchors, start=1):
        title = anchor.get("title") or f"Anchor {index}"
        print(f"{index}. {title}")

    raw_selection = input("Select anchors (comma-separated, e.g. 1,3): ").strip()
    if not raw_selection:
        raise ValueError("No anchors were selected.")

    selected_indices: list[int] = []
    for part in raw_selection.split(","):
        item = part.strip()
        if not item:
            continue
        if not item.isdigit():
            raise ValueError(f"Invalid anchor selection: {item}")
        value = int(item)
        if value < 1 or value > len(anchors):
            raise ValueError(f"Anchor number out of range: {value}")
        if value not in selected_indices:
            selected_indices.append(value)

    if not selected_indices:
        raise ValueError("No valid anchors were selected.")

    selected = [anchors[index - 1] for index in selected_indices]
    print(f"Selected anchors: {', '.join(str(index) for index in selected_indices)}")
    return selected


def read_job_description(path: Path) -> str:
    """Read the current job description from the provided file."""
    if not path.exists():
        raise FileNotFoundError(f"Job description file not found: {path}")

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Job description file is empty: {path}")

    return content


def trim_evidence_snippet(text: str, limit: int = 500) -> str:
    """Trim long evidence text to a compact snippet that keeps the signal while respecting model limits."""
    snippet = " ".join(text.strip().split())
    if len(snippet) <= limit:
        return snippet

    trimmed = snippet[:limit].rsplit(" ", 1)[0] if " " in snippet[:limit] else snippet[:limit]
    return f"{trimmed} ... [truncated]"


def candidate_identity_lines(candidate_name: str | None) -> tuple[str, str]:
    """Return (identity line, sign-off rule) built only from the current user's own candidate name, if known."""
    if candidate_name:
        return (
            f"Candidate identity: {candidate_name}. Use only this candidate's own supplied information.",
            f"Sign the letter with the candidate's name exactly as: {candidate_name}",
        )
    return (
        "Candidate identity: not provided. Do not invent or guess a name; use only the candidate's own supplied information.",
        "End with 'Sincerely,' and do not add a name below it",
    )


def build_cover_letter_plan_prompt(
    job_description: str,
    selected_anchors: list[dict[str, Any]],
    style_profile: dict[str, Any],
    previous_letters: list[tuple[str, str]],
    company_url: str,
    *,
    candidate_name: str | None = None,
) -> str:
    """Create the structured plan that guides the final cover-letter writing step."""
    anchor_details: list[str] = []
    for index, anchor in enumerate(selected_anchors, start=1):
        anchor_details.append(
            f"--- Selected Anchor {index} ---\n"
            f"Title: {anchor.get('title', '')}\n"
            f"Company evidence: {anchor.get('company_evidence', '')}\n"
            f"Job connection: {anchor.get('job_connection', '')}\n"
            f"Candidate evidence: {anchor.get('candidate_evidence', '')}\n"
            f"Anchor statement: {anchor.get('anchor', '')}\n"
            f"Source URL: {anchor.get('source_url', company_url)}\n"
        )

    evidence_sections: list[str] = []
    for name, text in previous_letters[:2]:
        snippet = trim_evidence_snippet(text, limit=350)
        evidence_sections.append(f"--- {name} ---\n{snippet}\n")

    style_summary = json.dumps(style_profile, ensure_ascii=False, separators=(",", ":"))
    selected_angle_value = selected_anchors[0].get("title") if selected_anchors else ""
    identity_line, _ = candidate_identity_lines(candidate_name)

    return f"""
You are producing a cover-letter plan, not the final prose.

You must reason from the actual evidence in the candidate's prior cover letters, the current job description, the selected anchor, and the style profile.

{identity_line}

Use only supported information. Do not invent motivation, experience, skills, projects, technologies, or metrics.

Relevant inputs:

Style profile:
{style_summary}

Selected anchors:
{''.join(anchor_details)}

Current job description:
{job_description}

Company URL:
{company_url}

Previous cover-letter evidence:
{''.join(evidence_sections)}

Return valid JSON with this exact shape and no extra fields:
{{
  "role_need": "",
  "company_connection": "",
  "selected_angle": "",
  "hook": "",
  "candidate_evidence": [""],
  "personal_connection": "",
  "role_connection": "",
  "gap": "",
  "transferable_strength": "",
  "closing_direction": ""
}}

Instructions:
- role_need: a short summary of the most important role needs from the JD.
- company_connection: a short, factual connection between the company and the role, drawing only from the current company/anchor context.
- selected_angle: the actual selected human anchor title; do not invent a different angle.
- hook: a short, evidence-backed hook sentence or short paragraph that explains why this role/company is relevant to the candidate based on actual work.
- candidate_evidence: a list of 3 to 5 concrete evidence strings from the previous cover letters that support the selected angle and the role connection.
- personal_connection: a brief statement explaining why this specific type of work is interesting to the candidate, grounded only in the candidate's work patterns and demonstrated preferences.
- role_connection: a concise explanation of how the candidate's evidence connects to the role's major requirements.
- gap: brief acknowledgement of a relevant gap, if any, framed as a factual gap and not a personal weakness. Use empty string if no meaningful gap needs to be raised.
- transferable_strength: the relevant strength that makes the gap manageable, based on actual evidence from the letters.
- closing_direction: a short closing approach that reinforces the strongest evidence-based fit and the company/role connection.

Do not include any non-supported claims.
The selected_angle must be exactly the human-selected anchor title, not a paraphrase.
The candidate_evidence strings must be concrete, attributable to the candidate's prior work, and not generic statements.
""".strip()


def call_groq_json(
    client: Groq,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    max_tokens: int,
) -> str:
    """Call Groq with a strict JSON request, falling back to plain text parsing if Groq rejects the JSON schema."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        error_text = str(exc).lower()
        if "json_validate_failed" not in error_text and "failed to validate json" not in error_text and "failed to generate json" not in error_text:
            raise
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    raw_content = response.choices[0].message.content
    if raw_content is None or not raw_content.strip():
        raise ValueError("Groq returned an empty response body.")

    cleaned = raw_content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()

    return cleaned


def generate_cover_letter_plan(
    job_description: str,
    selected_anchors: list[dict[str, Any]],
    style_profile: dict[str, Any],
    previous_letters: list[tuple[str, str]],
    company_url: str,
    *,
    candidate_name: str | None = None,
) -> dict[str, Any]:
    """Generate a structured cover-letter plan before final writing."""
    load_environment()
    client, model_name = get_groq_client()
    prompt = build_cover_letter_plan_prompt(job_description, selected_anchors, style_profile, previous_letters, company_url, candidate_name=candidate_name)

    try:
        raw_content = call_groq_json(
            client,
            model_name,
            "You create a structured, evidence-based cover-letter plan. Return valid JSON with the exact required fields only.",
            prompt,
            temperature=0.1,
            max_tokens=600,
        )
    except (APIConnectionError, APIStatusError, RateLimitError) as exc:
        raise RuntimeError(f"Groq API request failed while building the cover-letter plan: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Unexpected Groq error while building the cover-letter plan: {exc}") from exc

    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        logger.error("ERROR: Groq returned invalid JSON plan content.\nRAW GROQ RESPONSE:\n%s", raw_content)
        raise ValueError("Groq returned invalid JSON content for the cover-letter plan.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Cover-letter plan was not a JSON object.")

    required_fields = {
        "role_need",
        "company_connection",
        "selected_angle",
        "hook",
        "candidate_evidence",
        "personal_connection",
        "role_connection",
        "gap",
        "transferable_strength",
        "closing_direction",
    }
    missing = sorted(required_fields - set(payload.keys()))
    if missing:
        raise ValueError(f"Cover-letter plan missing required fields: {missing}")

    candidate_evidence = payload.get("candidate_evidence")
    if not isinstance(candidate_evidence, list) or not candidate_evidence:
        raise ValueError("Cover-letter plan candidate_evidence must be a non-empty list.")

    return {
        "role_need": str(payload.get("role_need", "")).strip(),
        "company_connection": str(payload.get("company_connection", "")).strip(),
        "selected_angle": str(payload.get("selected_angle", "")).strip(),
        "hook": str(payload.get("hook", "")).strip(),
        "candidate_evidence": [str(item).strip() for item in candidate_evidence if str(item).strip()],
        "personal_connection": str(payload.get("personal_connection", "")).strip(),
        "role_connection": str(payload.get("role_connection", "")).strip(),
        "gap": str(payload.get("gap", "")).strip(),
        "transferable_strength": str(payload.get("transferable_strength", "")).strip(),
        "closing_direction": str(payload.get("closing_direction", "")).strip(),
    }


def build_cover_letter_prompt(
    job_description: str,
    selected_anchors: list[dict[str, Any]],
    style_profile: dict[str, Any],
    previous_letters: list[tuple[str, str]],
    company_url: str,
    plan: dict[str, Any],
    *,
    candidate_name: str | None = None,
) -> str:
    """Assemble the final generation prompt using the structured cover-letter plan."""
    anchor_details: list[str] = []
    for index, anchor in enumerate(selected_anchors, start=1):
        anchor_details.append(
            f"--- Selected Anchor {index} ---\n"
            f"Title: {anchor.get('title', '')}\n"
            f"Company evidence: {anchor.get('company_evidence', '')}\n"
            f"Job connection: {anchor.get('job_connection', '')}\n"
            f"Candidate evidence: {anchor.get('candidate_evidence', '')}\n"
            f"Anchor statement: {anchor.get('anchor', '')}\n"
            f"Source URL: {anchor.get('source_url', company_url)}\n"
        )

    evidence_sections: list[str] = []
    for name, text in previous_letters[:3]:
        snippet = trim_evidence_snippet(text, limit=550)  # reduced from 700 to stay under Groq's 7000 input-token limit
        evidence_sections.append(f"--- {name} ---\n{snippet}\n")

    style_summary = json.dumps(style_profile, ensure_ascii=False, separators=(",", ":"))
    plan_summary = json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
    # writing framework: overrides the default length target and adds its requirements block.
    length_line = writing_framework.length_instruction() or "Target approximately 350-500 words."
    framework_section = writing_framework.generation_section()
    # Quality lines the framework already covers; kept only when it is disabled to stay under the input-token limit.
    legacy_quality_lines = "" if framework_section else (
        "- Do not invent skills, projects, technologies, accomplishments, or personal motivations.\n"
        "- Use the evidence in the candidate's prior letters as the source of truth.\n"
        "- The opening must be an evidence-backed hook, not a generic statement of interest.\n"
        "- The letter must sound like a real professional wrote it, not a polished corporate summary.\n"
        "- Be natural, direct, and persuasive without sounding overly AI-generated.\n"
        "- Do not use generic enthusiasm or corporate buzzwords.\n"
        "- Keep the paragraph flow coherent and human.\n"
        "- If a gap is relevant, keep it brief and strength-oriented: Gap → transferable evidence → learning ability → manageable.\n"
    )
    legacy_closing_lines = "" if framework_section else (
        "- The hook, personal connection, role relevance, and gap handling must all feel real and grounded in the evidence.\n"
        "- Do not artificially over-polish or repeat generic statements.\n"
    )
    identity_line, signoff_line = candidate_identity_lines(candidate_name)

    return f"""
You are writing the final cover letter in the candidate's voice for a specific company and role.

{identity_line} Use the evidence in the prior letters as the source of truth.

You must follow the cover-letter plan exactly, but write the final prose naturally and coherently.

Hard requirements:
- Start with: Dear Hiring Manager,
- Use the plan as the structure for the argument, but do not copy the plan verbatim.
- It must feel specific to the actual company (identified by the Company URL and the selected anchor's company evidence below) and the actual role.
- Use the selected anchor as a narrative thread, not a keyword list.
- Do not start with “I am applying for…”, “I want to be direct…”, “I am excited to apply…”, “With my extensive experience…”, or any formulaic opening.
- {length_line}
{legacy_quality_lines}- The final closing should be concise and reinforce the strongest evidence-based fit.
- {signoff_line}
- Preserve the candidate's established voice as described in the style profile.
- Do not mention that you are following a plan or using a style profile.

Style profile:
{style_summary}

Cover-letter plan:
{plan_summary}

Selected anchors:
{''.join(anchor_details)}

Current job description:
{job_description}

Company URL:
{company_url}

Previous cover-letter evidence:
{''.join(evidence_sections)}

Return valid JSON with this exact shape:
{{
  "cover_letter": "The full cover letter text here..."
}}

Important:
- The output must be valid JSON only.
- The letter must be one coherent document, not a bullet list.
{legacy_closing_lines}
{framework_section}
""".strip()


def semantic_anchor_validation(letter: str, selected_anchor: dict[str, Any], job_description: str) -> dict[str, Any]:
    """Check whether the selected anchor is meaningfully reflected by the generated letter."""
    if not isinstance(selected_anchor, dict):
        return {"reflected": True, "confidence": 1.0, "evidence": "No anchor details were available."}

    load_environment()
    client, model_name = get_groq_client()

    anchor_summary = json.dumps(selected_anchor, ensure_ascii=False, indent=2)
    prompt = f"""
You are checking whether a generated cover letter meaningfully reflects the selected anchor's actual meaning.

Do not check whether the exact title appears verbatim. Instead, determine whether the letter communicates the underlying idea using the candidate's evidence.

Selected anchor object:
{anchor_summary}

Current job description:
{job_description}

Generated cover letter:
{letter}

Return ONLY valid JSON in this exact shape:
{{
  "reflected": true,
  "confidence": 0.92,
  "evidence": "A brief, concrete explanation of the actual evidence in the letter that matches the anchor's meaning."
}}
""".strip()

    try:
        raw_content = call_groq_json(
            client,
            model_name,
            "You evaluate whether a cover letter meaningfully reflects the selected anchor's actual meaning. Return only valid JSON with reflected, confidence, and evidence.",
            prompt,
            temperature=0.1,
            max_tokens=600,
        )
    except (APIConnectionError, APIStatusError, RateLimitError):
        return {"reflected": True, "confidence": 0.0, "evidence": "Semantic anchor validation could not be completed due to the Groq API issue."}
    except Exception:
        return {"reflected": True, "confidence": 0.0, "evidence": "Semantic anchor validation could not be completed due to an unexpected error."}

    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError:
        return {"reflected": True, "confidence": 0.0, "evidence": "Semantic validation returned invalid JSON."}

    if not isinstance(payload, dict):
        return {"reflected": True, "confidence": 0.0, "evidence": "Semantic validation returned an invalid object."}

    reflected = bool(payload.get("reflected", True))
    confidence = payload.get("confidence", 0.0)
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0

    return {
        "reflected": reflected,
        "confidence": max(0.0, min(1.0, confidence_value)),
        "evidence": str(payload.get("evidence", "No explanation provided.")),
    }


def validate_generated_letter(letter: str, selected_anchors: list[dict[str, Any]], job_description: str, *, min_words: int = 200, candidate_name: str | None = None) -> None:
    """Perform basic validation before accepting the generated cover letter."""
    if not letter or not letter.strip():
        raise ValueError("Generated cover letter is empty.")

    lowered = letter.lower()
    placeholders = [
        "[company name]",
        "[your name]",
        "[insert",
        "your name",
        "company name",
        "insert here",
        "example company",
    ]
    if any(token in lowered for token in placeholders):
        raise ValueError("Generated cover letter contains obvious placeholders or missing values.")

    if len(letter.split()) < min_words:
        raise ValueError("Generated cover letter is too short to be a credible application letter.")

    # Identify the current candidate only when their name is known from their own resume; never a default name.
    if candidate_name:
        name_parts = [part.lower() for part in candidate_name.split() if len(part) > 1]
        if name_parts and not any(part in lowered for part in name_parts):
            raise ValueError("Generated cover letter does not clearly identify the candidate.")

    suspicious_patterns = [
        "success rates acrossi want to be direct",
        "i want to be direct.*success rates across",
        "i will be honest.*success rates across",
    ]
    if any(re.search(pattern, lowered) for pattern in suspicious_patterns):
        raise ValueError("Generated cover letter contains obvious text corruption or duplicated content.")

    for anchor in selected_anchors:
        if not isinstance(anchor, dict):
            continue
        validation = semantic_anchor_validation(letter, anchor, job_description)
        if validation.get("reflected") is False:
            logger.warning(
                "Selected anchor meaning may not be reflected in the generated letter. "
                "Reason: %s",
                validation.get("evidence", "No evidence provided."),
            )


def normalize_for_comparison(text: str) -> str:
    """Normalize a cover letter for unchanged-output comparison."""
    return re.sub(r"\s+", " ", text.strip().lower())


def is_effectively_unchanged(current_letter: str, revised_letter: str) -> bool:
    """Reject revisions that are identical or effectively unchanged from the current version."""
    if not current_letter or not revised_letter:
        return True

    def paragraph_list(text: str) -> list[str]:
        paragraphs = []
        for raw_paragraph in re.split(r"\n\s*\n", text.strip()):
            cleaned = normalize_for_comparison(raw_paragraph)
            if cleaned:
                paragraphs.append(cleaned)
        return paragraphs

    current_paragraphs = paragraph_list(current_letter)
    revised_paragraphs = paragraph_list(revised_letter)

    if current_paragraphs == revised_paragraphs:
        return True

    if not current_paragraphs or not revised_paragraphs:
        return True

    current_body = [p for p in current_paragraphs if not p.startswith("dear hiring manager")]
    revised_body = [p for p in revised_paragraphs if not p.startswith("dear hiring manager")]

    if not current_body or not revised_body:
        return True

    # Feedback may target only the middle or closing paragraph, so an identical opening alone is not "unchanged".
    return False


NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
SIGN_OFF_PATTERN = re.compile(
    r"^(sincerely|yours sincerely|yours faithfully|yours truly|best regards|kind regards|warm regards|regards|best|respectfully|thank you|thanks|with gratitude)\s*[,.!]?\s*$",
    re.IGNORECASE,
)


def extract_feedback_constraints(user_feedback: str, feedback_history: list[str] | None = None) -> dict[str, int | None]:
    """Extract explicit word/paragraph counts, preferring the latest feedback and then the most recent earlier feedback."""
    constraints: dict[str, int | None] = {"words": None, "paragraphs": None}
    for text in [user_feedback, *reversed(feedback_history or [])]:
        lowered = (text or "").lower()
        if constraints["words"] is None:
            match = re.search(r"(\d[\d,]*)\s*-?\s*words?\b", lowered)
            if match and int(match.group(1).replace(",", "")) > 0:
                constraints["words"] = int(match.group(1).replace(",", ""))
        if constraints["paragraphs"] is None:
            match = re.search(r"\b(\d+|" + "|".join(NUMBER_WORDS) + r")\s*-?\s*paragraphs?\b", lowered)
            if match:
                token = match.group(1)
                value = int(token) if token.isdigit() else NUMBER_WORDS[token]
                if value > 0:
                    constraints["paragraphs"] = value
    return constraints


def count_body_paragraphs(letter: str) -> int:
    """Count blank-line-separated paragraphs, excluding the greeting and the sign-off."""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", letter.strip()) if block.strip()]

    if blocks and blocks[0].lower().startswith("dear"):
        rest = "\n".join(blocks[0].splitlines()[1:]).strip()
        blocks = ([rest] if rest else []) + blocks[1:]

    while blocks:
        lines = blocks[-1].splitlines()
        cut = next((index for index, line in enumerate(lines) if SIGN_OFF_PATTERN.match(line.strip())), None)
        if cut is not None:
            rest = "\n".join(lines[:cut]).strip()
            blocks = blocks[:-1] + ([rest] if rest else [])
            break
        # A bare name/signature line (e.g. "Jane Doe") after a separate sign-off block.
        if len(blocks[-1].split()) <= 4 and not blocks[-1].endswith((".", "!", "?")):
            blocks = blocks[:-1]
            continue
        break

    return len(blocks)


def check_feedback_constraints(letter: str, constraints: dict[str, int | None]) -> str | None:
    """Return a retry instruction describing unmet explicit constraints, or None when all are satisfied."""
    problems: list[str] = []

    requested_words = constraints.get("words")
    if requested_words:
        actual_words = len(letter.split())
        tolerance = max(1, round(requested_words * 0.10))
        if abs(actual_words - requested_words) > tolerance:
            problems.append(
                f"The previous draft had {actual_words} words. The user requires approximately {requested_words} words "
                f"(between {requested_words - tolerance} and {requested_words + tolerance})."
            )

    requested_paragraphs = constraints.get("paragraphs")
    if requested_paragraphs:
        actual_paragraphs = count_body_paragraphs(letter)
        if actual_paragraphs != requested_paragraphs:
            problems.append(
                f"The previous draft had {actual_paragraphs} body paragraphs. The user requires exactly {requested_paragraphs} "
                "body paragraphs, not counting the greeting and the sign-off."
            )

    if not problems:
        return None
    return " ".join(problems) + " Rewrite the full letter to satisfy these constraints."


def build_cover_letter_revision_prompt(
    current_letter: str,
    user_feedback: str,
    job_description: str,
    selected_anchors: list[dict[str, Any]],
    style_profile: dict[str, Any],
    previous_letters: list[tuple[str, str]],
    company_url: str,
    *,
    retry_context: str = "",
    feedback_history: list[str] | None = None,
    constraints: dict[str, int | None] | None = None,
    candidate_name: str | None = None,
) -> str:
    """Create a targeted revision prompt for the current cover letter."""
    anchor_details: list[str] = []
    for index, anchor in enumerate(selected_anchors, start=1):
        anchor_details.append(
            f"--- Selected Anchor {index} ---\n"
            f"Title: {anchor.get('title', '')}\n"
            f"Company evidence: {anchor.get('company_evidence', '')}\n"
            f"Job connection: {anchor.get('job_connection', '')}\n"
            f"Candidate evidence: {anchor.get('candidate_evidence', '')}\n"
            f"Anchor statement: {anchor.get('anchor', '')}\n"
            f"Source URL: {anchor.get('source_url', company_url)}\n"
        )

    evidence_sections: list[str] = []
    for name, text in previous_letters[:2]:
        snippet = trim_evidence_snippet(text, limit=350)
        evidence_sections.append(f"--- {name} ---\n{snippet}\n")

    style_summary = json.dumps(style_profile, ensure_ascii=False, separators=(",", ":"))

    retry_prefix = ""
    if retry_context:
        retry_prefix = f"\nIMPORTANT: {retry_context}\n"

    history_section = ""
    if feedback_history:
        history_lines = "\n".join(f"{index}. {item}" for index, item in enumerate(feedback_history, start=1))
        history_section = (
            "\nEARLIER FEEDBACK (lower priority; applied in earlier revisions; keep honoring it only where it does not conflict with the LATEST FEEDBACK):\n"
            f"{history_lines}\n"
        )

    requirement_lines: list[str] = []
    if constraints and constraints.get("words"):
        requirement_lines.append(f"- The whole letter must be approximately {constraints['words']} words (within about 10%).")
    if constraints and constraints.get("paragraphs"):
        requirement_lines.append(
            f"- The letter must have exactly {constraints['paragraphs']} body paragraphs separated by blank lines "
            "(the greeting line and the sign-off are not counted as paragraphs)."
        )
    framework_section = writing_framework.revision_section()  # writing framework
    requirements_section = ""
    if requirement_lines:
        requirements_section = "\nHARD REQUIREMENTS FROM THE USER (mandatory for the whole letter):\n" + "\n".join(requirement_lines) + "\n"
    identity_line, signoff_line = candidate_identity_lines(candidate_name)

    return f"""
Revise the CURRENT COVER LETTER according to the LATEST FEEDBACK.

{identity_line} {signoff_line}.

The user's feedback is an explicit editing instruction and MUST be applied.
The model must not return the current letter unchanged.
The feedback must be addressed in the relevant section of the letter, not ignored.
Identify what part of the letter the feedback refers to and rewrite that part meaningfully.
When feedback items conflict, the LATEST FEEDBACK wins over EARLIER FEEDBACK.
Explicit word-count, paragraph-count, and formatting requests are HARD requirements for the whole letter.
They override the style profile's paragraph structure, any earlier length target, and the instruction to make only local edits.
Restructure or rewrite the entire letter as necessary to satisfy them.

{retry_prefix}
CURRENT COVER LETTER:
{current_letter}

{history_section}
LATEST FEEDBACK (highest priority):
{user_feedback}
{requirements_section}

JOB DESCRIPTION:
{job_description}

SELECTED COMPANY ANGLE:
{''.join(anchor_details)}

CANDIDATE EVIDENCE:
{''.join(evidence_sections)}

STYLE PROFILE:
{style_summary}

Instructions:
- Apply the user's feedback directly and substantively.
- Preserve factual accuracy and candidate evidence unless the user's feedback asks for a change.
- Preserve the candidate's established writing style (tone and voice); explicit length, paragraph-count, or formatting requests override its paragraph structure.
- Preserve valid company and role references.
- Do not invent experience, skills, metrics, or company facts.
- Do not make unrelated changes unless required for coherence or to satisfy an explicit length, paragraph-count, or formatting requirement.
- Do not return the current letter unchanged.
- If the user asks for something unsupported, keep the letter grounded in the actual evidence rather than inventing claims.
- Make the writing personal, reflective, and human when appropriate. You may transform documented candidate experiences into natural reflections, lessons, motivations, and narrative connections. However, do not invent specific events, memories, failures, conversations, feelings, motivations, achievements, or experiences that are not supported by the candidate evidence.
- You may write lines such as "That experience changed how I think about reliable AI systems" or "Working on this problem taught me to treat evaluation as part of the engineering process" when they are reasonably grounded in the documented evidence, but do not invent anecdotal moments, manager feedback, client conversations, or production failures unless they are explicitly supported by the evidence.
- You must make a real change in the section discussed by the feedback; if the user requests a different opening, the opening sentence or paragraph must genuinely change.
- Return valid JSON with this exact shape:
  {{
    "cover_letter": "The revised cover letter text here..."
  }}

{framework_section}
""".strip()


def generate_cover_letter_revision(
    current_letter: str,
    user_feedback: str,
    job_description: str,
    selected_anchors: list[dict[str, Any]],
    style_profile: dict[str, Any],
    previous_letters: list[tuple[str, str]],
    company_url: str,
    feedback_history: list[str] | None = None,
    candidate_name: str | None = None,
) -> str:
    """Generate a revised version of the current cover letter using the user's feedback."""
    load_environment()
    client, model_name = get_groq_client()

    constraints = writing_framework.cap_feedback_constraints(extract_feedback_constraints(user_feedback, feedback_history))
    requested_words = constraints["words"]
    # Let an explicitly requested short letter pass the generic 200-word floor, and leave room for long requests.
    min_words = min(200, int(requested_words * 0.9)) if requested_words else 200
    max_tokens = max(1200, requested_words * 2 + 300) if requested_words else 1200

    max_attempts = 3
    retry_message = "The previous revision did not apply the user's feedback. Revise the letter again and make a substantive change specifically addressing the user's feedback. Do not return the previous version unchanged."
    retry_context = ""

    for attempt in range(1, max_attempts + 1):
        prompt = build_cover_letter_revision_prompt(
            current_letter=current_letter,
            user_feedback=user_feedback,
            job_description=job_description,
            selected_anchors=selected_anchors,
            style_profile=style_profile,
            previous_letters=previous_letters,
            company_url=company_url,
            retry_context=retry_context,
            feedback_history=feedback_history,
            constraints=constraints,
            candidate_name=candidate_name,
        )

        try:
            raw_content = call_groq_json(
                client,
                model_name,
                "You revise a cover letter based on direct user feedback while preserving the candidate's factual grounding and writing style. Apply the requested changes and do not return the current letter unchanged.",
                prompt,
                temperature=0.2,
                max_tokens=max_tokens,
            )
        except (APIConnectionError, APIStatusError, RateLimitError) as exc:
            raise RuntimeError(f"Groq API request failed while revising the letter: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Unexpected Groq error while revising the letter: {exc}") from exc

        try:
            payload = json.loads(raw_content)
        except json.JSONDecodeError:
            cleaned = raw_content.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.IGNORECASE)
                cleaned = cleaned.strip()
            try:
                payload = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                logger.error("ERROR: Groq returned invalid JSON while revising the cover letter.\nRAW GROQ RESPONSE:\n%s", raw_content)
                raise ValueError("Groq returned invalid JSON content while revising the cover letter.") from exc

        if not isinstance(payload, dict) or "cover_letter" not in payload:
            raise ValueError("Groq response did not include a cover_letter field for the revision.")

        revised_letter = str(payload["cover_letter"]).strip()
        validate_generated_letter(revised_letter, selected_anchors, job_description, min_words=min_words, candidate_name=candidate_name)

        if is_effectively_unchanged(current_letter, revised_letter):
            if attempt < max_attempts:
                logger.warning("Revision rejected as unchanged; retrying with stronger correction prompt.")
                retry_context = retry_message
                continue
            raise ValueError("Revision was rejected because the model returned an unchanged letter after the allowed retry attempts.")

        constraint_problem = check_feedback_constraints(revised_letter, constraints)
        framework_problem = writing_framework.word_limit_problem(revised_letter)  # writing framework
        if constraint_problem or framework_problem:
            if attempt < max_attempts:
                retry_context = " ".join(problem for problem in (constraint_problem, framework_problem) if problem)
                logger.warning("Revision rejected: %s", retry_context)
                continue
            if constraint_problem:
                raise ValueError(
                    f"Revision was not saved because it did not meet your explicit requirements after {max_attempts} attempts. {constraint_problem}"
                )
            raise ValueError(writing_framework.GENERIC_FAILURE_MESSAGE)

        return revised_letter

    raise ValueError("Revision failed after the allowed retry attempts.")


def ask_yes_no(prompt: str) -> bool:
    """Read a yes/no answer and normalize it to a boolean."""
    answer = input(prompt).strip().lower()
    return answer in {"y", "yes"}


def generate_cover_letter(
    job_description: str,
    selected_anchors: list[dict[str, Any]],
    style_profile: dict[str, Any],
    previous_letters: list[tuple[str, str]],
    company_url: str,
    *,
    candidate_name: str | None = None,
) -> str:
    """Generate the final cover letter through Groq."""
    load_environment()
    client, model_name = get_groq_client()
    plan = generate_cover_letter_plan(job_description, selected_anchors, style_profile, previous_letters, company_url, candidate_name=candidate_name)
    base_prompt = build_cover_letter_prompt(job_description, selected_anchors, style_profile, previous_letters, company_url, plan, candidate_name=candidate_name)
    prompt = base_prompt
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        try:
            raw_content = call_groq_json(
                client,
                model_name,
                "You write a tailored cover letter based on the candidate's evidence, the role, the selected anchor, and the structured cover-letter plan. Return valid JSON with a cover_letter string.",
                prompt,
                temperature=0.25,
                max_tokens=1200,
            )
        except (APIConnectionError, APIStatusError, RateLimitError) as exc:
            raise RuntimeError(f"Groq API request failed: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Unexpected Groq error: {exc}") from exc

        try:
            payload = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            logger.error("ERROR: Groq returned invalid JSON content.\nRAW GROQ RESPONSE:\n%s", raw_content)
            raise ValueError("Groq returned invalid JSON content.") from exc

        if not isinstance(payload, dict) or "cover_letter" not in payload:
            raise ValueError("Groq response did not include a cover_letter field.")

        letter = str(payload["cover_letter"]).strip()
        validate_generated_letter(letter, selected_anchors, job_description, candidate_name=candidate_name)

        # writing framework: the complete letter must stay within the word limit.
        framework_problem = writing_framework.word_limit_problem(letter)
        if not framework_problem:
            return letter
        logger.warning("Generated letter rejected by the writing framework: %s", framework_problem)
        prompt = f"{base_prompt}\n\nIMPORTANT: {framework_problem}"

    raise ValueError(writing_framework.GENERIC_FAILURE_MESSAGE)


def save_cover_letter(path: Path, letter: str) -> None:
    """Persist the final cover letter to disk."""
    path.write_text(letter + "\n", encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        parser = argparse.ArgumentParser(description="Generate a cover letter using selected anchors, candidate evidence, and the user's writing style.")
        parser.add_argument("--jd", required=True, help="Path to the current job description text file.")
        args = parser.parse_args()

        project_root = Path(__file__).resolve().parent.parent
        jd_path = Path(args.jd).resolve()

        company_anchors_path = project_root / "company_anchors.json"
        style_profile_path = project_root / "style_profile.json"
        extracted_dir = project_root / "extracted_letters"
        output_path = project_root / "generated_cover_letter.txt"

        job_description = read_job_description(jd_path)
        anchor_payload = load_anchor_data(company_anchors_path)
        anchors = anchor_payload.get("anchors", [])
        selected_anchors = select_anchors(anchors)
        style_profile = load_style_profile(style_profile_path)
        previous_letters = load_candidate_letters(extracted_dir)
        company_url = str(anchor_payload.get("company_url") or "")

        print("\nGenerating personalized cover letter...")
        letter = generate_cover_letter(
            job_description=job_description,
            selected_anchors=selected_anchors,
            style_profile=style_profile,
            previous_letters=previous_letters,
            company_url=company_url,
        )

        save_cover_letter(output_path, letter)

        print("\n--------------------------------")
        print("GENERATED COVER LETTER")
        print("--------------------------------")
        print(letter)
        print("--------------------------------")
        print("\nSaved to:")
        print(output_path)

        if not ask_yes_no("\nWould you like to revise this letter? (yes/no): "):
            print("\nAccepted the current cover letter.")
            return 0

        revision_count = 0
        current_letter = letter
        while revision_count < 3:
            revision_count += 1
            print(f"\nRevision {revision_count} of 3")
            feedback = input("Enter your feedback: ").strip()
            if not feedback:
                print("No feedback provided. Keeping the current version.")
                print("\nAccepted the current cover letter.")
                return 0

            revised_letter = generate_cover_letter_revision(
                current_letter=current_letter,
                user_feedback=feedback,
                job_description=job_description,
                selected_anchors=selected_anchors,
                style_profile=style_profile,
                previous_letters=previous_letters,
                company_url=company_url,
            )

            revision_path = project_root / f"generated_cover_letter_revision_{revision_count}.txt"
            save_cover_letter(revision_path, revised_letter)

            print("\n--------------------------------")
            print(f"REVISED COVER LETTER - REVISION {revision_count}")
            print("--------------------------------")
            print(revised_letter)
            print("--------------------------------")
            print(f"\nSaved to:")
            print(revision_path)

            if revision_count >= 3:
                print("\nMaximum revision limit reached. Final version accepted.")
                return 0

            if not ask_yes_no("\nWould you like to revise this letter again? (yes/no): "):
                print("\nAccepted the current revised cover letter.")
                return 0

            current_letter = revised_letter

        print("\nMaximum revision limit reached. Final version accepted.")
        return 0
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:  # pragma: no cover - safety net
        logger.exception("Unexpected failure while generating the cover letter: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
