from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from supabase import create_client

from src.database import (
    delete_all_resumes_for_user,
    delete_cover_letter_for_user,
    delete_resume_for_user,
    get_or_create_app_user,
    get_candidate_profile,
    get_cover_letters_for_user,
    get_resumes_for_user,
    get_style_profile,
    save_candidate_profile,
    save_cover_letter,
    save_resume,
    save_style_profile,
)
from src.profile_builder import build_profile_bundle, read_uploaded_text, validate_cover_letters

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Cover Letter AI Profile Builder")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "cover-letter-ai-dev-secret"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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


def build_profile_status(request: Request, user_id: str) -> dict[str, Any]:
    access_token, refresh_token = get_request_session_tokens(request)
    resumes = get_resumes_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
    cover_letters = get_cover_letters_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
    style_profile = get_style_profile(user_id, access_token=access_token, refresh_token=refresh_token)
    candidate_profile = get_candidate_profile(user_id, access_token=access_token, refresh_token=refresh_token)
    cover_letter_count = len(cover_letters)
    has_resume = bool(resumes)
    has_valid_cover_count = cover_letter_count == 5

    return {
        "has_resume": has_resume,
        "resume_count": len(resumes),
        "cover_letter_count": cover_letter_count,
        "has_style_profile": bool(style_profile),
        "has_candidate_profile": bool(candidate_profile),
        "can_build": has_resume and has_valid_cover_count,
        "is_ready": has_resume and has_valid_cover_count and bool(candidate_profile) and bool(style_profile),
    }


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request, "error": None})


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
        validated = validate_cover_letters(files)
        existing_count = len(get_cover_letters_for_user(str(user["id"]), access_token=access_token, refresh_token=refresh_token))
        if existing_count + len(validated) > 5:
            raise ValueError("You can upload a maximum of 5 previous cover letters.")

        for uploaded in files:
            if not uploaded.filename:
                continue
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
        if cover_letter_count != 5:
            raise ValueError("Please upload exactly 5 previous cover letters before building your profile.")

        resume_text = "\n".join(item.get("extracted_text", "") for item in resumes if item.get("extracted_text"))
        cover_letter_texts = [item.get("content", "") for item in letters if item.get("content")]

        bundle = build_profile_bundle(resume_text, cover_letter_texts)
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
