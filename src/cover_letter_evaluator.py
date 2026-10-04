from __future__ import annotations

import argparse
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    from src.llm_client import LLMError, chat_client
except ImportError:  # running as a script from inside src/
    from llm_client import LLMError, chat_client

try:
    from src import writing_framework
except ImportError:  # running as a script from inside src/
    import writing_framework

logger = logging.getLogger(__name__)


def load_environment() -> None:
    """Load environment variables from the project root .env file if present."""
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()


def get_llm_client() -> Any:
    """Return the LLM gateway client (provider, model and keys come from the environment)."""
    return chat_client()


def load_json_file(path: Path) -> dict[str, Any]:
    """Load a JSON object from disk."""
    if not path.exists():
        raise FileNotFoundError(f"Required JSON file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must contain an object: {path}")

    return payload


def load_generated_letter(path: Path) -> str:
    """Read the generated cover letter from disk."""
    if not path.exists():
        raise FileNotFoundError(f"Generated cover letter not found: {path}")

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Generated cover letter file is empty: {path}")

    return content


def read_job_description(path: Path) -> str:
    """Read the current job description text."""
    if not path.exists():
        raise FileNotFoundError(f"Job description file not found: {path}")

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Job description file is empty: {path}")

    return content


def load_candidate_evidence(extracted_dir: Path) -> list[str]:
    """Read relevant cover-letter evidence from the extracted_letters directory."""
    if not extracted_dir.exists():
        raise FileNotFoundError(f"Extracted letters directory not found: {extracted_dir}")

    files = sorted(extracted_dir.glob("*.txt"))
    if not files:
        raise ValueError(f"No extracted cover letters were found in {extracted_dir}.")

    records: list[str] = []
    for file_path in files:
        content = file_path.read_text(encoding="utf-8").strip()
        if content:
            records.append(content)

    if not records:
        raise ValueError(f"No usable extracted letters were found in {extracted_dir}.")

    return records


