from __future__ import annotations

from src.profile_builder import build_candidate_evidence, build_profile_bundle, validate_cover_letters


def test_validate_cover_letters_accepts_valid_uploads():
    files = [
        type("File", (), {"filename": "letter1.txt"})(),
        type("File", (), {"filename": "letter2.txt"})(),
        type("File", (), {"filename": "letter3.txt"})(),
    ]

    result = validate_cover_letters(files)

    assert result == ["letter1.txt", "letter2.txt", "letter3.txt"]


def test_validate_cover_letters_rejects_invalid_counts():
    zero = []
    single = [type("File", (), {"filename": "letter1.txt"})()]
    too_many = [type("File", (), {"filename": f"letter{i}.txt"})() for i in range(1, 7)]

    for files in (zero, single, too_many):
        try:
            validate_cover_letters(files)
            raise AssertionError("Expected invalid cover-letter count to be rejected.")
        except ValueError:
            pass


def test_build_candidate_evidence_extracts_grounded_facts():
    resume_text = """
    Senior Product Engineer with 5 years of experience in Python, Go, and cloud systems.
    Led a migration to AWS and improved deployment speed by 40%.
    BS in Computer Science, University of Washington.
    """
    letters = [
        "I built APIs in Python and Kubernetes for a fintech platform. I shipped a fraud detection service that reduced false positives by 25%.",
        "I partnered closely with stakeholders to deliver a new analytics platform using Postgres and React.",
    ]

    evidence = build_candidate_evidence(resume_text, letters)

    assert evidence["skills"]
    assert any("python" in skill.lower() for skill in evidence["skills"])
    assert any("aws" in item.lower() for item in evidence["experience"] + evidence["tools"] + evidence["achievements"]) or "aws" in " ".join(evidence["tools"]).lower()
    assert evidence["grounding"] == "Grounded in the uploaded resume and previous cover letters only."


def test_build_profile_bundle_requires_resume_and_two_letters():
    try:
        build_profile_bundle("", ["letter 1", "letter 2"])
        raise AssertionError("Resume was required.")
    except ValueError:
        pass

    try:
        build_profile_bundle("resume text", ["single letter"])
        raise AssertionError("Two cover letters were required.")
    except ValueError:
        pass

    bundle = build_profile_bundle("Senior software engineer with Python and AWS experience.", ["I led a Python migration and improved deployment speed by 40%.", "I built SQL-based reporting with strong stakeholder communication and clear delivery."])
    assert "candidate_evidence" in bundle
    assert "writing_style_profile" in bundle
    assert "professional_profile" in bundle
