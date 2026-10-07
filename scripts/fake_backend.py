"""Run the real app with no keys: in-memory Supabase, fake (deliberately slow) Groq.

    python scripts/fake_backend.py              # http://127.0.0.1:8000
    FAKE_LATENCY=10 python scripts/fake_backend.py

Everything except the outside services is real: routes, templates, sessions, uploads, the profile builder.
Sign up with any email and password. Data lives in memory and resets on restart.

Trigger the error paths on purpose:
    - an email starting with "fail" cannot sign in
    - a company URL containing "fail" makes company research time out
    - a job description containing "FAIL" makes letter generation fail (Groq down)
    - a job description containing "BUSY" makes letter generation hit the rate limit
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402
from src.llm_client import LLMBusyError  # noqa: E402

SAMPLE_ANCHORS = json.loads((ROOT / "tests" / "fixtures" / "sample_anchors.json").read_text(encoding="utf-8"))
SAMPLE_LETTER = (ROOT / "tests" / "fixtures" / "sample_letter.txt").read_text(encoding="utf-8")
SAMPLE_LETTER_DE = (ROOT / "tests" / "fixtures" / "sample_letter_de.txt").read_text(encoding="utf-8")


def fakes(latency: float = 0.0) -> dict[str, Any]:
    """Replacements for every outside call app.py makes, keyed by the name app.py imports."""
    db: dict[str, Any] = {"users": {}, "resumes": [], "cover_letters": [], "style": {}, "candidate": {}, "jobs": {}, "letters": [], "files": {}}

    def new_id() -> str:
        return str(uuid.uuid4())

    def mine(rows: list[dict[str, Any]], user_id: str) -> list[dict[str, Any]]:
        return [row for row in rows if row["user_id"] == user_id]

    def slow(fraction: float = 1.0) -> None:
        time.sleep(latency * fraction)

    def token(user_id: str) -> str:
        # Shaped like a Supabase JWT (the app reads `exp` to decide when to refresh); not signed.
        claims = base64.urlsafe_b64encode(json.dumps({"sub": user_id, "exp": int(time.time()) + 3600}).encode()).decode().rstrip("=")
        return f"fake.{claims}.fake"

    def session_for(user_id: str):
        return SimpleNamespace(access_token=token(user_id), refresh_token=f"refresh-{new_id()}")

    class Auth:
        def _login(self, credentials: dict[str, Any]):
            email = credentials["email"]
            if email.lower().startswith("fail"):
                raise RuntimeError("Invalid login credentials")
            user_id = db["users"].setdefault(email, new_id())
            return SimpleNamespace(user=SimpleNamespace(id=user_id), session=session_for(user_id))

        sign_up = sign_in_with_password = _login
        admin = SimpleNamespace(sign_out=lambda jwt, scope="global": None)

        def refresh_session(self, refresh_token: str):
            return SimpleNamespace(session=session_for("refreshed"))

    def save_resume(user_id, filename, storage_path, extracted_text, **_):
        row = {"id": new_id(), "user_id": user_id, "filename": filename, "storage_path": storage_path, "extracted_text": extracted_text}
        db["resumes"].append(row)
        return row

    def save_cover_letter(user_id, filename, content, *, storage_path=None, **_):
        row = {"id": new_id(), "user_id": user_id, "filename": filename, "content": content, "storage_path": storage_path}
        db["cover_letters"].append(row)
        return row

    def upload_document(auth_user_id, kind, filename, data, **_):
        path = f"{auth_user_id}/{kind}/{new_id()[:12]}-{filename}"
        db["files"][path] = data
        return path

    def remove(table: str, user_id: str, row_id: str | None = None) -> None:
        db[table] = [row for row in db[table] if not (row["user_id"] == user_id and (row_id is None or row["id"] == row_id))]

    def save_job_application(user_id, job_description, company_url, *, selected_anchor=None, anchors=None, **_):
        row = {"id": new_id(), "user_id": user_id, "job_description": job_description, "company_url": company_url,
               "selected_anchor": selected_anchor, "anchors": anchors}
        db["jobs"][row["id"]] = row
        return row

    def get_job_application(user_id, *, job_application_id=None, **_):
        rows = [row for row in db["jobs"].values() if row["user_id"] == user_id and (job_application_id is None or row["id"] == job_application_id)]
        return rows[-1] if rows else None

    def update_job_application_anchor(user_id, job_application_id, selected_anchor, **_):
        row = get_job_application(user_id, job_application_id=job_application_id)
        if not row:
            raise RuntimeError("Failed to save the selected angle for this job application.")
        row["selected_anchor"] = selected_anchor
        return row

    def save_generated_cover_letter(user_id, filename, content, *, revision_number=None, is_final=False, job_application_id=None, feedback=None, **_):
        row = {"id": new_id(), "user_id": user_id, "filename": filename, "content": content, "revision_number": revision_number,
               "is_final": is_final, "job_application_id": job_application_id, "feedback": feedback}
        db["letters"].append(row)
        return row

    def get_generated_cover_letters_for_job(user_id, job_application_id, **_):
        rows = [row for row in mine(db["letters"], user_id) if row["job_application_id"] == job_application_id]
        return sorted((dict(row) for row in rows), key=lambda row: row["revision_number"] or 0)

    def mark_generated_cover_letter_final(user_id, job_application_id, cover_letter_id, **_):
        for row in mine(db["letters"], user_id):
            if row["job_application_id"] == job_application_id:
                row["is_final"] = row["id"] == cover_letter_id
        return {"id": cover_letter_id}

    def update_generated_cover_letter_content(user_id, cover_letter_id, content, **_):
        for row in mine(db["letters"], user_id):
            if row["id"] == cover_letter_id:
                row["content"] = content
                return row
        raise RuntimeError("Failed to save the edited cover letter.")

    def research_company(company_url):
        slow(0.5)
        if "fail" in company_url.lower():
            raise TimeoutError(f"Request timed out while fetching the company website: {company_url}")
        return {"company_research": f"Research notes for {company_url}"}

    def generate_anchors(**_):
        slow()
        return SAMPLE_ANCHORS

    def generate_cover_letter(*, job_description, selected_anchors, language="en", **_):
        slow()
        if "FAIL" in job_description:
            raise RuntimeError("Groq API is unavailable")
        if "BUSY" in job_description:
            raise LLMBusyError(detail="429 rate limit for organization org_FAKE")
        return SAMPLE_LETTER_DE if language == "de" else SAMPLE_LETTER

    def generate_cover_letter_revision(*, current_letter, user_feedback, **_):
        slow()
        return f"{current_letter.rstrip()}\n\n[Revised for: {user_feedback}]"

    return {
        "get_supabase_client": lambda: SimpleNamespace(auth=Auth()),
        "get_or_create_app_user": lambda auth_user_id=None, name=None, **_: {"id": auth_user_id, "name": name},
        "get_resumes_for_user": lambda user_id, **_: mine(db["resumes"], user_id),
        "save_resume": save_resume,
        "delete_resume_for_user": lambda user_id, resume_id, **_: remove("resumes", user_id, resume_id),
        "delete_all_resumes_for_user": lambda user_id, **_: remove("resumes", user_id),
        "get_cover_letters_for_user": lambda user_id, **_: mine(db["cover_letters"], user_id),
        "save_cover_letter": save_cover_letter,
        # Original files: an in-memory stand-in for the Supabase Storage bucket.
        "upload_document": upload_document,
        "remove_documents": lambda paths, **_: [db["files"].pop(path, None) for path in paths],
        "download_document": lambda path, **_: db["files"][path],
        "documents_bucket_bytes": lambda **_: sum(len(data) for data in db["files"].values()),
        "delete_cover_letter_for_user": lambda user_id, cover_letter_id, **_: remove("cover_letters", user_id, cover_letter_id),
        "save_candidate_profile": lambda user_id, profile, **_: db["candidate"].setdefault(user_id, {}).update(profile=profile) or db["candidate"][user_id],
        "get_candidate_profile": lambda user_id, **_: db["candidate"].get(user_id),
        "save_style_profile": lambda user_id, profile, **_: db["style"].setdefault(user_id, {}).update(profile=profile) or db["style"][user_id],
        "get_style_profile": lambda user_id, **_: db["style"].get(user_id),
        "save_job_application": save_job_application,
        "get_job_application": get_job_application,
        "update_job_application_anchor": update_job_application_anchor,
        "save_generated_cover_letter": save_generated_cover_letter,
        # In-memory rows have no timestamps, so this counts everything since the server started.
        "count_recent_ai_actions": lambda user_id, since, **_: len(mine(list(db["jobs"].values()), user_id)) + len(mine(db["letters"], user_id)),
        "get_generated_cover_letters_for_job": get_generated_cover_letters_for_job,
        "mark_generated_cover_letter_final": mark_generated_cover_letter_final,
        "update_generated_cover_letter_content": update_generated_cover_letter_content,
        "save_user_full_name": lambda full_name, **_: None,
        "research_company": research_company,
        "generate_anchors": generate_anchors,
        "generate_cover_letter": generate_cover_letter,
        "generate_cover_letter_revision": generate_cover_letter_revision,
    }


def install(latency: float) -> None:
    for name, value in fakes(latency).items():
        # Fail loudly if app.py renamed something, instead of silently calling the real service.
        if not hasattr(app, name):
            raise AttributeError(f"app.py has no '{name}' any more; update scripts/fake_backend.py")
        setattr(app, name, value)


if __name__ == "__main__":
    import uvicorn

    latency = float(os.getenv("FAKE_LATENCY", "3"))
    install(latency)
    port = int(os.getenv("PORT", "8000"))
    print(f"Fake backend: no keys, {latency:g}s fake Groq latency. Open http://127.0.0.1:{port}")
    uvicorn.run(app.app, host="127.0.0.1", port=port)