def parse_selected_anchors(raw_value: str | None, anchor_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve the human-selected anchors from CLI input against the anchor payload."""
    anchors = anchor_payload.get("anchors")
    if not isinstance(anchors, list):
        raise ValueError("company_anchors.json does not contain an anchors list.")

    if raw_value is None or not raw_value.strip():
        return anchors

    selected: list[dict[str, Any]] = []
    for part in raw_value.split(","):
        text = part.strip()
        if not text:
            continue
        if not text.isdigit():
            raise ValueError(f"Invalid anchor selection: {text}")
        index = int(text)
        if index < 1 or index > len(anchors):
            raise ValueError(f"Anchor number out of range: {index}")
        anchor = anchors[index - 1]
        if anchor not in selected:
            selected.append(anchor)

    if not selected:
        raise ValueError("No valid anchors were selected for evaluation.")

    return selected


def deterministic_checks(
    letter_text: str,
    job_description: str,
    selected_anchors: list[dict[str, Any]],
    style_profile: dict[str, Any],
    candidate_evidence: list[str],
    company_url: str,
) -> list[str]:
    """Perform deterministic validation checks before LLM review."""
    issues: list[str] = []

    if not letter_text or not letter_text.strip():
        issues.append("The cover letter file is empty.")
        return issues

    text = letter_text.strip()
    word_count = len(re.findall(r"\b\w+\b", text))
    if word_count < 200:
        issues.append("The cover letter is shorter than a credible application letter.")

    placeholders = [
        "[company name]",
        "[your name]",
        "[insert",
        "company name",
        "your name",
        "insert here",
        "example company",
    ]
    lowered = text.lower()
    if any(token in lowered for token in placeholders):
        issues.append("The cover letter still contains placeholder language.")

    if company_url and company_url.lower().strip() not in lowered:
        # This is intentionally conservative; not every letter mentions the URL verbatim.
        pass

    if any(term in lowered for term in ["i am passionate about ai", "i would be a great addition", "i am excited to contribute to your innovative organization"]):
        issues.append("The letter contains generic enthusiasm that is not backed by clear evidence.")

    anchor_titles = [str(anchor.get("title", "")).lower() for anchor in selected_anchors if isinstance(anchor, dict)]
    if anchor_titles and not any(title in lowered for title in anchor_titles if title):
        issues.append("The selected anchor(s) do not appear to be reflected in the letter.")

    jd_lower = job_description.lower()
    if len(jd_lower) > 0 and any(token in jd_lower for token in ["python", "ml", "machine learning", "ai", "scientist"]):
        if not any(token in lowered for token in ["python", "machine learning", "ml", "ai", "scientist"]):
            issues.append("The letter does not appear to reference the main technical themes from the job description.")

    if not candidate_evidence:
        issues.append("There is no candidate evidence available for factual grounding checks.")

    if not style_profile:
        issues.append("No style profile was provided for voice fidelity checks.")

    return sorted(set(issues))


def normalize_scores(payload: dict[str, Any]) -> dict[str, int]:
    """Normalize the LLM-produced scores into numeric values between 0 and 10."""
    defaults = {
        "factual_grounding": 0,
        "job_relevance": 0,
        "company_specificity": 0,
        "selected_angle_usage": 0,
        "persuasiveness": 0,
        "voice_fidelity": 0,
        "human_likeness": 0,
    }
    scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
    for key, value in defaults.items():
        raw_value = scores.get(key, value)
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError):
            numeric = 0
        defaults[key] = max(0, min(10, int(round(numeric))))
    return defaults


def build_evaluation_prompt(
    letter_text: str,
    job_description: str,
    selected_anchors: list[dict[str, Any]],
    style_profile: dict[str, Any],
    candidate_evidence: list[str],
    company_url: str,
) -> str:
    """Assemble the evaluation prompt for Groq."""
    selected_anchor_details = []
    for index, anchor in enumerate(selected_anchors, start=1):
        if not isinstance(anchor, dict):
            continue
        selected_anchor_details.append(
            f"--- Anchor {index} ---\n"
            f"Title: {anchor.get('title', '')}\n"
            f"Company evidence: {anchor.get('company_evidence', '')}\n"
            f"Job connection: {anchor.get('job_connection', '')}\n"
            f"Candidate evidence: {anchor.get('candidate_evidence', '')}\n"
            f"Anchor statement: {anchor.get('anchor', '')}\n"
        )

    evidence_blob = "\n\n---\n\n".join(candidate_evidence[:6])
    style_blob = json.dumps(style_profile, ensure_ascii=False, indent=2)
    anchors_blob = "\n\n".join(selected_anchor_details) if selected_anchor_details else "No selected anchors were provided."
    framework_section = writing_framework.evaluation_section()  # writing framework

    return f"""
You are evaluating a generated cover letter for a specific company and role.

Your task is to judge whether the letter makes a credible, evidence-based case for why the candidate is relevant to the role and company, while still sounding like a real human wrote it.

Evaluate the document against the following reasoning chain:
Company need → Job requirement → Candidate evidence → Relevant capability → Why it matters for this company/role

Use the source material carefully. Do not assume a claim is true just because it sounds plausible.

Relevant source material:

1. Generated cover letter
{letter_text}

2. Job description
{job_description}

3. Selected anchors chosen by the human user
{anchors_blob}

4. Style profile
{style_blob}

5. Candidate evidence from previous cover letters
{evidence_blob}

6. Company URL
{company_url}

Evaluation dimensions:
1. Factual grounding: no invented experience, metrics, projects, technologies, achievements, or responsibilities.
2. Job relevance: link actual candidate experience to meaningful job requirements.
3. Company specificity: the letter should feel relevant to this company, not easily reusable elsewhere.
4. Selected-angle usage: check whether the human-selected angle is present, relevant, supported, and naturally integrated.
5. Persuasiveness: the case must be built from evidence, not generic enthusiasm.
6. Voice fidelity: preserve the candidate's existing tone, formality, paragraph structure, and writing style.
7. Human-likeness: sound natural, credible, and consistent with the candidate's established voice.

Scoring scale:
- 0 = unacceptable / clearly unsupported
- 2 = weak / inconsistent
- 4 = mixed / partial
- 6 = acceptable / mostly aligned
- 8 = strong / credible and well aligned
- 10 = excellent / fully meets the requirement

Important rules:
- Do not reward generic enthusiasm such as “I am passionate about AI” or “I would be a great addition.”
- Do not assume a claim is true without support from the evidence.
- Do not create a ranking or subjective comparison between candidates.
- Return ONLY valid JSON in the exact shape below.

{framework_section}

JSON shape:
{{
  "overall_status": "PASS | NEEDS_REVISION",
  "scores": {{
    "factual_grounding": 0,
    "job_relevance": 0,
    "company_specificity": 0,
    "selected_angle_usage": 0,
    "persuasiveness": 0,
    "voice_fidelity": 0,
    "human_likeness": 0
  }},
  "strengths": ["..."],
  "issues": ["..."],
  "unsupported_claims": ["..."],
  "generic_or_ai_like_phrases": ["..."],
  "revision_suggestions": ["..."]
}}
""".strip()


def evaluate_cover_letter(
    letter_text: str,
    job_description: str,
    selected_anchors: list[dict[str, Any]],
    style_profile: dict[str, Any],
    candidate_evidence: list[str],
    company_url: str,
) -> dict[str, Any]:
    """Perform deterministic and LLM-based quality-control evaluation."""
    if not isinstance(selected_anchors, list):
        selected_anchors = []

    deterministic_issues = deterministic_checks(
        letter_text=letter_text,
        job_description=job_description,
        selected_anchors=selected_anchors,
        style_profile=style_profile,
        candidate_evidence=candidate_evidence,
        company_url=company_url,
    )

    load_environment()
    client = get_llm_client()
    prompt = build_evaluation_prompt(
        letter_text=letter_text,
        job_description=job_description,
        selected_anchors=selected_anchors,
        style_profile=style_profile,
        candidate_evidence=candidate_evidence,
        company_url=company_url,
    )

    try:
        response = client.chat.completions.create(
            step="evaluation",
            messages=[
                {
                    "role": "system",
                    "content": "You evaluate whether a cover letter is evidence-based, role-specific, company-specific, human-sounding, and faithful to the candidate's writing voice. Return ONLY valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=2000,
        )
    except LLMError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Unexpected Groq error: {exc}") from exc

    raw_content = response.choices[0].message.content
    if raw_content is None or not raw_content.strip():
        raise ValueError("Groq returned an empty response body.")

    cleaned = raw_content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Groq returned invalid JSON content (%d characters).", len(raw_content))
        raise ValueError("Groq returned invalid JSON content.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Groq returned a non-object payload.")

    normalized_scores = normalize_scores(payload)
    final_payload: dict[str, Any] = {
        "overall_status": str(payload.get("overall_status", "NEEDS_REVISION")).upper(),
        "scores": normalized_scores,
        "strengths": payload.get("strengths", []) if isinstance(payload.get("strengths"), list) else [],
        "issues": payload.get("issues", []) if isinstance(payload.get("issues"), list) else [],
        "unsupported_claims": payload.get("unsupported_claims", []) if isinstance(payload.get("unsupported_claims"), list) else [],
        "generic_or_ai_like_phrases": payload.get("generic_or_ai_like_phrases", []) if isinstance(payload.get("generic_or_ai_like_phrases"), list) else [],
        "revision_suggestions": payload.get("revision_suggestions", []) if isinstance(payload.get("revision_suggestions"), list) else [],
    }

    final_payload["issues"] = sorted(set(deterministic_issues + [str(item) for item in final_payload["issues"]]))
    final_payload["unsupported_claims"] = sorted(set([str(item) for item in final_payload["unsupported_claims"]]))
    final_payload["generic_or_ai_like_phrases"] = sorted(set([str(item) for item in final_payload["generic_or_ai_like_phrases"]]))
    final_payload["revision_suggestions"] = sorted(set([str(item) for item in final_payload["revision_suggestions"]]))
    final_payload["strengths"] = sorted(set([str(item) for item in final_payload["strengths"]]))

    # writing framework: rule-based checks plus the LLM's framework_issues; absent when the framework is disabled.
    framework_issues: list[str] = []
    if writing_framework.FRAMEWORK_ENABLED:
        llm_framework_issues = payload.get("framework_issues") if isinstance(payload.get("framework_issues"), list) else []
        framework_issues = sorted(set(writing_framework.deterministic_issues(letter_text) + [str(item) for item in llm_framework_issues]))
        final_payload["framework_issues"] = framework_issues

    average_score = sum(final_payload["scores"].values()) / len(final_payload["scores"]) if final_payload["scores"] else 0
    if average_score >= 7 and not final_payload["issues"] and not final_payload["unsupported_claims"] and not framework_issues:
        final_payload["overall_status"] = "PASS"
    else:
        final_payload["overall_status"] = "NEEDS_REVISION"

    return final_payload


def save_evaluation(path: Path, payload: dict[str, Any]) -> None:
    """Persist the evaluation report to disk."""
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def print_summary(payload: dict[str, Any]) -> None:
    """Print an evaluation summary to the terminal in the requested format."""
    scores = payload.get("scores", {})
    print("\n---")
    print("## COVER LETTER EVALUATION")
    print(f"Status: {payload.get('overall_status', 'NEEDS_REVISION')}")
    print(f"Factual grounding: {scores.get('factual_grounding', 0)}")
    print(f"Job relevance: {scores.get('job_relevance', 0)}")
    print(f"Company specificity: {scores.get('company_specificity', 0)}")
    print(f"Selected angle: {scores.get('selected_angle_usage', 0)}")
    print(f"Persuasiveness: {scores.get('persuasiveness', 0)}")
    print(f"Voice fidelity: {scores.get('voice_fidelity', 0)}")
    print(f"Human-likeness: {scores.get('human_likeness', 0)}")
    print("")
    print("Issues:")
    issues = payload.get("issues", [])
    if issues:
        for issue in issues:
            print(f"- {issue}")
    else:
        print("- None")

    print("")
    print("Unsupported claims:")
    unsupported = payload.get("unsupported_claims", [])
    if unsupported:
        for claim in unsupported:
            print(f"- {claim}")
    else:
        print("- None")

    print("")
    print("Revision suggestions:")
    suggestions = payload.get("revision_suggestions", [])
    if suggestions:
        for suggestion in suggestions:
            print(f"- {suggestion}")
    else:
        print("- None")
    print("---")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        parser = argparse.ArgumentParser(description="Evaluate a generated cover letter against the job description, selected anchors, style profile, and candidate evidence.")
        parser.add_argument("--letter", default="generated_cover_letter.txt", help="Path to the generated cover-letter file to evaluate.")
        parser.add_argument("--jd", default="job_description.txt", help="Path to the current job description file.")
        parser.add_argument("--anchors", default="company_anchors.json", help="Path to the company anchors JSON file.")
        parser.add_argument("--style", default="style_profile.json", help="Path to the style profile JSON file.")
        parser.add_argument("--selected-anchors", default=None, help="Comma-separated selected anchor numbers, e.g. 1,3.")
        parser.add_argument("--output", default="cover_letter_evaluation.json", help="Path to write the JSON evaluation output.")
        args = parser.parse_args()

        project_root = Path(__file__).resolve().parent.parent
        letter_path = (Path(args.letter).resolve() if Path(args.letter).is_absolute() else (project_root / args.letter)).resolve()
        jd_path = (Path(args.jd).resolve() if Path(args.jd).is_absolute() else (project_root / args.jd)).resolve()
        anchors_path = (Path(args.anchors).resolve() if Path(args.anchors).is_absolute() else (project_root / args.anchors)).resolve()
        style_path = (Path(args.style).resolve() if Path(args.style).is_absolute() else (project_root / args.style)).resolve()
        output_path = (Path(args.output).resolve() if Path(args.output).is_absolute() else (project_root / args.output)).resolve()

        letter_text = load_generated_letter(letter_path)
        job_description = read_job_description(jd_path)
        anchor_payload = load_json_file(anchors_path)
        style_profile = load_json_file(style_path)
        evidence = load_candidate_evidence(project_root / "extracted_letters")
        selected_anchors = parse_selected_anchors(args.selected_anchors, anchor_payload)
        company_url = str(anchor_payload.get("company_url") or "")

        result = evaluate_cover_letter(
            letter_text=letter_text,
            job_description=job_description,
            selected_anchors=selected_anchors,
            style_profile=style_profile,
            candidate_evidence=evidence,
            company_url=company_url,
        )

        save_evaluation(output_path, result)
        print_summary(result)
        print("\nSaved to:")
        print(output_path)
        return 0
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:  # pragma: no cover - safety net
        logger.exception("Unexpected failure while evaluating the cover letter: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
