from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import APIConnectionError, APIStatusError, Groq, RateLimitError
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


class StyleProfile(BaseModel):
    model_config = {
        "extra": "forbid",
    }

    tone: str = Field(description="Overall tone expressed across the cover letters.")
    english_variant: str = Field(description="English variant most consistently used.")
    sentence_length_and_complexity: str = Field(description="Typical sentence length and complexity.")
    active_passive_voice: str = Field(description="Pattern of active vs passive voice usage.")
    paragraph_structure: str = Field(description="How the candidate structures paragraphs and transitions.")
    formality: str = Field(description="Overall formality level and style register.")
    personalization: str = Field(description="How personalized the letters are to target companies and roles.")
    technical_depth: str = Field(description="Depth of technical detail and domain specificity.")
    evidence_metrics_projects_technologies: str = Field(description="Use of evidence, metrics, projects, and technologies.")
    connection_between_candidate_company_and_role: str = Field(description="How the candidate links themselves to the company and role.")
    gap_framing: str = Field(description="How gaps or transitions are framed in a strengths-oriented way.")
    conciseness: str = Field(description="How concise and efficient the writing is.")
    preferred_content: list[str] = Field(description="Topics the candidate consistently prefers to highlight.")
    content_language_to_avoid: list[str] = Field(description="Content or language patterns that should usually be avoided.")
    ai_like_patterns_to_avoid: list[str] = Field(description="AI-like style traits to avoid.")
    recurring_writing_patterns: list[str] = Field(description="Recurring patterns in the candidate's writing.")
    personal_voice: str = Field(description="The candidate's likely personal voice, inferred from recurring patterns rather than polished phrasing.")


def load_environment() -> None:
    """Load environment variables from the project root .env file if present."""
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()


def load_extracted_letters(extracted_dir: Path) -> list[tuple[str, str]]:
    """Read all .txt cover letters from the extracted_letters folder."""
    if not extracted_dir.exists():
        raise FileNotFoundError(f"Extracted letters directory not found: {extracted_dir}")

    letter_files = sorted(extracted_dir.glob("*.txt"))
    if not letter_files:
        raise ValueError(f"No extracted cover-letter text files were found in {extracted_dir}.")

    letters: list[tuple[str, str]] = []
    for letter_path in letter_files:
        content = letter_path.read_text(encoding="utf-8").strip()
        if not content:
            logger.warning("Skipping empty text file: %s", letter_path.name)
            continue
        letters.append((letter_path.stem, content))

    if not letters:
        raise ValueError(f"All .txt files in {extracted_dir} were empty.")

    return letters


def build_analysis_prompt(letters: list[tuple[str, str]]) -> str:
    """Build a prompt that asks Groq to infer writing patterns across multiple letters."""
    combined_parts: list[str] = []
    for index, (filename, text) in enumerate(letters, start=1):
        snippet = text.strip()
        if len(snippet) > 8000:
            snippet = snippet[:8000] + "\n... [truncated for analysis]"
        combined_parts.append(f"--- LETTER {index} ({filename}) ---\n{snippet}\n")

    return f"""
You are analyzing a candidate's recurring cover-letter writing patterns across multiple letters.

Goals:
- Infer patterns that recur across the letters, not copy wording or phrases.
- Treat polished wording as possible marketing language, not necessarily the candidate's natural voice.
- Do not invent skills, experience, preferences, or achievements.
- For gap framing, identify genuine transferable strengths rather than making a list of weaknesses.
- Produce a profile useful for future cover-letter generation.

Requirements:
- Analyze the letters as a set, not one letter at a time.
- Focus on recurring patterns, tendencies, and likely preferences.
- Be precise and evidence-based.
- Keep the output concise but substantive.
- Use factual, non-speculative language.

Letters to analyze:

{''.join(combined_parts)}

Return a structured JSON profile with these fields:
- tone
- english_variant
- sentence_length_and_complexity
- active_passive_voice
- paragraph_structure
- formality
- personalization
- technical_depth
- evidence_metrics_projects_technologies
- connection_between_candidate_company_and_role
- gap_framing
- conciseness
- preferred_content
- content_language_to_avoid
- ai_like_patterns_to_avoid
- recurring_writing_patterns
- personal_voice

Each field should be a succinct but useful description. For list fields, use a few concrete bullet-like items as strings in an array.
""".strip()


def get_groq_client() -> Groq:
    """Return a configured Groq client or raise a clear error if the API key is missing."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or not api_key.strip():
        raise ValueError("Missing GROQ_API_KEY. Add it to your .env file before running the analyzer.")
    return Groq(api_key=api_key)


def analyze_letters_with_groq(letters: list[tuple[str, str]]) -> StyleProfile:
    """Send the combined letters to Groq and parse a structured StyleProfile response."""
    client = get_groq_client()
    prompt = build_analysis_prompt(letters)
    schema = StyleProfile.model_json_schema()

    try:
        response = client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"),
            messages=[
                {
                    "role": "system",
                    "content": "You analyze writing patterns across cover letters and return strict JSON in the required schema.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "style_profile",
                    "schema": schema,
                    "strict": True,
                },
            },
            temperature=0.2,
            max_tokens=2000,
        )
    except (APIConnectionError, APIStatusError, RateLimitError) as exc:
        raise RuntimeError(f"Groq API request failed: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Unexpected Groq error: {exc}") from exc

    raw_content = response.choices[0].message.content
    if raw_content is None or not raw_content.strip():
        raise ValueError("Groq returned an empty response body.")

    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise ValueError("Groq returned invalid JSON content.") from exc

    try:
        return StyleProfile.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"Groq returned a malformed structured response: {exc}") from exc


def save_style_profile(profile: StyleProfile, output_path: Path) -> None:
    """Persist the validated style profile as JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output = profile.model_dump(mode="json")
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info("Style profile saved to %s", output_path)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        load_environment()

        project_root = Path(__file__).resolve().parent.parent
        extracted_dir = project_root / "extracted_letters"
        output_path = project_root / "style_profile.json"

        letters = load_extracted_letters(extracted_dir)
        profile = analyze_letters_with_groq(letters)
        save_style_profile(profile, output_path)
        logger.info("Style analysis completed successfully.")
        return 0
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:  # pragma: no cover - safety net
        logger.exception("Unexpected analyzer failure: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
