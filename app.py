from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from supabase import create_client

from src.anchor_generator import generate_anchors, save_json
from src.company_researcher import research_company
from src.cover_letter_generator import generate_cover_letter, generate_cover_letter_revision
from src.database import (
    delete_all_resumes_for_user,
    delete_cover_letter_for_user,
    delete_resume_for_user,
    get_generated_cover_letters_for_job,
    get_job_application,
    get_or_create_app_user,
    get_candidate_profile,
    get_cover_letters_for_user,
    get_resumes_for_user,
    get_style_profile,
    mark_generated_cover_letter_final,
    save_candidate_profile,
    save_cover_letter,
    save_generated_cover_letter,
    save_job_application,
    save_resume,
    save_style_profile,
    update_job_application_anchor,
)
from src.profile_builder import (
    MAX_COVER_LETTERS,
    MIN_COVER_LETTERS,
    build_profile_bundle,
    read_uploaded_text,
    validate_cover_letters,
)

COVER_LETTER_REQUIREMENT_MESSAGE = (
    f"Upload a resume and at least {MIN_COVER_LETTERS} previous cover letter (maximum {MAX_COVER_LETTERS}) to enable profile building."
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
MAX_REVISIONS = 3
app = FastAPI(title="Cover Letter AI Profile Builder")
SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    # ponytail: per-process random key, so logins reset on restart and don't work across multiple workers. Set SESSION_SECRET in any real deployment.
    SESSION_SECRET = secrets.token_urlsafe(32)
    logging.getLogger(__name__).warning("SESSION_SECRET is not set; using a random key. Sessions will not survive a restart.")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals.update(
    MIN_COVER_LETTERS=MIN_COVER_LETTERS,
    MAX_COVER_LETTERS=MAX_COVER_LETTERS,
    COVER_LETTER_REQUIREMENT_MESSAGE=COVER_LETTER_REQUIREMENT_MESSAGE,
)


ERROR_COPY = {
    404: ("This page isn't in the draft.", "The link may be old, or the page moved. Nothing you did is lost."),
    500: ("Something smudged the ink.", "That one's on us, not you. Give it a moment and try again."),
}


@app.exception_handler(StarletteHTTPException)
async def http_error_page(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401:
        return RedirectResponse(url="/login", status_code=303)
    return render_error_page(request, exc.status_code)


@app.exception_handler(Exception)
async def server_error_page(request: Request, exc: Exception):
    return render_error_page(request, 500)


def render_error_page(request: Request, status_code: int):
    heading, message = ERROR_COPY.get(status_code, ("Something went wrong.", "Try going back, or start again from the home page."))
    return templates.TemplateResponse("error.html", {
        "request": request,
        "status_code": status_code,
        "heading": heading,
        "message": message,
        "signed_in": "session" in request.scope and bool(get_authenticated_user(request)),
    }, status_code=status_code)


def get_supabase_client() -> Any:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("Missing Supabase configuration. Set SUPABASE_URL and SUPABASE_ANON_KEY or SUPABASE_KEY.")
    return create_client(url, key)


def get_authenticated_user(request: Request) -> dict[str, Any] | None:
    user = request.session.get("auth_user")
    if not user:
        return None
    return user


def require_auth(request: Request) -> dict[str, Any]:
    user = get_authenticated_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def get_request_session_tokens(request: Request) -> tuple[str | None, str | None]:
    return request.session.get("access_token"), request.session.get("refresh_token")


def app_user_for_request(request: Request) -> dict[str, Any]:
    auth_user = require_auth(request)
    access_token, refresh_token = get_request_session_tokens(request)
    if not access_token or not refresh_token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    app_user = get_or_create_app_user(
        auth_user_id=auth_user.get("id"),
        name=auth_user.get("email", "user").split("@")[0],
        access_token=access_token,
        refresh_token=refresh_token,
    )
    return app_user


def profile_materials_fingerprint(resumes: list[dict[str, Any]], cover_letters: list[dict[str, Any]]) -> str:
    """Identify the exact resume/cover-letter set (ids and text) a profile was built from."""
    parts = sorted(f"resume:{row.get('id')}:{row.get('extracted_text') or ''}" for row in resumes)
    parts += sorted(f"cover_letter:{row.get('id')}:{row.get('content') or ''}" for row in cover_letters)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def build_profile_status(request: Request, user_id: str) -> dict[str, Any]:
    access_token, refresh_token = get_request_session_tokens(request)
    resumes = get_resumes_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
    cover_letters = get_cover_letters_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
    style_profile = get_style_profile(user_id, access_token=access_token, refresh_token=refresh_token)
    candidate_profile = get_candidate_profile(user_id, access_token=access_token, refresh_token=refresh_token)
    cover_letter_count = len(cover_letters)
    has_resume = bool(resumes)
    has_valid_cover_count = MIN_COVER_LETTERS <= cover_letter_count <= MAX_COVER_LETTERS
    is_ready = has_resume and has_valid_cover_count and bool(candidate_profile) and bool(style_profile)
    built_profile = candidate_profile.get("profile") if isinstance(candidate_profile, dict) else None
    built_fingerprint = built_profile.get("source_materials_fingerprint") if isinstance(built_profile, dict) else None

    return {
        "has_resume": has_resume,
        "resume_count": len(resumes),
        "cover_letter_count": cover_letter_count,
        "has_style_profile": bool(style_profile),
        "has_candidate_profile": bool(candidate_profile),
        "can_build": has_resume and has_valid_cover_count,
        "is_ready": is_ready,
        # True only when the saved profile was built from exactly the materials currently uploaded.
        "is_current": is_ready and built_fingerprint == profile_materials_fingerprint(resumes, cover_letters),
    }


def render_cover_letter_page(request: Request, user: dict[str, Any], job_application_id: str, chain: list[dict[str, Any]], error: str | None = None):
    """Render the letter page for a job's revision chain (ordered oldest revision first)."""
    current = chain[-1] if chain else None
    final_letter = next((record for record in chain if record.get("is_final")), None)
    shown = final_letter or current
    revision_number = int(current.get("revision_number") or 0) if current else 0
    return templates.TemplateResponse("generated_cover_letter.html", {
        "request": request,
        "user": user,
        "cover_letter": shown.get("content") if shown else "",
        "cover_letter_id": shown.get("id") if shown else "",
        "job_application_id": job_application_id,
        "revision_number": revision_number,
        "max_revisions": MAX_REVISIONS,
        "is_final": final_letter is not None,
        "can_revise": bool(current) and final_letter is None and revision_number < MAX_REVISIONS,
        "feedback_history": [record["feedback"] for record in chain if record.get("feedback")],
        "error": error,
    })


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request, "user": get_authenticated_user(request)})


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request, "error": None})


