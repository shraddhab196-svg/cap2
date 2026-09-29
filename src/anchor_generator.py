from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import APIConnectionError, APIStatusError, Groq, RateLimitError

from company_researcher import research_company

logger = logging.getLogger(__name__)


class AnchorOutputError(ValueError):
    """Raised when the LLM JSON is missing or malformed."""


def load_environment() -> None:
    """Load environment variables from the project root .env file if present."""
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()


def get_groq_client() -> Groq:
    """Return a configured Groq client or raise a clear error if the API key is missing."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or not api_key.strip():
        raise ValueError("Missing GROQ_API_KEY. Add it to your .env file before running the analyzer.")
    model_name = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    return Groq(api_key=api_key), model_name


def read_job_description_from_file(project_root: Path) -> str:
    """Read the current job description from a temporary local file for testing."""
    job_path = project_root / "job_description.txt"
    if not job_path.exists():
        raise FileNotFoundError("job_description.txt not found. Please create it in the project root and paste the current Job Description into it.")

    content = job_path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError("job_description.txt is empty. Please paste the current Job Description into it.")

    return content


def load_candidate_letters(extracted_dir: Path) -> list[tuple[str, str]]:
    """Read all previous cover letters from extracted_letters."""
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
        raise ValueError("No usable candidate evidence was found in the extracted cover letters.")

    return letters


def build_anchor_prompt(company_url: str, job_description: str, company_research: str, letters: list[tuple[str, str]]) -> str:
    """Create the prompt for anchor generation."""
    letter_sections: list[str] = []
    for index, (name, text) in enumerate(letters, start=1):
        snippet = text.strip()
        if len(snippet) > 5000:
            snippet = snippet[:5000] + "\n... [truncated for analysis]"
        letter_sections.append(f"--- LETTER {index} ({name}) ---\n{snippet}\n")

    return f"""
You are identifying evidence-based, personalized anchors that connect a company, a job, and the candidate's prior experience.

Core task:
Find 3 to 5 materially different anchors when the evidence supports them. The anchors must connect:
- a specific company fact,
- a specific job requirement or responsibility,
- specific candidate evidence from the previous cover letters.

Anchor definition:
An anchor is not a generic statement about liking the company or being passionate about AI. It is a concrete, evidence-backed connection that could later be used as a foundation for a cover letter.

Hard rules:
- Use only information present in the company research, job description, and the candidate's previous cover letters.
- Do not invent company facts, candidate skills, achievements, technologies, projects, or responsibilities.
- Do not write a final cover letter.
- Do not produce generic statements such as "I am passionate about your company," "I admire your mission," or "I would love to work here."
- Do not generate duplicate anchors that just restate the same connection in a different way.
- Each anchor must be materially different from the others.
- If evidence is weak or missing for a dimension, do not force it. Return only the genuinely supported ones.
- If there are fewer than 3 strong anchors, return only the ones with real support.

Search across multiple distinct dimensions of evidence before deciding the final set.
Examine the candidate's letters for evidence across, where present:
1. technical skills
2. relevant project experience
3. industry or domain experience
4. problem-solving or analytical experience
5. deployment or production experience
6. collaboration or stakeholder communication experience
7. business impact or measurable outcomes
8. technologies mentioned in the JD
9. responsibilities described in the JD
10. company-specific initiatives, products, or ways of working

Only include a dimension if there is specific evidence in the candidate's letters and a real connection to the company/job.

Before finalizing, compare the candidate's evidence across all 8 letters and ensure the anchors are distinct.
Do not rely on only the first relevant letter.
Do not copypaste the same theme under different labels.

Company URL:
{company_url}

Company research:
{company_research}

Job description:
{job_description}

Candidate previous cover letters:
{''.join(letter_sections)}

Return valid JSON with this structure:
{{
  "company_url": "https://example.com",
  "anchors": [
    {{
      "title": "short title",
      "company_evidence": "specific fact from the company website",
      "job_connection": "why it matters for the role",
      "candidate_evidence": "specific evidence from the candidate's letters",
      "anchor": "the personalized connection between company + job + candidate",
      "source_url": "https://example.com"
    }},
    {{
      "title": "another distinct short title",
      "company_evidence": "another specific fact from the company website",
      "job_connection": "why it matters for the role",
      "candidate_evidence": "another specific evidence point from the candidate's letters",
      "anchor": "a different personalized connection between company + job + candidate",
      "source_url": "https://example.com"
    }}
  ]
}}

