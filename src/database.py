from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client


def load_supabase_env() -> tuple[str, str]:
    """Load Supabase configuration from environment variables."""
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY")  # same names app.py accepts

    if not url or not url.strip():
        raise ValueError("Missing SUPABASE_URL in environment variables.")
    if not key or not key.strip():
        raise ValueError("Missing SUPABASE_ANON_KEY (or SUPABASE_KEY) in environment variables.")

    return url, key


def get_client(access_token: str | None = None, refresh_token: str | None = None) -> Client:
    """Return a Supabase client that queries as the signed-in user (row-level security applies).

    The access token is attached locally; PostgREST verifies it on every query, so there is no extra auth
    round trip per call. app.py refreshes expiring tokens once per request before they get here.
    `refresh_token` is accepted for compatibility and not used.
    """
    url, key = load_supabase_env()
    return _client_for(url, key, access_token or "")


_thread_clients = threading.local()
MAX_CLIENTS_PER_THREAD = 64


def _client_for(url: str, key: str, access_token: str) -> Client:
    # One cached client per token *per thread*. A client must never be shared across threads: postgrest talks
    # HTTP/2, and httpcore compresses request headers without a lock, so concurrent use of one connection can
    # corrupt headers (seen as "Invalid API key" / dropped connections in /profile/setup's parallel loaders).
    clients = getattr(_thread_clients, "clients", None)
    if clients is None:
        clients = _thread_clients.clients = {}
    cache_key = (url, key, access_token)
    client = clients.get(cache_key)
    if client is None:
        if len(clients) >= MAX_CLIENTS_PER_THREAD:
            clients.clear()
        client = create_client(url, key)
        if access_token:
            client.postgrest.auth(access_token)
        clients[cache_key] = client
    return client


def filter_records_for_user(records: list[dict[str, Any]], user_id: str) -> list[dict[str, Any]]:
    """Return only records belonging to the requested user."""
    return [record for record in records if str(record.get("user_id")) == str(user_id)]


def get_or_create_app_user(auth_user_id: str | None = None, name: str | None = None, *, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any]:
    """Create or retrieve the application user row linked to Supabase Auth identity when available."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    lookup_name = name or "test_user"

    if auth_user_id:
        result = supabase.table("users").select("*").eq("auth_user_id", auth_user_id).limit(1).execute()
        rows = result.data or []
        if rows:
            return rows[0]

    result = supabase.table("users").select("*").eq("name", lookup_name).limit(1).execute()
    rows = result.data or []
    if rows:
        return rows[0]

    payload: dict[str, Any] = {"name": lookup_name}
    if auth_user_id:
        payload["auth_user_id"] = auth_user_id

    insert_result = supabase.table("users").insert(payload).execute()
    if not insert_result.data:
        raise RuntimeError("Failed to create the application user.")
    return insert_result.data[0]


def create_test_user() -> dict[str, Any]:
    """Create or retrieve the legacy demo user used for local testing."""
    return get_or_create_app_user(name="test_user")


def save_cover_letter(user_id: str, filename: str, content: str, *, storage_path: str | None = None, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any]:
    """Persist one extracted cover letter text into the cover_letters table."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    payload = {
        "user_id": user_id,
        "filename": filename,
        "content": content,
    }
    if storage_path:  # only sent when set, so this works before migration_add_document_storage.sql is run
        payload["storage_path"] = storage_path

    result = supabase.table("cover_letters").insert(payload).execute()
    if not result.data:
        raise RuntimeError(f"Failed to save cover letter {filename}.")
    return result.data[0]


def delete_cover_letter_for_user(user_id: str, cover_letter_id: str, *, access_token: str | None = None, refresh_token: str | None = None) -> None:
    """Delete a cover letter only when it belongs to the authenticated user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    result = supabase.table("cover_letters").delete().eq("id", cover_letter_id).eq("user_id", user_id).execute()
    if result.data is None:
        raise RuntimeError("Failed to delete the selected cover letter.")


def get_cover_letters_for_user(user_id: str, *, access_token: str | None = None, refresh_token: str | None = None) -> list[dict[str, Any]]:
    """Return all cover-letter records belonging to a specific user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    result = supabase.table("cover_letters").select("*").eq("user_id", user_id).execute()
    return result.data or []


def save_style_profile(user_id: str, profile: dict[str, Any], *, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any]:
    """Insert or update the JSONB style profile for the given user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)

    existing = supabase.table("style_profiles").select("*").eq("user_id", user_id).limit(1).execute()
    rows = existing.data or []

    payload = {
        "user_id": user_id,
        "profile": profile,
    }

    if rows:
        result = supabase.table("style_profiles").update(payload).eq("user_id", user_id).execute()
    else:
        result = supabase.table("style_profiles").insert(payload).execute()

    if not result.data:
        raise RuntimeError("Failed to save the style profile.")
    return result.data[0]


def get_style_profile(user_id: str, *, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any] | None:
    """Fetch the most recent style profile for the user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    result = supabase.table("style_profiles").select("*").eq("user_id", user_id).order("updated_at", desc=True).limit(1).execute()
    rows = result.data or []
    return rows[0] if rows else None