@app.post("/signup", response_class=HTMLResponse)
async def signup(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        client = get_supabase_client()
        auth_response = client.auth.sign_up({"email": email, "password": password})
        session = getattr(auth_response, "session", None) or (auth_response.get("session") if isinstance(auth_response, dict) else None)
        user = getattr(auth_response, "user", None) or (auth_response.get("user") if isinstance(auth_response, dict) else None)
        if not user:
            raise RuntimeError("Sign-up was accepted but no user record was returned.")
        if not session:
            request.session.clear()
            return templates.TemplateResponse(
                "signup.html",
                {"request": request, "error": "Your account was created. Please confirm your email before signing in."},
            )
        request.session["auth_user"] = {"id": user.id, "email": email}
        request.session["access_token"] = session.access_token
        request.session["refresh_token"] = session.refresh_token
        return RedirectResponse(url="/profile/setup", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse("signup.html", {"request": request, "error": str(exc)})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        client = get_supabase_client()
        auth_response = client.auth.sign_in_with_password({"email": email, "password": password})
        session = getattr(auth_response, "session", None) or (auth_response.get("session") if isinstance(auth_response, dict) else None)
        user = getattr(auth_response, "user", None) or (auth_response.get("user") if isinstance(auth_response, dict) else None)
        if not session or not user:
            raise RuntimeError("Login failed. Please confirm your credentials and Supabase Auth configuration.")
        request.session["auth_user"] = {"id": user.id, "email": email}
        request.session["access_token"] = session.access_token
        request.session["refresh_token"] = session.refresh_token
        return RedirectResponse(url="/profile/setup", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse("login.html", {"request": request, "error": str(exc)})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/profile/setup", response_class=HTMLResponse)
async def profile_setup_page(request: Request):
    try:
        user = app_user_for_request(request)
        access_token, refresh_token = get_request_session_tokens(request)
        user_id = str(user["id"])
        resumes = get_resumes_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
        cover_letters = get_cover_letters_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
        status = build_profile_status(request, user_id)
        current_resume = resumes[-1] if resumes else None
        return templates.TemplateResponse("profile_setup.html", {
            "request": request,
            "user": user,
            "status": status,
            "resume": current_resume,
            "cover_letters": cover_letters,
            "error": None,
        })
    except HTTPException as exc:
        return RedirectResponse(url="/login", status_code=303)


@app.post("/profile/resume", response_class=HTMLResponse)
async def upload_resume(request: Request, resume: UploadFile = File(None)):
    try:
        user = app_user_for_request(request)
        access_token, refresh_token = get_request_session_tokens(request)
        if resume is None or not resume.filename:
            raise ValueError("Please upload your resume before building your profile.")
        filename = resume.filename.lower()
        if not filename.endswith((".pdf", ".txt")):
            raise ValueError("Unsupported resume file type. Please upload a PDF or TXT file.")

        text = read_uploaded_text(resume)
        if not text or not text.strip():
            raise ValueError("The uploaded resume could not be extracted. Please try a different file.")

        existing_resumes = get_resumes_for_user(str(user["id"]), access_token=access_token, refresh_token=refresh_token)
        if existing_resumes:
            delete_all_resumes_for_user(str(user["id"]), access_token=access_token, refresh_token=refresh_token)

        save_resume(str(user["id"]), resume.filename, f"/resumes/{resume.filename}", text, access_token=access_token, refresh_token=refresh_token)
        return RedirectResponse(url="/profile/setup", status_code=303)
    except Exception as exc:
        try:
            user = app_user_for_request(request)
            status = build_profile_status(request, str(user["id"]))
            return templates.TemplateResponse("profile_setup.html", {"request": request, "user": user, "status": status, "error": str(exc)})
        except HTTPException:
            return RedirectResponse(url="/login", status_code=303)


@app.post("/profile/resume/delete", response_class=HTMLResponse)
async def delete_resume_route(request: Request, resume_id: str = Form(...)):
    try:
        user = app_user_for_request(request)
        access_token, refresh_token = get_request_session_tokens(request)
        delete_resume_for_user(str(user["id"]), resume_id, access_token=access_token, refresh_token=refresh_token)
        return RedirectResponse(url="/profile/setup", status_code=303)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)


@app.post("/profile/cover-letters", response_class=HTMLResponse)
async def upload_cover_letters(request: Request, files: list[UploadFile] = File(...)):
    try:
        user = app_user_for_request(request)
        access_token, refresh_token = get_request_session_tokens(request)
        selected_files = [uploaded for uploaded in files if (uploaded.filename or "").strip()]
        existing_count = len(get_cover_letters_for_user(str(user["id"]), access_token=access_token, refresh_token=refresh_token))

        if not selected_files:
            raise ValueError("Please select a cover letter before uploading.")
        if len(selected_files) > 1:
            raise ValueError("Please upload one cover letter at a time.")
        if existing_count + len(selected_files) > MAX_COVER_LETTERS:
            raise ValueError(f"You can upload a maximum of {MAX_COVER_LETTERS} previous cover letters.")

        validated = validate_cover_letters(selected_files, min_count=1)
        for uploaded in selected_files:
            filename = uploaded.filename.lower()
            if not filename.endswith((".pdf", ".txt")):
                raise ValueError(f"Unsupported file type: {uploaded.filename}. Please upload a PDF or TXT cover letter.")
            text = read_uploaded_text(uploaded)
            if not text or not text.strip():
                raise ValueError(f"The file {uploaded.filename} could not be extracted. Please check the document and try again.")
            save_cover_letter(str(user["id"]), uploaded.filename, text, access_token=access_token, refresh_token=refresh_token)

        return RedirectResponse(url="/profile/setup", status_code=303)
    except Exception as exc:
        try:
            user = app_user_for_request(request)
            status = build_profile_status(request, str(user["id"]))
            return templates.TemplateResponse("profile_setup.html", {"request": request, "user": user, "status": status, "error": str(exc)})
        except HTTPException:
            return RedirectResponse(url="/login", status_code=303)


@app.post("/profile/cover-letters/delete", response_class=HTMLResponse)
async def delete_cover_letter_route(request: Request, cover_letter_id: str = Form(...)):
    try:
        user = app_user_for_request(request)
        access_token, refresh_token = get_request_session_tokens(request)
        delete_cover_letter_for_user(str(user["id"]), cover_letter_id, access_token=access_token, refresh_token=refresh_token)
        return RedirectResponse(url="/profile/setup", status_code=303)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)


@app.post("/profile/build", response_class=HTMLResponse)
async def build_profile(request: Request):
    try:
        user = app_user_for_request(request)
        access_token, refresh_token = get_request_session_tokens(request)
        user_id = str(user["id"])
        resumes = get_resumes_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
        letters = get_cover_letters_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
        cover_letter_count = len(letters)

        if not resumes:
            raise ValueError("Please upload your resume before building your profile.")
        if not MIN_COVER_LETTERS <= cover_letter_count <= MAX_COVER_LETTERS:
            raise ValueError(COVER_LETTER_REQUIREMENT_MESSAGE)

        resume_text = "\n".join(item.get("extracted_text", "") for item in resumes if item.get("extracted_text"))
        cover_letter_texts = [item.get("content", "") for item in letters if item.get("content")]

        bundle = build_profile_bundle(resume_text, cover_letter_texts)
        bundle["source_materials_fingerprint"] = profile_materials_fingerprint(resumes, letters)
        save_candidate_profile(user_id, bundle, access_token=access_token, refresh_token=refresh_token)
        save_style_profile(user_id, bundle["writing_style_profile"], access_token=access_token, refresh_token=refresh_token)

        return RedirectResponse(url="/profile/ready", status_code=303)
    except Exception as exc:
        try:
            user = app_user_for_request(request)
            status = build_profile_status(request, str(user["id"]))
            return templates.TemplateResponse("profile_setup.html", {"request": request, "user": user, "status": status, "error": str(exc)})
        except HTTPException:
            return RedirectResponse(url="/login", status_code=303)


@app.get("/profile/ready", response_class=HTMLResponse)
async def profile_ready(request: Request):
    try:
        user = app_user_for_request(request)
        access_token, refresh_token = get_request_session_tokens(request)
        user_id = str(user["id"])
        profile = get_candidate_profile(user_id, access_token=access_token, refresh_token=refresh_token)
        style_profile = get_style_profile(user_id, access_token=access_token, refresh_token=refresh_token)
        if not profile and not style_profile:
            return RedirectResponse(url="/profile/setup", status_code=303)

        profile_payload = profile.get("profile", profile) if isinstance(profile, dict) else {}
        return templates.TemplateResponse("profile_ready.html", {
            "request": request,
            "user": user,
            "profile": profile_payload,
            "style_profile": style_profile.get("profile", style_profile) if isinstance(style_profile, dict) else style_profile,
        })
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)