Quality bar:
- 3 to 5 distinct anchors when genuinely supported by the evidence.
- Different anchors should usually correspond to different evidence dimensions (for example: manufacturing ML pipeline, deployment experience, stakeholder communication, business impact, or technology stack alignment).
- Each anchor must be specific and evidence-based.
- Use clean JSON only.
""".strip()


def generate_anchors(company_url: str, job_description: str, company_research: str, letters: list[tuple[str, str]]) -> dict[str, Any]:
    """Call Groq to generate the anchor set in valid JSON."""
    load_environment()
    client, model_name = get_groq_client()
    prompt = build_anchor_prompt(company_url, job_description, company_research, letters)

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "system",
                    "content": "You generate evidence-based anchors that map company context, job needs, and candidate experience. Return only valid JSON with an 'anchors' array.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=2000,
        )
    except (APIConnectionError, APIStatusError, RateLimitError) as exc:
        raise RuntimeError(f"Groq API request failed: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Unexpected Groq error: {exc}") from exc

    raw_content = response.choices[0].message.content
    if raw_content is None or not raw_content.strip():
        raise AnchorOutputError("Groq returned an empty response body.")

    cleaned_content = raw_content.strip()
    if cleaned_content.startswith("```"):
        cleaned_content = re.sub(r"^```(?:json)?\s*", "", cleaned_content, flags=re.IGNORECASE)
        cleaned_content = re.sub(r"\s*```$", "", cleaned_content, flags=re.IGNORECASE)
        cleaned_content = cleaned_content.strip()

    try:
        payload = json.loads(cleaned_content)
    except json.JSONDecodeError as exc:
        logger.error("ERROR: Groq returned invalid JSON content.\nRAW GROQ RESPONSE:\n%s", raw_content)
        raise AnchorOutputError("Groq returned invalid JSON content.") from exc

    if not isinstance(payload, dict) or "anchors" not in payload:
        logger.error("ERROR: Groq returned invalid JSON content.\nRAW GROQ RESPONSE:\n%s", raw_content)
        raise AnchorOutputError("Groq response did not contain the required anchors structure.")

    anchors = payload.get("anchors")
    if not isinstance(anchors, list):
        logger.error("ERROR: Groq returned invalid JSON content.\nRAW GROQ RESPONSE:\n%s", raw_content)
        raise AnchorOutputError("Groq returned an invalid anchors list.")

    required_fields = {"title", "company_evidence", "job_connection", "candidate_evidence", "anchor", "source_url"}
    for anchor in anchors:
        if not isinstance(anchor, dict):
            logger.error("ERROR: Groq returned invalid JSON content.\nRAW GROQ RESPONSE:\n%s", raw_content)
            raise AnchorOutputError("Every anchor must be a JSON object.")
        missing_fields = sorted(required_fields - set(anchor.keys()))
        if missing_fields:
            logger.error("ERROR: Groq returned invalid JSON content.\nRAW GROQ RESPONSE:\n%s", raw_content)
            raise AnchorOutputError(f"Each anchor must include the required fields: {missing_fields}")

    print("\nRAW/GENERATED ANCHORS:")
    print("--------------------------------")
    for index, anchor in enumerate(anchors, start=1):
        print(f"Anchor {index}:")
        print(json.dumps(anchor, indent=2, ensure_ascii=False))
        print()
    print("--------------------------------")
    print(f"Number of anchors returned: {len(anchors)}")

    useful_anchors: list[dict[str, Any]] = []
    for index, anchor in enumerate(anchors, start=1):
        reasons: list[str] = []
        if not isinstance(anchor, dict):
            reasons.append("anchor was not an object")
        for field in ["title", "company_evidence", "job_connection", "candidate_evidence", "anchor", "source_url"]:
            value = anchor.get(field) if isinstance(anchor, dict) else None
            if value is None or (isinstance(value, str) and not value.strip()):
                reasons.append(f"{field} was empty")
        if not reasons:
            useful_anchors.append(anchor)
            print(f"Anchor {index} accepted")
        else:
            print(f"Anchor {index} rejected:")
            for reason in reasons:
                print(f"- {reason}")

    print(f"Number considered useful: {len(useful_anchors)}")

    if len(useful_anchors) < 3:
        raise ValueError("Fewer than 3 useful anchors were generated.")

    payload["company_url"] = company_url
    payload["anchors"] = useful_anchors[:5]
    return payload


def save_json(path: Path, payload: dict[str, Any]) -> None:
    """Write the anchor JSON file to disk."""
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        project_root = Path(__file__).resolve().parent.parent

        company_url = input("Enter company URL: ").strip()
        print("Reading Job Description from job_description.txt...")
        job_description = read_job_description_from_file(project_root)

        print("\nResearching company...")
        company_data = research_company(company_url)

        print("\nLoading previous cover letters...")
        letters = load_candidate_letters(project_root / "extracted_letters")
        print(f"Loaded {len(letters)} cover letters.")

        print("\nFinding personalized anchors...")
        anchors = generate_anchors(
            company_url=company_url,
            job_description=job_description,
            company_research=company_data["company_research"],
            letters=letters,
        )

        output_path = project_root / "company_anchors.json"
        save_json(output_path, anchors)

        print("\nGenerated 3-5 personalized anchors.")
        print("\nSaved to company_anchors.json")
        return 0
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    except TimeoutError as exc:
        logger.error("%s", exc)
        return 1
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:  # pragma: no cover - safety net
        logger.exception("Unexpected failure: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