def save_resume(user_id: str, filename: str, storage_path: str, extracted_text: str, *, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any]:
    """Persist a resume record for a specific user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    payload = {
        "user_id": user_id,
        "filename": filename,
        "storage_path": storage_path,
        "extracted_text": extracted_text,
    }
    result = supabase.table("resumes").insert(payload).execute()
    if not result.data:
        raise RuntimeError(f"Failed to save resume {filename}.")
    return result.data[0]


def delete_resume_for_user(user_id: str, resume_id: str, *, access_token: str | None = None, refresh_token: str | None = None) -> None:
    """Delete a resume only when it belongs to the authenticated user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    result = supabase.table("resumes").delete().eq("id", resume_id).eq("user_id", user_id).execute()
    if result.data is None:
        raise RuntimeError("Failed to delete the selected resume.")


def delete_all_resumes_for_user(user_id: str, *, access_token: str | None = None, refresh_token: str | None = None) -> None:
    """Remove all resume rows for the authenticated user before replacing the current resume."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    supabase.table("resumes").delete().eq("user_id", user_id).execute()


def get_resumes_for_user(user_id: str, *, access_token: str | None = None, refresh_token: str | None = None) -> list[dict[str, Any]]:
    """Return resume records for a single user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    result = supabase.table("resumes").select("*").eq("user_id", user_id).execute()
    return result.data or []


def save_candidate_profile(user_id: str, profile: dict[str, Any], *, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any]:
    """Persist the structured candidate profile/evidence JSON for a single user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    existing = supabase.table("candidate_profiles").select("*").eq("user_id", user_id).limit(1).execute()
    rows = existing.data or []
    payload = {"user_id": user_id, "profile": profile}
    if rows:
        result = supabase.table("candidate_profiles").update(payload).eq("user_id", user_id).execute()
    else:
        result = supabase.table("candidate_profiles").insert(payload).execute()
    if not result.data:
        raise RuntimeError("Failed to save the candidate profile.")
    return result.data[0]


def get_candidate_profile(user_id: str, *, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any] | None:
    """Fetch the latest candidate profile for the user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    result = supabase.table("candidate_profiles").select("*").eq("user_id", user_id).order("updated_at", desc=True).limit(1).execute()
    rows = result.data or []
    return rows[0] if rows else None


def save_generated_cover_letter(user_id: str, filename: str, content: str, *, revision_number: int | None = None, is_final: bool = False, job_application_id: str | None = None, feedback: str | None = None, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any]:
    """Persist a generated cover-letter artifact for a user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    payload = {
        "user_id": user_id,
        "filename": filename,
        "content": content,
        "revision_number": revision_number,
        "is_final": is_final,
        "job_application_id": job_application_id,
        "feedback": feedback,
    }
    result = supabase.table("generated_cover_letters").insert(payload).execute()
    if not result.data:
        raise RuntimeError(f"Failed to save generated cover letter {filename}.")
    return result.data[0]


def get_generated_cover_letters_for_user(user_id: str, *, access_token: str | None = None, refresh_token: str | None = None) -> list[dict[str, Any]]:
    """Return generated cover letters for a single user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    result = supabase.table("generated_cover_letters").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
    return result.data or []


def save_job_application(
    user_id: str,
    job_description: str,
    company_url: str,
    *,
    selected_anchor: dict[str, Any] | None = None,
    anchors: list[dict[str, Any]] | None = None,
    access_token: str | None = None,
    refresh_token: str | None = None,
) -> dict[str, Any]:
    """Persist a job application draft (with its generated company angles) for a single user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    payload = {
        "user_id": user_id,
        "job_description": job_description,
        "company_url": company_url,
        "selected_anchor": selected_anchor,
        "anchors": anchors,
    }

    result = supabase.table("job_applications").insert(payload).execute()
    if not result.data:
        raise RuntimeError("Failed to save the job application.")
    return result.data[0]


def get_job_application(user_id: str, *, job_application_id: str | None = None, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any] | None:
    """Return a specific job application, or the most recent one, for a user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    query = supabase.table("job_applications").select("*").eq("user_id", user_id)
    if job_application_id:
        query = query.eq("id", job_application_id)
    result = query.order("created_at", desc=True).limit(1).execute()
    rows = result.data or []
    return rows[0] if rows else None