@app.get("/profile/job-input", response_class=HTMLResponse)
async def job_input_page(request: Request):
    try:
        user = app_user_for_request(request)
        return templates.TemplateResponse("job_input.html", {
            "request": request,
            "user": user,
            "job_description": request.session.get("job_description", ""),
            "company_url": request.session.get("company_url", ""),
            "error": None,
        })
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)


@app.post("/profile/job-input", response_class=HTMLResponse)
async def submit_job_input(request: Request, job_description: str = Form(...), company_url: str = Form(...)):
    try:
        user = app_user_for_request(request)
        jd = (job_description or "").strip()
        company_url_value = (company_url or "").strip()

        if not jd:
            raise ValueError("Job Description is required.")
        if not company_url_value:
            raise ValueError("Company URL is required.")

        user_id = str(user["id"])
        access_token, refresh_token = get_request_session_tokens(request)
        job_application = save_job_application(
            user_id,
            jd,
            company_url_value,
            access_token=access_token,
            refresh_token=refresh_token,
        )

        # Only the internal id travels in the (size-limited) session cookie; job details live in the database.
        request.session.pop("job_description", None)
        request.session.pop("company_url", None)
        request.session["job_application_id"] = str(job_application["id"])

        company_research = research_company(company_url_value)
        # Use this user's uploaded cover letters (the local extracted_letters folder held 8 letters and pushed
        # the anchor request over Groq's 7000 input-token limit).
        letter_rows = get_cover_letters_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
        letters = [
            (str(row.get("filename") or "cover_letter"), str(row.get("content") or ""))
            for row in letter_rows
            if row.get("content")
        ]
        if not letters:
            raise ValueError("No previous cover letters were found for your profile. Please upload them in Profile Setup.")
        anchor_payload = generate_anchors(
            company_url=company_url_value,
            job_description=jd,
            company_research=company_research["company_research"],
            letters=letters,
        )
        save_json(BASE_DIR / "company_anchors.json", anchor_payload)
        return RedirectResponse(url="/company/angles", status_code=303)
    except ValueError as exc:
        try:
            user = app_user_for_request(request)
            return templates.TemplateResponse("job_input.html", {
                "request": request,
                "user": user,
                "job_description": job_description or "",
                "company_url": company_url or "",
                "error": str(exc),
            })
        except HTTPException:
            return RedirectResponse(url="/login", status_code=303)
    except Exception as exc:
        try:
            user = app_user_for_request(request)
            return templates.TemplateResponse("job_input.html", {
                "request": request,
                "user": user,
                "job_description": job_description or "",
                "company_url": company_url or "",
                "error": str(exc),
            })
        except HTTPException:
            return RedirectResponse(url="/login", status_code=303)


