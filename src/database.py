from __future__ import annotations

import json
import os
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
    key = os.getenv("SUPABASE_KEY")

    if not url or not url.strip():
        raise ValueError("Missing SUPABASE_URL in environment variables.")
    if not key or not key.strip():
        raise ValueError("Missing SUPABASE_KEY in environment variables.")

    return url, key


def get_client(access_token: str | None = None, refresh_token: str | None = None) -> Client:
    """Create and return a Supabase client authenticated with the current session when provided."""
    url, key = load_supabase_env()
    client = create_client(url, key)
    if access_token and refresh_token:
        client.auth.set_session(access_token, refresh_token)
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


def save_cover_letter(user_id: str, filename: str, content: str, *, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any]:
    """Persist one extracted cover letter text into the cover_letters table."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    payload = {
        "user_id": user_id,
        "filename": filename,
        "content": content,
    }

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


def save_generated_cover_letter(user_id: str, filename: str, content: str, *, revision_number: int | None = None, is_final: bool = False, access_token: str | None = None, refresh_token: str | None = None) -> dict[str, Any]:
    """Persist a generated cover-letter artifact for a user."""
    supabase = get_client(access_token=access_token, refresh_token=refresh_token)
    payload = {
        "user_id": user_id,
        "filename": filename,
        "content": content,
        "revision_number": revision_number,
        "is_final": is_final,
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
