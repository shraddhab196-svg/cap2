from pathlib import Path

from src.cover_letter_generator import build_cover_letter_plan_prompt


def test_cover_letter_plan_prompt_stays_under_budget():
    job_description = "Senior Forward Deployed AI Scientist role for BCG X. Need client-facing, operations, optimization, and analytics work."
    style_profile = {
        "voice": "direct, evidence-driven, professional",
        "strengths": ["clear communication", "technical depth", "practical delivery"],
    }
    previous_letters = [
        ("letter_1", "A realistic paragraph about building data pipelines and translating ML outcomes for experts. " * 40),
        ("letter_2", "A realistic paragraph about production deployment, evaluation, stakeholder workshops, and platform delivery. " * 40),
        ("letter_3", "A realistic paragraph about client communication, agentic systems, and multidisciplinary collaboration. " * 40),
    ]
    selected_anchors = [
        {
            "title": "Client-Facing Communication & Stakeholder Translation",
            "company_evidence": "BCG X works closely with operations teams and clients.",
            "job_connection": "The role requires translating analytical ideas into action.",
            "candidate_evidence": "I built dashboards for non-technical experts.",
            "anchor": "Bridging technical depth with stakeholder clarity.",
            "source_url": "https://example.com",
        }
    ]

    prompt = build_cover_letter_plan_prompt(
        job_description=job_description,
        selected_anchors=selected_anchors,
        style_profile=style_profile,
        previous_letters=previous_letters,
        company_url="https://example.com",
    )

    assert len(prompt) < 7000