@app.get("/company/angles", response_class=HTMLResponse)
async def company_angles_page(request: Request):
    try:
        user = app_user_for_request(request)
        payload_path = BASE_DIR / "company_anchors.json"
        if not payload_path.exists():
            return RedirectResponse(url="/profile/job-input", status_code=303)

        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return RedirectResponse(url="/profile/job-input", status_code=303)

        anchors = payload.get("anchors", []) if isinstance(payload, dict) else []
        user_id = str(user["id"])
        access_token, refresh_token = get_request_session_tokens(request)
        job_application_id = request.session.get("job_application_id")
        if not job_application_id:
            return RedirectResponse(url="/profile/job-input", status_code=303)
        job_application = get_job_application(user_id, job_application_id=job_application_id, access_token=access_token, refresh_token=refresh_token)
        if not job_application:
            return RedirectResponse(url="/profile/job-input", status_code=303)
        db_company_url = job_application.get("company_url") if isinstance(job_application, dict) else ""
        db_job_description = job_application.get("job_description") if isinstance(job_application, dict) else ""
        company_url = (db_company_url or request.session.get("company_url") or (payload.get("company_url", "") if isinstance(payload, dict) else "")).strip()
        job_description = (db_job_description or request.session.get("job_description") or "").strip()
        return templates.TemplateResponse("company_angles.html", {
            "request": request,
            "user": user,
            "company_url": company_url,
            "job_description": job_description,
            "job_application_id": str(job_application["id"]),
            "anchors": anchors,
            "error": None,
        })
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)