def update_job_application_anchor(user_id: str, job_application_id: str, selected_anchor: dict[str, Any], *, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any]:
    """Persist the angle the user selected for this job application."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    result = (
        supabase.table("job_applications")
        .update({"selected_anchor": selected_anchor, "updated_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", job_application_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Failed to save the selected angle for this job application.")
    return result.data[0]


def get_generated_cover_letters_for_job(user_id: str, job_application_id: str, *, access_token: str | None = None, refresh_token: str | None = None) -> list[dict[str, Any]]:
    """Return the revision chain for one job application, oldest revision first."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    result = (
        supabase.table("generated_cover_letters")
        .select("*")
        .eq("user_id", user_id)
        .eq("job_application_id", job_application_id)
        .order("revision_number")
        .order("created_at")  # tie-break so "latest" is the same row on every read
        .execute()
    )
    return result.data or []


def save_letter_satisfaction(user_id: str, cover_letter_id: str, satisfaction: str, feedback: str | None = None, *, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any]:
    """Record the post-revision satisfaction response (and optional product feedback) on one generated letter."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    result = (
        supabase.table("generated_cover_letters")
        .update({
            "satisfaction": satisfaction,
            "satisfaction_feedback": feedback,
            "satisfaction_submitted_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", cover_letter_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Failed to save your response.")
    return result.data[0]


def update_generated_cover_letter_content(user_id: str, cover_letter_id: str, content: str, *, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any]:
    """Replace one letter's text with the user's own edits (only their own row, enforced by RLS too)."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    result = (
        supabase.table("generated_cover_letters")
        .update({"content": content})
        .eq("id", cover_letter_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Failed to save the edited cover letter.")
    return result.data[0]


def mark_generated_cover_letter_final(user_id: str, job_application_id: str, cover_letter_id: str, *, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any]:
    """Mark exactly one letter in a job's revision chain as final."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    (
        supabase.table("generated_cover_letters")
        .update({"is_final": False})
        .eq("user_id", user_id)
        .eq("job_application_id", job_application_id)
        .execute()
    )
    result = (
        supabase.table("generated_cover_letters")
        .update({"is_final": True})
        .eq("id", cover_letter_id)
        .eq("user_id", user_id)
        .eq("job_application_id", job_application_id)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Failed to mark the selected cover letter as final.")
    return result.data[0]


def count_recent_ai_actions(user_id: str, since: datetime, *, access_token: str | None = None, refresh_token: str | None = None) -> int:
    """Jobs started plus letters drafted or revised since `since`: each one costs several LLM calls."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    total = 0
    for table in ("job_applications", "generated_cover_letters"):
        result = supabase.table(table).select("id", count="exact", head=True).eq("user_id", user_id).gte("created_at", since.isoformat()).execute()
        total += result.count or 0
    return total


# --- Original files in Supabase Storage (supabase/migration_add_document_storage.sql) ---
# Private bucket; each user's files live under "<auth user id>/..." and storage policies limit access to that folder.
DOCUMENTS_BUCKET = "documents"
CONTENT_TYPES = {".pdf": "application/pdf", ".txt": "text/plain"}


def _documents(access_token: str):
    # A fresh client per call: uploads are rare, and storage clients must not be shared across threads either.
    from storage3 import SyncStorageClient

    url, key = load_supabase_env()
    storage = SyncStorageClient(f"{url.rstrip('/')}/storage/v1", {"apikey": key, "Authorization": f"Bearer {access_token}"})
    return storage.from_(DOCUMENTS_BUCKET)


def safe_filename(filename: str) -> str:
    name = Path(filename or "file").name
    cleaned = "".join(ch if (ch.isascii() and ch.isalnum()) or ch in "._-" else "_" for ch in name).strip("._") or "file"
    return cleaned[-120:]


def upload_document(auth_user_id: str, kind: str, filename: str, data: bytes, *, access_token: str) -> str:
    """Store one original file and return its storage path ("<auth id>/<kind>/<random>-<name>")."""
    import uuid

    path = f"{auth_user_id}/{kind}/{uuid.uuid4().hex[:12]}-{safe_filename(filename)}"
    content_type = CONTENT_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")
    _documents(access_token).upload(path, data, {"content-type": content_type, "upsert": "false"})
    return path


def remove_documents(paths: list[str], *, access_token: str) -> None:
    paths = [path for path in paths if path and not path.startswith("/")]  # "/resumes/x.pdf" was a label, never a file
    if paths:
        _documents(access_token).remove(paths)


def download_document(path: str, *, access_token: str) -> bytes:
    return _documents(access_token).download(path)


def save_user_full_name(full_name: str, *, access_token: str) -> None:
    """Store the user's own name on their Supabase login (auth user metadata); only they can change it."""
    import httpx

    url, key = load_supabase_env()
    response = httpx.put(
        f"{url.rstrip('/')}/auth/v1/user",
        headers={"apikey": key, "Authorization": f"Bearer {access_token}"},
        json={"data": {"full_name": full_name}},
        timeout=15,
    )
    response.raise_for_status()


def documents_bucket_bytes(*, access_token: str) -> int:
    """Total size of every file in the "documents" bucket (SQL function in migration_add_storage_budget.sql)."""
    result = get_client(access_token=access_token).rpc("documents_bucket_bytes").execute()
    return int(result.data or 0)
