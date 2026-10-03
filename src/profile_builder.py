from __future__ import annotations

import json
import re
from typing import Any, Iterable, Sequence

from fastapi import UploadFile

MIN_COVER_LETTERS = 1
MAX_COVER_LETTERS = 5

SKILL_KEYWORDS = {
    "python": ["python", "django", "flask", "fastapi", "pandas", "numpy", "sqlalchemy"],
    "javascript": ["javascript", "typescript", "react", "node", "next", "vue", "express"],
    "cloud": ["aws", "azure", "gcp", "docker", "kubernetes", "terraform", "cloud"],
    "data": ["sql", "postgres", "postgresql", "data analysis", "power bi", "tableau", "etl"],
    "leadership": ["mentor", "led", "managed", "coordinated", "owned", "sponsored"],
    "product": ["product strategy", "roadmap", "customer discovery", "stakeholder management"],
}


def read_uploaded_text(file: UploadFile) -> str:
    """Read uploaded text from a resume or cover-letter file."""
    if file is None:
        return ""
    filename = (file.filename or "").lower()
    if filename.endswith(".pdf"):
        from pathlib import Path

        from src.pdf_extractor import extract_pdf_text

        temp_path = Path(file.filename or "temp.pdf")
        temp_path.write_bytes(file.file.read())
        try:
            return extract_pdf_text(temp_path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    content = file.file.read()
    return content.decode("utf-8", errors="replace")


def validate_cover_letters(files: Sequence[UploadFile], min_count: int = MIN_COVER_LETTERS) -> list[str]:
    """Validate uploaded cover-letter files for a given minimum count."""
    if files is None:
        raise ValueError("No cover letters were uploaded.")

    cleaned = [
        (file.filename or "").strip()
        for file in files
        if (file.filename or "").strip()
    ]
    letter_word = "letter" if min_count == 1 else "letters"
    if not cleaned:
        raise ValueError(f"Please upload at least {min_count} previous cover {letter_word}.")
    if len(cleaned) < min_count:
        raise ValueError(f"Please upload at least {min_count} previous cover {letter_word}.")
    if len(cleaned) > MAX_COVER_LETTERS:
        raise ValueError(f"You can upload a maximum of {MAX_COVER_LETTERS} previous cover letters.")

    return cleaned


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def extract_sentences(text: str) -> list[str]:
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def grounded_candidates_from_text(text: str) -> dict[str, list[str]]:
    """Extract only facts explicitly supported by the uploaded documents."""
    sentences = extract_sentences(text)
    response = {
        "skills": [],
        "experience": [],
        "tools": [],
        "achievements": [],
        "education": [],
    }

    for sentence in sentences:
        lower = sentence.lower()
        if any(word in lower for word in ["led", "managed", "built", "designed", "owned", "delivered", "implemented", "developed", "worked on"]):
            response["experience"].append(sentence)
        if any(word in lower for word in ["python", "sql", "aws", "azure", "gcp", "docker", "kubernetes", "react", "typescript", "javascript", "postgres", "postgresql"]):
            response["tools"].append(sentence)
        if any(token in lower for token in ["bachelor", "master", "degree", "university", "college", "certification", "certified"]):
            response["education"].append(sentence)
        if re.search(r"\b\d+%\b|\b\d+\s*(x|times)\b|\b\d+\s*(months|years)\b|\b\d+\+\b", sentence, re.IGNORECASE):
            response["achievements"].append(sentence)

        for skill, keywords in SKILL_KEYWORDS.items():
            if any(keyword in lower for keyword in keywords):
                response["skills"].append(skill.title())
                break

    for key in response:
        if key == "skills":
            response[key] = sorted(set(response[key]))
        else:
            response[key] = response[key][:8]

    return response


def build_candidate_evidence(resume_text: str, previous_letters: Iterable[str]) -> dict[str, Any]:
    """Build grounded candidate evidence from the resume and prior cover letters only."""
    if not resume_text or not resume_text.strip():
        raise ValueError("Please upload your resume before building your profile.")

    letters = [normalize_text(letter) for letter in previous_letters if letter and letter.strip()]
    if len(letters) < MIN_COVER_LETTERS:
        raise ValueError(f"Please upload at least {MIN_COVER_LETTERS} previous cover letter.")

    combined_text = "\n".join([normalize_text(resume_text)] + letters)
    evidence = grounded_candidates_from_text(combined_text)

    skills = evidence["skills"]
    tools = evidence["tools"]
    experience = evidence["experience"]
    achievements = evidence["achievements"]
    education = evidence["education"]

    metrics = []
    for sentence in achievements:
        metric_matches = re.findall(r"\b\d+%\b|\b\d+\s*(x|times)\b|\b\d+\s*(months|years)\b|\b\d+\+\b", sentence, flags=re.IGNORECASE)
        metrics.extend(metric_matches)
    metrics = sorted(set(metrics))[:10]

    return {
        "skills": skills,
        "experience": experience,
        "tools": tools,
        "achievements": achievements,
        "education": education,
        "metrics": metrics,
        "grounding": "Grounded in the uploaded resume and previous cover letters only.",
    }


def build_style_profile(previous_letters: Iterable[str]) -> dict[str, Any]:
    """Build a writing-style profile based on recurring patterns across 1–5 letters."""
    texts = [normalize_text(letter) for letter in previous_letters if letter and letter.strip()]
    if len(texts) < MIN_COVER_LETTERS:
        raise ValueError(f"Please upload at least {MIN_COVER_LETTERS} previous cover letter to analyze writing style.")
    if len(texts) > MAX_COVER_LETTERS:
        raise ValueError(f"You can upload a maximum of {MAX_COVER_LETTERS} previous cover letters.")

    combined = " ".join(texts)
    lower = combined.lower()

    tone = "professional, confident, and concise" if any(term in lower for term in ["results", "impact", "delivered", "improved"]) else "professional and clear"
    sentence_structure = "medium-length sentences with clear action and evidence" if len(texts) >= 2 else "clear, direct sentences"
    vocabulary = "balanced technical vocabulary with practical business framing" if any(term in lower for term in ["product", "customer", "team", "delivery", "system"]) else "clear technical vocabulary with direct business language"
    detail_level = "moderate detail, focused on outcomes and evidence" if re.search(r"\b\d+%\b|\b\d+\s*(x|times)\b", combined, flags=re.IGNORECASE) else "concise but specific detail"
    technical_terminology = "strong technical vocabulary when relevant to the role" if any(term in lower for term in ["python", "aws", "kubernetes", "sql", "react", "docker"]) else "selective technical terminology aligned to the role"
    metrics_usage = "metrics are used selectively to reinforce impact" if re.search(r"\b\d+%\b|\b\d+\s*(x|times)\b", combined, flags=re.IGNORECASE) else "metrics are used sparingly and only when clearly grounded"
    personalization_style = "tailored to the target role, company, and problem space" if any(term in lower for term in ["company", "team", "role", "mission", "challenge"]) else "positioned around relevant role fit and evidence"
    achievement_presentation = "achievements are framed around scope, ownership, and measurable outcomes" if re.search(r"\b(led|owned|built|delivered|improved)\b", combined, flags=re.IGNORECASE) else "achievements are grounded in concrete delivery examples"
    paragraph_structure = "clear opening, evidence-based body, and concise close" if len(texts) >= 2 else "direct narrative structure"
    opening_style = "starts with contribution and role relevance" if any(term in lower for term in ["i am", "with", "in", "role"]) else "opens with the candidate's fit and contribution"
    closing_style = "closes by reinforcing fit, value, and readiness" if any(term in lower for term in ["thank", "grateful", "excited", "eager"]) else "closes with a concise value-focused summary"

    return {
        "tone": tone,
        "sentence_structure": sentence_structure,
        "vocabulary": vocabulary,
        "detail_level": detail_level,
        "technical_terminology": technical_terminology,
        "use_of_metrics": metrics_usage,
        "personalization_style": personalization_style,
        "achievement_presentation": achievement_presentation,
        "paragraph_structure": paragraph_structure,
        "opening_style": opening_style,
        "closing_style": closing_style,
        "patterns_to_avoid": ["generic filler", "unsupported claims", "vague buzzwords", "overly abstract language"],
        "grounding": "Inferred from recurring patterns across the uploaded previous cover letters only.",
    }


def build_professional_profile(candidate_evidence: dict[str, Any], style_profile: dict[str, Any]) -> dict[str, Any]:
    """Ground the professional summary in candidate evidence instead of generic filler."""
    skills = candidate_evidence.get("skills", []) or []
    tools = candidate_evidence.get("tools", []) or []
    experience = candidate_evidence.get("experience", []) or []
    achievements = candidate_evidence.get("achievements", []) or []
    education = candidate_evidence.get("education", []) or []

    core_strengths: list[str] = []
    if skills:
        core_strengths.append(f"core skills across {', '.join(skills[:3])}")
    if tools:
        tool_names = ", ".join(re.sub(r"\s+", " ", str(item)) for item in tools[:3])
        core_strengths.append(f"tooling experience including {tool_names}")
    if experience:
        core_strengths.append("clear experience leading delivery, implementation, or ownership")
    if achievements:
        core_strengths.append("evidence of measurable output and achievement")

    professional_background = []
    if education:
        professional_background.extend(education[:2])
    if experience:
        professional_background.extend(experience[:2])
    if not professional_background:
        professional_background = ["Professional profile built from the uploaded resume and prior cover letters."]

    return {
        "professional_background": professional_background[:3],
        "core_strengths": core_strengths[:5],
        "technical_capabilities": skills[:6],
        "experience_themes": experience[:3],
        "achievement_themes": achievements[:3],
        "writing_style_summary": style_profile.get("tone", "professional"),
        "grounding": "Grounded in the uploaded resume and previous cover letters only.",
    }


def build_profile_bundle(resume_text: str, previous_cover_letters: Iterable[str]) -> dict[str, Any]:
    letters = [normalize_text(letter) for letter in previous_cover_letters if letter and letter.strip()]
    if not resume_text or not resume_text.strip():
        raise ValueError("Please upload your resume before building your profile.")
    if len(letters) < MIN_COVER_LETTERS:
        raise ValueError(f"Please upload at least {MIN_COVER_LETTERS} previous cover letter.")
    if len(letters) > MAX_COVER_LETTERS:
        raise ValueError(f"You can upload a maximum of {MAX_COVER_LETTERS} previous cover letters.")

    candidate_evidence = build_candidate_evidence(resume_text, letters)
    style_profile = build_style_profile(letters)
    professional_profile = build_professional_profile(candidate_evidence, style_profile)

    return {
        "candidate_evidence": candidate_evidence,
        "writing_style_profile": style_profile,
        "professional_profile": professional_profile,
    }