@app.post("/company/angles", response_class=HTMLResponse)
async def select_company_angle(
    request: Request,
    selected_anchor: str = Form(...),
    job_description: str = Form(""),
    company_url: str = Form(""),
    job_application_id: str = Form(""),
):
    try:
        user = app_user_for_request(request)
        anchors_payload_path = BASE_DIR / "company_anchors.json"
        if not anchors_payload_path.exists():
            raise FileNotFoundError("No company angle data is available yet. Please go back to Job Input and generate angles again.")

        payload = json.loads(anchors_payload_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("The generated company angle payload is invalid.")

        anchors = payload.get("anchors", [])
        if not isinstance(anchors, list) or not anchors:
            raise ValueError("No company angles were generated for this opportunity.")

        if not selected_anchor:
            raise ValueError("Please choose one angle.")

        try:
            selected_index = int(selected_anchor)
        except (TypeError, ValueError):
            raise ValueError("The selected angle was not valid.") from None

        if selected_index < 0 or selected_index >= len(anchors):
            raise ValueError("The selected angle is outside the generated list.")

        user_id = str(user["id"])
        access_token, refresh_token = get_request_session_tokens(request)
        requested_application_id = (job_application_id or "").strip() or request.session.get("job_application_id")
        job_application = None
        if requested_application_id:
            # Filtered by user_id (and RLS), so an id belonging to another user resolves to None.
            job_application = get_job_application(user_id, job_application_id=requested_application_id, access_token=access_token, refresh_token=refresh_token)
        if not job_application:
            return templates.TemplateResponse("job_input.html", {
                "request": request,
                "user": user,
                "job_description": "",
                "company_url": company_url or "",
                "error": "No saved job details were found. Please submit the Job Description and Company URL again.",
            })

        jd = (job_application.get("job_description") or "").strip()
        company_url_value = (job_application.get("company_url") or "").strip()

        if not jd:
            raise ValueError("Job description is required before generating a cover letter.")
        if not company_url_value:
            raise ValueError("Company URL is required before generating a cover letter.")

        style_profile_row = get_style_profile(user_id, access_token=access_token, refresh_token=refresh_token)
        if not style_profile_row:
            raise ValueError("No writing-style profile is available for this user.")
        style_profile = style_profile_row.get("profile") if isinstance(style_profile_row, dict) else style_profile_row

        candidate_profile_row = get_candidate_profile(user_id, access_token=access_token, refresh_token=refresh_token)
        if not candidate_profile_row:
            raise ValueError("No candidate profile is available for this user.")
        candidate_profile = candidate_profile_row.get("profile") if isinstance(candidate_profile_row, dict) else candidate_profile_row

        previous_letter_rows = get_cover_letters_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
        previous_letters = [
            (str(record.get("filename") or "cover_letter"), str(record.get("content") or ""))
            for record in previous_letter_rows
            if record.get("content")
        ]
        if not previous_letters:
            raise ValueError("No previous cover-letter evidence is available for this user.")

        job_application_id = str(job_application["id"])
        existing_chain = get_generated_cover_letters_for_job(user_id, job_application_id, access_token=access_token, refresh_token=refresh_token)
        if existing_chain:
            # Never add a second revision 0 to an existing chain: start a fresh application with the same job details.
            job_application = save_job_application(user_id, jd, company_url_value, access_token=access_token, refresh_token=refresh_token)
            job_application_id = str(job_application["id"])
        request.session["job_application_id"] = job_application_id

        update_job_application_anchor(
            user_id,
            job_application_id,
            anchors[selected_index],
            access_token=access_token,
            refresh_token=refresh_token,
        )

        selected_anchors = [anchors[selected_index]]
        letter = generate_cover_letter(
            job_description=jd,
            selected_anchors=selected_anchors,
            style_profile=style_profile,
            previous_letters=previous_letters,
            company_url=company_url_value,
        )

        saved_letter = save_generated_cover_letter(
            user_id,
            f"generated_cover_letter_{selected_index + 1}.txt",
            letter,
            revision_number=0,
            is_final=False,
            job_application_id=job_application_id,
            access_token=access_token,
            refresh_token=refresh_token,
        )

        chain = [{**saved_letter, "content": letter, "revision_number": 0, "is_final": False, "feedback": None}]
        return render_cover_letter_page(request, user, job_application_id, chain)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    except Exception as exc:
        fallback_anchors = []
        try:
            payload = json.loads((BASE_DIR / "company_anchors.json").read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                fallback_anchors = payload.get("anchors", [])
        except Exception:
            fallback_anchors = []

        return templates.TemplateResponse("company_angles.html", {
            "request": request,
            "user": app_user_for_request(request),
            "company_url": company_url or request.session.get("company_url") or "",
            "job_description": job_description or request.session.get("job_description") or "",
            "job_application_id": request.session.get("job_application_id") or job_application_id or "",
            "anchors": fallback_anchors,
            "error": str(exc),
        })


@app.post("/cover-letter/revise", response_class=HTMLResponse)
async def revise_cover_letter(request: Request, job_application_id: str = Form(...), feedback: str = Form("")):
    try:
        user = app_user_for_request(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)

    chain: list[dict[str, Any]] = []
    try:
        user_id = str(user["id"])
        access_token, refresh_token = get_request_session_tokens(request)
        job_application = get_job_application(user_id, job_application_id=job_application_id, access_token=access_token, refresh_token=refresh_token)
        if not job_application:
            raise ValueError("This job application was not found for the current user.")

        chain = get_generated_cover_letters_for_job(user_id, job_application_id, access_token=access_token, refresh_token=refresh_token)
        if not chain:
            raise ValueError("No generated cover letter exists for this job application yet.")
        if any(record.get("is_final") for record in chain):
            raise ValueError("This cover letter has already been accepted as final.")

        current = chain[-1]
        revision_number = int(current.get("revision_number") or 0)
        if revision_number >= MAX_REVISIONS:
            raise ValueError(f"The maximum of {MAX_REVISIONS} revisions has been reached. Please accept the current letter.")

        feedback_text = (feedback or "").strip()
        if not feedback_text:
            raise ValueError("Please enter feedback before requesting a revision.")

        selected_anchor = job_application.get("selected_anchor")
        if not isinstance(selected_anchor, dict) or not selected_anchor:
            raise ValueError("No selected angle is stored for this job application.")

        style_profile_row = get_style_profile(user_id, access_token=access_token, refresh_token=refresh_token)
        if not style_profile_row:
            raise ValueError("No writing-style profile is available for this user.")
        style_profile = style_profile_row.get("profile") if isinstance(style_profile_row, dict) else style_profile_row

        previous_letter_rows = get_cover_letters_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
        previous_letters = [
            (str(record.get("filename") or "cover_letter"), str(record.get("content") or ""))
            for record in previous_letter_rows
            if record.get("content")
        ]
        if not previous_letters:
            raise ValueError("No previous cover-letter evidence is available for this user.")

        revised_letter = generate_cover_letter_revision(
            current_letter=str(current.get("content") or ""),
            user_feedback=feedback_text,
            job_description=str(job_application.get("job_description") or ""),
            selected_anchors=[selected_anchor],
            style_profile=style_profile,
            previous_letters=previous_letters,
            company_url=str(job_application.get("company_url") or ""),
            feedback_history=[record["feedback"] for record in chain if record.get("feedback")],
        )

        new_revision_number = revision_number + 1
        saved_letter = save_generated_cover_letter(
            user_id,
            f"generated_cover_letter_revision_{new_revision_number}.txt",
            revised_letter,
            revision_number=new_revision_number,
            is_final=False,
            job_application_id=job_application_id,
            feedback=feedback_text,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        chain.append({**saved_letter, "content": revised_letter, "revision_number": new_revision_number, "is_final": False, "feedback": feedback_text})
        return render_cover_letter_page(request, user, job_application_id, chain)
    except Exception as exc:
        return render_cover_letter_page(request, user, job_application_id, chain, error=str(exc))


@app.post("/cover-letter/accept", response_class=HTMLResponse)
async def accept_cover_letter(request: Request, job_application_id: str = Form(...), cover_letter_id: str = Form(...)):
    try:
        user = app_user_for_request(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)

    chain: list[dict[str, Any]] = []
    try:
        user_id = str(user["id"])
        access_token, refresh_token = get_request_session_tokens(request)
        chain = get_generated_cover_letters_for_job(user_id, job_application_id, access_token=access_token, refresh_token=refresh_token)
        if not any(str(record.get("id")) == cover_letter_id for record in chain):
            raise ValueError("The selected cover letter does not belong to this job application.")

        mark_generated_cover_letter_final(user_id, job_application_id, cover_letter_id, access_token=access_token, refresh_token=refresh_token)
        for record in chain:
            record["is_final"] = str(record.get("id")) == cover_letter_id
        return render_cover_letter_page(request, user, job_application_id, chain)
    except Exception as exc:
        return render_cover_letter_page(request, user, job_application_id, chain, error=str(exc))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
