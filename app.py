from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import anyio.to_thread
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from supabase import create_client
from supabase_auth.helpers import generate_pkce_challenge, generate_pkce_verifier

from src.anchor_generator import generate_anchors
from src.company_researcher import cap_text, extract_company_text, fetch_company_html, research_company
from src.cover_letter_generator import generate_cover_letter, generate_cover_letter_revision
from src.resume_facts import select_resume_facts
from src.send_checks import send_check_items
from src.database import (
    delete_all_resumes_for_user,
    delete_cover_letter_for_user,
    delete_resume_for_user,
    download_document,
    get_generated_cover_letters_for_job,
    get_job_application,
    get_or_create_app_user,
    get_candidate_profile,
    count_recent_ai_actions,
    get_cover_letters_for_user,
    get_resumes_for_user,
    documents_bucket_bytes,
    DOCX_MIME,
    get_style_profile,
    remove_documents,
    save_user_full_name,
    mark_generated_cover_letter_final,
    save_letter_satisfaction,
    save_candidate_profile,
    save_cover_letter,
    save_generated_cover_letter,
    save_job_application,
    save_resume,
    save_style_profile,
    update_generated_cover_letter_content,
    update_job_application_anchor,
    upload_document,
)

from src.llm_client import LLMError
from src.letter_pdf import build_cover_letter_pdf

from src.profile_builder import (
    MAX_COVER_LETTERS,
    MIN_COVER_LETTERS,
    build_profile_bundle,
    extract_candidate_name,
    SUPPORTED_EXTENSIONS,
    read_limited,
    read_uploaded_text,
    validate_cover_letters,
)

COVER_LETTER_REQUIREMENT_MESSAGE = (
    f"Upload a resume and at least {MIN_COVER_LETTERS} previous cover letter (maximum {MAX_COVER_LETTERS}) to enable profile building."
)

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

BASE_DIR = Path(__file__).resolve().parent
MAX_REVISIONS = 3
# Routes that call Supabase, Groq or the company site are plain `def`, so FastAPI runs them on worker threads
# and one user's 30-second letter never blocks anyone else. Pages without I/O stay `async` on the event loop.
WORKER_THREADS = int(os.getenv("WORKER_THREADS", "100"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # ponytail: one shared pool (default 40) sized up for slow LLM calls; a separate limiter or job queue if they ever crowd out uploads.
    anyio.to_thread.current_default_thread_limiter().total_tokens = WORKER_THREADS
    yield


app = FastAPI(title="Cover Letter AI Profile Builder", lifespan=lifespan)
SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    # ponytail: per-process random key, so logins reset on restart and don't work across multiple workers. Set SESSION_SECRET in any real deployment.
    SESSION_SECRET = secrets.token_urlsafe(32)
    logging.getLogger(__name__).warning("SESSION_SECRET is not set; using a random key. Sessions will not survive a restart.")
# On an https deployment (APP_URL set) the login cookie is never sent over plain http.
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=os.getenv("APP_URL", "").startswith("https://"))
logger = logging.getLogger(__name__)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# Templates build links through url()/asset() so scripts/build_site.py can render the same pages for GitHub Pages.
SITE_CONFIG = json.loads((BASE_DIR / "site" / "config.json").read_text(encoding="utf-8"))
templates.env.globals.update(url=lambda path: path, asset=lambda path: f"/static/{path}", site=SITE_CONFIG, site_url=None)
templates.env.globals.update(
    MIN_COVER_LETTERS=MIN_COVER_LETTERS,
    MAX_COVER_LETTERS=MAX_COVER_LETTERS,
    COVER_LETTER_REQUIREMENT_MESSAGE=COVER_LETTER_REQUIREMENT_MESSAGE,
    # Turn on after enabling the Google provider in Supabase; read per request so tests can flip it.
    google_sign_in=lambda: os.getenv("GOOGLE_SIGN_IN", "").lower() in ("1", "true", "on"),
)


ERROR_COPY = {
    404: ("This page isn't in the draft.", "The link may be old, or the page moved. Nothing you did is lost."),
    500: ("Something smudged the ink.", "That one's on us, not you. Give it a moment and try again."),
}


CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    # 'unsafe-inline': the templates use a few small inline scripts and style="--i:0" attributes.
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "form-action 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    response.headers.setdefault("X-Frame-Options", "DENY")  # no clickjacking via an invisible iframe
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if os.getenv("APP_URL", "").startswith("https://"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if "session" in request.cookies and not request.url.path.startswith("/static/"):
        # Signed-in pages hold letters and resume details: keep them out of the browser cache,
        # so the back button on a shared computer can't show them after logout.
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def log_request_time(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    if not request.url.path.startswith("/static/"):
        logging.getLogger("app.timing").info("%s %s -> %s in %.0f ms", request.method, request.url.path, response.status_code, (time.perf_counter() - started) * 1000)
    return response


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


def load_anchors(job_application: dict[str, Any] | None) -> list[dict[str, Any]]:
    # Angles live on the job application row (RLS keeps them per user), not on local disk that free hosts wipe.
    anchors = (job_application or {}).get("anchors")
    return anchors if isinstance(anchors, list) else []


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


def token_expires_soon(access_token: str, within_seconds: int = 60) -> bool:
    """Read the JWT's exp claim locally (no signature check needed just to decide on a refresh)."""
    try:
        payload = access_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"]) - time.time() < within_seconds
    except Exception:
        return True


def ensure_fresh_tokens(request: Request) -> None:
    """Refresh an expiring session once per request and store the new tokens.

    Supabase refresh tokens are single-use, so the rotated pair must be saved back to the session.
    """
    access_token, refresh_token = get_request_session_tokens(request)
    if not access_token or not refresh_token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not token_expires_soon(access_token):
        return
    session = refresh_once(refresh_token)
    if not session:
        # Forget the dead login, or /login would bounce the user straight back here (redirect loop).
        request.session.clear()
        raise HTTPException(status_code=401, detail="Session expired.")
    request.session["access_token"] = session.access_token
    request.session["refresh_token"] = session.refresh_token
    # Re-read the user's row about once an hour so name or email changes show up.
    request.session.pop("app_user", None)


# Several requests from one user (tabs, parallel fetches) can carry the same expiring token. Refresh tokens are
# single-use, so they share one refresh instead of the second one failing and logging the user out.
# ponytail: one lock and an in-process dict; with several app instances, Supabase's refresh-token reuse
# interval (10 s by default) covers the cross-instance race.
_refresh_lock = threading.Lock()
_recent_refreshes: dict[str, tuple[float, Any]] = {}
REFRESH_REUSE_SECONDS = 30


def refresh_once(refresh_token: str) -> Any:
    with _refresh_lock:
        now = time.monotonic()
        for token, (when, _) in list(_recent_refreshes.items()):
            if now - when > REFRESH_REUSE_SECONDS:
                del _recent_refreshes[token]
        if refresh_token in _recent_refreshes:
            return _recent_refreshes[refresh_token][1]
        try:
            session = get_supabase_client().auth.refresh_session(refresh_token).session
        except Exception as exc:
            logging.getLogger(__name__).info("session refresh failed, asking user to log in again: %s", exc)
            return None
        if session:
            _recent_refreshes[refresh_token] = (now, session)
        return session


def app_user_for_request(request: Request) -> dict[str, Any]:
    auth_user = require_auth(request)
    ensure_fresh_tokens(request)
    # The app user row rides along in the signed session cookie and is re-read whenever the token refreshes.
    cached = request.session.get("app_user")
    if isinstance(cached, dict) and cached.get("auth_user_id") == auth_user.get("id"):
        return cached["user"]
    access_token, refresh_token = get_request_session_tokens(request)
    app_user = get_or_create_app_user(
        auth_user_id=auth_user.get("id"),
        name=auth_user.get("email", "user").split("@")[0],
        access_token=access_token,
        refresh_token=refresh_token,
    )
    user = {"id": app_user["id"], "name": app_user.get("name"), "email": auth_user.get("email")}
    request.session["app_user"] = {"auth_user_id": auth_user.get("id"), "user": user}
    return user


def start_session(request: Request, user: Any, email: str, session: Any) -> None:
    request.session.clear()
    metadata = getattr(user, "user_metadata", None) or {}
    full_name = str(metadata.get("full_name") or metadata.get("name") or "").strip()
    request.session["auth_user"] = {"id": user.id, "email": email, "full_name": full_name[:80]}
    request.session["access_token"] = session.access_token
    request.session["refresh_token"] = session.refresh_token


EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def email_error(email: str) -> str | None:
    if not email or len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        return "Enter a valid email address, like name@company.com."
    return None


def password_error(password: str, email: str) -> str | None:
    if len(password) < 8:
        return "Use at least 8 characters for your password."
    if len(password) > 72:
        return "Use at most 72 characters for your password."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Use at least one letter and one number in your password."
    if password.lower() in (email, email.split("@")[0]):
        return "Your password can't be your email address."
    return None


def friendly_auth_error(exc: Exception) -> str:
    """Supabase auth errors in plain words; anything unexpected is logged, not shown."""
    text = str(exc).lower()
    if "invalid login credentials" in text:
        return "Email or password is incorrect."
    if "email not confirmed" in text:
        return "Please confirm your email first. Check your inbox (and spam folder) for the link."
    if "already registered" in text or "already been registered" in text or "already exists" in text:
        return "An account with this email already exists. Log in instead."
    if "rate limit" in text or "too many" in text or "429" in text:
        return "Too many attempts. Please wait a few minutes and try again."
    if "password" in text:
        return "That password isn't accepted. Use at least 8 characters with a letter and a number."
    logging.getLogger(__name__).error("auth error: %s", exc)
    return "Something went wrong. Please try again in a moment."


# Open signup means anyone can spend our Groq credits; each job lookup or draft costs several LLM calls.
DAILY_AI_LIMIT = int(os.getenv("DAILY_AI_LIMIT", "40"))


def enforce_daily_limit(request: Request, user_id: str) -> None:
    """Refuse a new job lookup, draft or revision once a user has made DAILY_AI_LIMIT of them in 24 hours (0 = off).

    ponytail: counts the user's own rows, which RLS lets them delete through the API to reset the count.
    Move to a server-side counter table if that's ever abused.
    """
    if DAILY_AI_LIMIT <= 0:
        return
    access_token, refresh_token = get_request_session_tokens(request)
    since = datetime.now(timezone.utc) - timedelta(days=1)
    if count_recent_ai_actions(user_id, since, access_token=access_token, refresh_token=refresh_token) >= DAILY_AI_LIMIT:
        raise ValueError(f"You've reached today's limit of {DAILY_AI_LIMIT} job lookups and drafts. Please try again tomorrow.")


GENERIC_ERROR = "Something went wrong on our side. Please try again in a moment."


def user_error(exc: Exception) -> str:
    """Our own messages (ValueError, timeouts, LLMError) are written for users; anything else is logged, not shown.

    Library errors can carry SQL, table names, URLs or provider ids, so they never reach the page.
    """
    library_value_errors = (json.JSONDecodeError, UnicodeError, PydanticValidationError)
    if isinstance(exc, (ValueError, TimeoutError, LLMError)) and not isinstance(exc, library_value_errors):
        return str(exc)
    logging.getLogger(__name__).error("request failed: %s", type(exc).__name__, exc_info=exc)
    return GENERIC_ERROR


def account_name(request: Request) -> str:
    """The name on the person's login: set on profile setup, or from their Google account."""
    return str((request.session.get("auth_user") or {}).get("full_name") or "").strip()


def letter_name_for(request: Request, resume_rows: list[dict[str, Any]]) -> str | None:
    """Name signed under every letter: the account name first (the user confirmed it), then the resume."""
    return account_name(request) or candidate_name_from_rows(resume_rows)


def candidate_name_from_rows(resume_rows: list[dict[str, Any]]) -> str | None:
    """Candidate name from the user's own resume rows; None if not identifiable."""
    for resume in resume_rows:
        name = extract_candidate_name(str(resume.get("extracted_text") or ""))
        if name:
            return name
    return None


def build_source_texts(
    resume_rows: list[dict[str, Any]],
    previous_letters: list[tuple[str, str]],
    job_description: str,
    selected_anchor: dict[str, Any],
    candidate_name: str | None,
    company_url: str,
) -> list[str]:
    """Everything a letter may draw facts from (full texts, not prompt snippets); used only for plain-code fact checks."""
    texts = [str(row.get("extracted_text") or "") for row in resume_rows]
    texts += [text for _, text in previous_letters]
    texts.append(job_description)
    texts += [value for value in selected_anchor.values() if isinstance(value, str)]
    texts += [candidate_name or "", company_url]
    return [text for text in texts if text]


def load_letter_source_texts(request: Request, user_id: str, job_application_id: str) -> list[str] | None:
    """Source texts for the letter page's checks when the route has not loaded them: three reads in parallel.

    None (checks run without the unsupported-facts note) if anything is missing or fails.
    """
    try:
        access_token, refresh_token = get_request_session_tokens(request)
        tokens = {"access_token": access_token, "refresh_token": refresh_token}
        with ThreadPoolExecutor(max_workers=3) as pool:
            job_future = pool.submit(get_job_application, user_id, job_application_id=job_application_id, **tokens)
            resumes_future = pool.submit(get_resumes_for_user, user_id, **tokens)
            letters_future = pool.submit(get_cover_letters_for_user, user_id, **tokens)
            job_application, resume_rows, letter_rows = job_future.result(), resumes_future.result(), letters_future.result()
        if not job_application:
            return None
        previous_letters = [(str(row.get("filename") or ""), str(row.get("content") or "")) for row in letter_rows if row.get("content")]
        selected_anchor = job_application.get("selected_anchor")
        return build_source_texts(
            resume_rows,
            previous_letters,
            str(job_application.get("job_description") or ""),
            selected_anchor if isinstance(selected_anchor, dict) else {},
            letter_name_for(request, resume_rows),
            str(job_application.get("company_url") or ""),
        )
    except Exception as exc:
        logger.warning("letter page sources unavailable (%s); showing checks without them", type(exc).__name__)
        return None


def resume_facts_for(
    resume_rows: list[dict[str, Any]],
    job_description: str,
    selected_anchor: dict[str, Any] | None,
    previous_letters: list[tuple[str, str]],
) -> str:
    """Relevant resume lines for the letter prompt; "" (letter still generated) if selection fails. Logs counts only."""
    try:
        resume_text = "\n\n".join(str(row.get("extracted_text") or "") for row in resume_rows if row.get("extracted_text"))
        facts = select_resume_facts(resume_text, job_description, selected_anchor, previous_letters)
    except Exception as exc:
        logging.getLogger(__name__).warning("resume facts selection failed (%s); continuing without them", type(exc).__name__)
        return ""
    logging.getLogger(__name__).info("resume facts: %d lines", len(facts.splitlines()))
    return facts


def profile_materials_fingerprint(resumes: list[dict[str, Any]], cover_letters: list[dict[str, Any]]) -> str:
    """Identify the exact resume/cover-letter set (ids and text) a profile was built from."""
    parts = sorted(f"resume:{row.get('id')}:{row.get('extracted_text') or ''}" for row in resumes)
    parts += sorted(f"cover_letter:{row.get('id')}:{row.get('content') or ''}" for row in cover_letters)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def load_profile_materials(request: Request, user_id: str) -> dict[str, Any]:
    """The four profile rows, fetched in parallel (each is one ~250 ms round trip to Supabase)."""
    access_token, refresh_token = get_request_session_tokens(request)
    tokens = {"access_token": access_token, "refresh_token": refresh_token}
    loaders = {
        "resumes": get_resumes_for_user,
        "cover_letters": get_cover_letters_for_user,
        "style_profile": get_style_profile,
        "candidate_profile": get_candidate_profile,
    }
    with ThreadPoolExecutor(max_workers=len(loaders)) as pool:
        futures = {name: pool.submit(load, user_id, **tokens) for name, load in loaders.items()}
        return {name: future.result() for name, future in futures.items()}


def build_profile_status(request: Request, user_id: str, materials: dict[str, Any] | None = None) -> dict[str, Any]:
    materials = materials or load_profile_materials(request, user_id)
    resumes, cover_letters = materials["resumes"], materials["cover_letters"]
    style_profile, candidate_profile = materials["style_profile"], materials["candidate_profile"]
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


def render_cover_letter_page(request: Request, user: dict[str, Any], job_application_id: str, chain: list[dict[str, Any]], error: str | None = None, *, dialog_step: str | None = None, feedback_draft: str = "", source_texts: list[str] | None = None):
    """Render the letter page for a job's revision chain (ordered oldest revision first).

    "Check before sending" notes are computed here on every render, never stored; without source_texts the
    unsupported-facts note is skipped.
    """
    current = chain[-1] if chain else None
    final_letter = next((record for record in chain if record.get("is_final")), None)
    shown = final_letter or current
    revision_number = int(current.get("revision_number") or 0) if current else 0
    shown_text = str(shown.get("content") or "") if shown else ""
    try:
        send_checks = send_check_items(shown_text, source_texts)
    except Exception as exc:
        logger.warning("letter checks failed (%s); page shown without them", type(exc).__name__)
        send_checks = []
    return templates.TemplateResponse("generated_cover_letter.html", {
        "request": request,
        "user": user,
        "send_checks": send_checks,
        "cover_letter": shown.get("content") if shown else "",
        "cover_letter_id": shown.get("id") if shown else "",
        "job_application_id": job_application_id,
        "revision_number": revision_number,
        "max_revisions": MAX_REVISIONS,
        "is_final": final_letter is not None,
        "can_revise": bool(current) and final_letter is None and revision_number < MAX_REVISIONS,
        "feedback_history": [record["feedback"] for record in chain if record.get("feedback")],
        # After the last revision the page asks whether the user is satisfied (answer stored on the current letter).
        "satisfaction_due": bool(current) and revision_number >= MAX_REVISIONS,
        # Latest saved answer anywhere in the chain (like is_final above), so it is found even if rows tie on revision_number.
        "satisfaction": next((record["satisfaction"] for record in reversed(chain) if record.get("satisfaction")), None),
        # Keeps a failed NO submission recoverable: reopen the feedback step with the user's text.
        "dialog_step": dialog_step,
        "feedback_draft": feedback_draft,
        "error": error,
    })


@app.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz():
    # For the host's health checks: answers without touching Supabase or Groq, so a slow provider can't trigger restarts.
    # commit: Render sets RENDER_GIT_COMMIT, so CI can tell when a new deploy is live.
    return {"ok": True, "commit": os.getenv("RENDER_GIT_COMMIT", "")[:7]}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request, "user": get_authenticated_user(request)})


def add_legal_page(page: str) -> None:
    async def legal_page(request: Request):
        return templates.TemplateResponse(f"{page}.html", {"request": request})
    app.add_api_route(f"/{page}", legal_page, methods=["GET"], response_class=HTMLResponse, include_in_schema=False)


for _page in ("privacy", "terms", "imprint"):
    add_legal_page(_page)


def app_url(request: Request, path: str) -> str:
    # APP_URL (e.g. https://yourdomain) wins behind a proxy, where request.base_url may say http://.
    return (os.getenv("APP_URL") or str(request.base_url)).rstrip("/") + path


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    if get_authenticated_user(request):
        return RedirectResponse(url="/profile/setup", status_code=303)
    return templates.TemplateResponse("signup.html", {"request": request, "error": None})


@app.post("/signup", response_class=HTMLResponse)
def signup(request: Request, email: str = Form(...), password: str = Form(...)):
    email = normalize_email(email)
    problem = email_error(email) or password_error(password, email)
    if problem:
        return templates.TemplateResponse("signup.html", {"request": request, "error": problem, "email": email})
    try:
        auth_response = get_supabase_client().auth.sign_up({
            "email": email,
            "password": password,
            "options": {"email_redirect_to": app_url(request, "/login?confirmed=1")},
        })
        session = getattr(auth_response, "session", None) or (auth_response.get("session") if isinstance(auth_response, dict) else None)
        user = getattr(auth_response, "user", None) or (auth_response.get("user") if isinstance(auth_response, dict) else None)
        if not user:
            raise RuntimeError("Sign-up was accepted but no user record was returned.")
        if not session and getattr(user, "identities", None) == []:
            # Supabase hides taken emails: it answers like a new signup but sends no email.
            return templates.TemplateResponse("signup.html", {"request": request, "error": "An account with this email already exists. Log in instead, or check your inbox for the first confirmation email.", "email": email})
        if not session:
            # Email confirmation is on: Supabase sends a link; nothing to log in to yet.
            request.session.clear()
            return templates.TemplateResponse("signup.html", {"request": request, "error": None, "sent_to": email})
        start_session(request, user, email, session)
        return RedirectResponse(url="/profile/setup", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse("signup.html", {"request": request, "error": friendly_auth_error(exc), "email": email})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, confirmed: str = ""):
    if get_authenticated_user(request):
        return RedirectResponse(url="/profile/setup", status_code=303)
    notice = "Email confirmed. Log in to continue." if confirmed else None
    return templates.TemplateResponse("login.html", {"request": request, "error": None, "notice": notice})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    email = normalize_email(email)
    problem = email_error(email) or (None if password else "Enter your password.")
    if problem:
        return templates.TemplateResponse("login.html", {"request": request, "error": problem, "email": email})
    try:
        auth_response = get_supabase_client().auth.sign_in_with_password({"email": email, "password": password})
        session = getattr(auth_response, "session", None) or (auth_response.get("session") if isinstance(auth_response, dict) else None)
        user = getattr(auth_response, "user", None) or (auth_response.get("user") if isinstance(auth_response, dict) else None)
        if not session or not user:
            raise RuntimeError("Invalid login credentials")
        start_session(request, user, email, session)
        return RedirectResponse(url="/profile/setup", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse("login.html", {"request": request, "error": friendly_auth_error(exc), "email": email})


GOOGLE_FAILED = "Google sign-in didn't finish. Please try again, or use your email and password."


@app.get("/auth/google")
def google_sign_in(request: Request):
    # PKCE: the verifier stays in this browser's signed session, so only the browser that started the sign-in can finish it.
    verifier = generate_pkce_verifier()
    request.session["oauth_verifier"] = verifier
    query = urlencode({
        "provider": "google",
        "redirect_to": app_url(request, "/auth/callback"),
        "code_challenge": generate_pkce_challenge(verifier),
        "code_challenge_method": "s256",
    })
    return RedirectResponse(url=f"{os.getenv('SUPABASE_URL', '').rstrip('/')}/auth/v1/authorize?{query}", status_code=303)


@app.get("/auth/callback", response_class=HTMLResponse)
def google_callback(request: Request, code: str = "", error_description: str = ""):
    verifier = request.session.pop("oauth_verifier", None)
    if not code or not verifier:
        if error_description:
            logging.getLogger(__name__).info("google sign-in stopped: %s", error_description)
        return templates.TemplateResponse("login.html", {"request": request, "error": GOOGLE_FAILED})
    try:
        auth_response = get_supabase_client().auth.exchange_code_for_session({
            "auth_code": code, "code_verifier": verifier, "redirect_to": app_url(request, "/auth/callback"),
        })
        if not auth_response.session or not auth_response.user:
            raise RuntimeError("Code exchange returned no session.")
    except Exception as exc:
        logging.getLogger(__name__).error("google sign-in failed: %s", exc)
        return templates.TemplateResponse("login.html", {"request": request, "error": GOOGLE_FAILED})
    start_session(request, auth_response.user, normalize_email(auth_response.user.email), auth_response.session)
    return RedirectResponse(url="/profile/setup", status_code=303)


@app.get("/logout")
def logout(request: Request):
    access_token = request.session.get("access_token")
    request.session.clear()
    if access_token:
        # Revoke this login's refresh token at Supabase too, so a copied session cookie stops working.
        try:
            get_supabase_client().auth.admin.sign_out(access_token, "local")
        except Exception as exc:
            logging.getLogger(__name__).info("sign-out at Supabase failed (session cleared locally anyway): %s", type(exc).__name__)
    return RedirectResponse(url="/login", status_code=303)


def render_profile_setup(request: Request, user: dict[str, Any], error: str | None = None, notice: str | None = None):
    materials = load_profile_materials(request, str(user["id"]))
    resumes = materials["resumes"]
    letter_name, source = account_name(request), "account"
    if not letter_name:
        letter_name = next((name for name in (extract_candidate_name(str(r.get("extracted_text") or "")) for r in resumes) if name), "")
        source = "resume" if letter_name else "none"
    return templates.TemplateResponse("profile_setup.html", {
        "request": request,
        "user": user,
        "status": build_profile_status(request, str(user["id"]), materials),
        "resume": resumes[-1] if resumes else None,
        "cover_letters": materials["cover_letters"],
        "letter_name": letter_name,
        "letter_name_source": source,
        "error": error,
        "notice": notice,
    })


FILE_UNAVAILABLE = "That file isn't available to download right now. Your profile still uses its text as usual."


@app.get("/profile/setup", response_class=HTMLResponse)
def profile_setup_page(request: Request, file: str = ""):
    try:
        return render_profile_setup(request, app_user_for_request(request), notice=FILE_UNAVAILABLE if file == "unavailable" else None)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)


def read_upload(upload: UploadFile, label: str) -> tuple[bytes, str]:
    """Return the file's bytes (for storage) and its extracted text; both checks run before anything is saved."""
    if not (upload.filename or "").lower().endswith(SUPPORTED_EXTENSIONS):
        raise ValueError(f"{upload.filename} can't be read. Please upload your {label} as PDF, Word (.docx) or .txt.")
    data = read_limited(upload)
    upload.file.seek(0)
    text = read_uploaded_text(upload)
    if not text or not text.strip():
        raise ValueError(f"We couldn't read any text in {upload.filename}. Please try a different file.")
    return data, text


# Keeps the "documents" bucket inside the plan's storage (Supabase free plan: 1 GB for the whole project).
# Past the budget, originals are no longer kept; uploads still work and the extracted text is saved.
STORAGE_BUDGET_BYTES = int(float(os.getenv("STORAGE_BUDGET_MB", "800")) * 1024 * 1024)


def store_original(request: Request, kind: str, upload: UploadFile, data: bytes) -> str | None:
    """Keep the original file in Supabase Storage. Optional: on failure the extracted text is still saved."""
    auth_id = (request.session.get("auth_user") or {}).get("id")
    access_token, _ = get_request_session_tokens(request)
    try:
        # Fails closed: if the bucket's size can't be read, the original isn't kept.
        used = documents_bucket_bytes(access_token=access_token)
        if used + len(data) > STORAGE_BUDGET_BYTES:
            logger.warning("storage budget reached: %.1f of %.0f MB used; original not kept kind=%s",
                           used / 1048576, STORAGE_BUDGET_BYTES / 1048576, kind)
            return None
        return upload_document(str(auth_id), kind, upload.filename, data, access_token=access_token)
    except Exception as exc:
        logger.warning("original file not stored kind=%s: %s: %s", kind, type(exc).__name__, exc)
        return None


def forget_originals(request: Request, paths: list[str | None]) -> None:
    access_token, _ = get_request_session_tokens(request)
    try:
        remove_documents([path for path in paths if path], access_token=access_token)
    except Exception as exc:
        logger.warning("original files not removed: %s: %s", type(exc).__name__, exc)


NAME_PATTERN = re.compile(r"^[^\W\d_]+(?:[ .'’-]+[^\W\d_]+)*\.?$")


@app.post("/profile/name", response_class=HTMLResponse)
def save_letter_name(request: Request, full_name: str = Form("")):
    """The name signed under the user's letters, kept on their Supabase login (user metadata)."""
    try:
        app_user_for_request(request)
        name = " ".join(full_name.split())
        if name and (len(name) > 80 or not NAME_PATTERN.match(name)):
            raise ValueError("Use letters only for your name, like Jane Doe or Mary-Jane O'Neil.")
        access_token, _ = get_request_session_tokens(request)
        save_user_full_name(name, access_token=access_token)
        request.session["auth_user"] = {**request.session.get("auth_user", {}), "full_name": name}
        return RedirectResponse(url="/profile/setup", status_code=303)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    except Exception as exc:
        return profile_setup_with_error(request, exc)


@app.post("/profile/resume", response_class=HTMLResponse)
def upload_resume(request: Request, resume: UploadFile = File(None)):
    try:
        user = app_user_for_request(request)
        user_id = str(user["id"])
        access_token, refresh_token = get_request_session_tokens(request)
        if resume is None or not resume.filename:
            raise ValueError("Please upload your resume before building your profile.")
        data, text = read_upload(resume, "resume")

        old_paths = [row.get("storage_path") for row in get_resumes_for_user(user_id, access_token=access_token, refresh_token=refresh_token)]
        path = store_original(request, "resume", resume, data)
        if old_paths:
            delete_all_resumes_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
        save_resume(user_id, resume.filename, path, text, access_token=access_token, refresh_token=refresh_token)
        forget_originals(request, old_paths)
        return RedirectResponse(url="/profile/setup", status_code=303)
    except Exception as exc:
        try:
            return render_profile_setup(request, app_user_for_request(request), error=user_error(exc))
        except HTTPException:
            return RedirectResponse(url="/login", status_code=303)


@app.post("/profile/resume/delete", response_class=HTMLResponse)
def delete_resume_route(request: Request, resume_id: str = Form(...)):
    try:
        user = app_user_for_request(request)
        access_token, refresh_token = get_request_session_tokens(request)
        rows = get_resumes_for_user(str(user["id"]), access_token=access_token, refresh_token=refresh_token)
        delete_resume_for_user(str(user["id"]), resume_id, access_token=access_token, refresh_token=refresh_token)
        forget_originals(request, [row.get("storage_path") for row in rows if str(row.get("id")) == resume_id])
        return RedirectResponse(url="/profile/setup", status_code=303)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    except Exception as exc:
        return profile_setup_with_error(request, exc)


@app.post("/profile/cover-letters", response_class=HTMLResponse)
def upload_cover_letters(request: Request, files: list[UploadFile] = File(...)):
    try:
        user = app_user_for_request(request)
        user_id = str(user["id"])
        access_token, refresh_token = get_request_session_tokens(request)
        selected_files = [uploaded for uploaded in files if (uploaded.filename or "").strip()]
        existing_count = len(get_cover_letters_for_user(user_id, access_token=access_token, refresh_token=refresh_token))

        if not selected_files:
            raise ValueError("Please select a cover letter before uploading.")
        room = MAX_COVER_LETTERS - existing_count
        if room <= 0:
            raise ValueError(f"You can upload a maximum of {MAX_COVER_LETTERS} previous cover letters.")
        if len(selected_files) > room:
            raise ValueError(f"You can add {room} more cover letter{'' if room == 1 else 's'} (maximum {MAX_COVER_LETTERS}).")

        validate_cover_letters(selected_files, min_count=1)
        # Read every file first, so one unreadable file doesn't leave the others half-saved.
        readable = [(uploaded, *read_upload(uploaded, "cover letter")) for uploaded in selected_files]
        for uploaded, data, text in readable:
            path = store_original(request, "letters", uploaded, data)
            save_cover_letter(user_id, uploaded.filename, text, storage_path=path, access_token=access_token, refresh_token=refresh_token)

        return RedirectResponse(url="/profile/setup", status_code=303)
    except Exception as exc:
        try:
            return render_profile_setup(request, app_user_for_request(request), error=user_error(exc))
        except HTTPException:
            return RedirectResponse(url="/login", status_code=303)


@app.post("/profile/cover-letters/delete", response_class=HTMLResponse)
def delete_cover_letter_route(request: Request, cover_letter_id: str = Form(...)):
    try:
        user = app_user_for_request(request)
        access_token, refresh_token = get_request_session_tokens(request)
        rows = get_cover_letters_for_user(str(user["id"]), access_token=access_token, refresh_token=refresh_token)
        delete_cover_letter_for_user(str(user["id"]), cover_letter_id, access_token=access_token, refresh_token=refresh_token)
        forget_originals(request, [row.get("storage_path") for row in rows if str(row.get("id")) == cover_letter_id])
        return RedirectResponse(url="/profile/setup", status_code=303)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    except Exception as exc:
        return profile_setup_with_error(request, exc)


@app.get("/profile/files/{kind}/{row_id}")
def download_original(request: Request, kind: str, row_id: str):
    """Send back one of the user's own original files, found through their own rows (RLS applies twice).
    Anything unavailable returns to profile setup with a quiet note, never an error page."""
    unavailable = RedirectResponse(url="/profile/setup?file=unavailable", status_code=303)
    try:
        user = app_user_for_request(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    loaders = {"resume": get_resumes_for_user, "letter": get_cover_letters_for_user}
    if kind not in loaders:
        return unavailable
    try:
        access_token, refresh_token = get_request_session_tokens(request)
        rows = loaders[kind](str(user["id"]), access_token=access_token, refresh_token=refresh_token)
        row = next((row for row in rows if str(row.get("id")) == row_id), None)
        path = (row or {}).get("storage_path") or ""
        if not path or path.startswith("/"):  # missing, or an old label from before files were kept
            return unavailable
        data = download_document(path, access_token=access_token)
    except Exception as exc:
        logger.warning("original file download failed: %s: %s", type(exc).__name__, exc)
        return unavailable
    filename = row.get("filename") or path.rsplit("/", 1)[-1]
    media_type = {".pdf": "application/pdf", ".docx": DOCX_MIME}.get(Path(filename).suffix.lower(), "text/plain; charset=utf-8")
    return Response(data, media_type=media_type, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


def profile_setup_with_error(request: Request, exc: Exception):
    try:
        return render_profile_setup(request, app_user_for_request(request), error=user_error(exc))
    except Exception:
        return RedirectResponse(url="/profile/setup", status_code=303)


@app.post("/profile/build", response_class=HTMLResponse)
def build_profile(request: Request):
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
            return render_profile_setup(request, app_user_for_request(request), error=user_error(exc))
        except HTTPException:
            return RedirectResponse(url="/login", status_code=303)


@app.get("/profile/ready", response_class=HTMLResponse)
def profile_ready(request: Request):
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


THIN_RESEARCH_CHARS = 800
USER_REASON_MAX_CHARS = 300
FALLBACK_URL_MAX_CHARS = 1500
FALLBACK_TEXT_MAX_CHARS = 3000


def company_fallback_section(company_extra: str) -> tuple[str, str] | None:
    """Turn the optional 'company text or another page URL' box into (source label, text), or None if it yields nothing."""
    extra = (company_extra or "").strip()
    if not extra:
        return None
    if "\n" not in extra and re.match(r"https?://\S+$", extra, re.IGNORECASE):
        try:
            # Same public-address (SSRF) checks as the main company URL.
            text = cap_text(extract_company_text(extra, fetch_company_html(extra)), FALLBACK_URL_MAX_CHARS)
        except Exception:
            return None
        return (extra, text) if text else None
    plain = BeautifulSoup(extra, "html.parser").get_text(" ")
    lines = [" ".join(line.split()) for line in plain.splitlines()]
    text = cap_text("\n".join(line for line in lines if line), FALLBACK_TEXT_MAX_CHARS)
    return ("pasted by the user", text) if text else None


def research_with_fallback(company_url: str, company_extra: str) -> dict[str, Any]:
    """Main company research plus the optional fallback box; re-raises the main error only if both give nothing."""
    try:
        research = research_company(company_url)
        main_error = None
    except Exception as exc:
        logger.warning("company research failed url=%s: %s: %s", company_url, type(exc).__name__, exc)
        research, main_error = None, exc
    fallback = company_fallback_section(company_extra)
    if research is None and fallback is None:
        raise main_error
    research = research or {}
    text = str(research.get("company_research") or "")
    pages = list(research["pages"]) if isinstance(research.get("pages"), list) else None
    if fallback:
        source, fallback_text = fallback
        text = f"{text}\n\n### Source: {source}\n{fallback_text}" if text else f"### Source: {source}\n{fallback_text}"
        if pages is not None and source.startswith("http"):
            pages.append(source)
    result = {"company_url": company_url, "company_research": text, "fallback_used": fallback is not None}
    if pages is not None:
        result["pages"] = pages
    return result


@app.get("/profile/job-input", response_class=HTMLResponse)
def job_input_page(request: Request):
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


NO_COMPANY_RESEARCH = (
    "The company website could not be read. For company evidence, use only what the job description says "
    "about the company, and do not invent any other company facts."
)


@app.post("/profile/job-input", response_class=HTMLResponse)
def submit_job_input(
    request: Request,
    job_description: str = Form(...),
    company_url: str = Form(...),
    company_extra: str = Form(""),
    user_reason: str = Form(""),
):
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
        enforce_daily_limit(request, user_id)
        try:
            company_research = research_with_fallback(company_url_value, company_extra)
        except Exception:
            # The site may block bots, be down or be private (already logged): carry on with the job description alone.
            company_research = {"company_url": company_url_value, "company_research": NO_COMPANY_RESEARCH}
        request.session.pop("research_notice", None)
        if len(company_research["company_research"]) < THIN_RESEARCH_CHARS and not (company_extra or "").strip():
            request.session["research_notice"] = "thin"  # shown once on the angles page; never blocks the flow
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
        research_pages = company_research.get("pages") if isinstance(company_research, dict) else None
        anchor_payload = generate_anchors(
            company_url=company_url_value,
            job_description=jd,
            company_research=company_research["company_research"],
            letters=letters,
            sources=research_pages if isinstance(research_pages, list) else None,
        )
        anchors = anchor_payload.get("anchors") if isinstance(anchor_payload, dict) else None
        reason = " ".join((user_reason or "").split())[:USER_REASON_MAX_CHARS]
        if reason and isinstance(anchors, list):
            # Travels with whichever angle is chosen (selected_anchor) into the letter and revision prompts.
            for anchor in anchors:
                if isinstance(anchor, dict):
                    anchor["user_reason"] = reason
        job_application = save_job_application(
            user_id,
            jd,
            company_url_value,
            anchors=anchors if isinstance(anchors, list) else [],
            access_token=access_token,
            refresh_token=refresh_token,
        )

        # Only the internal id travels in the (size-limited) session cookie; job details live in the database.
        request.session.pop("job_description", None)
        request.session.pop("company_url", None)
        request.session["job_application_id"] = str(job_application["id"])
        return RedirectResponse(url="/company/angles", status_code=303)
    except ValueError as exc:
        try:
            user = app_user_for_request(request)
            return templates.TemplateResponse("job_input.html", {
                "request": request,
                "user": user,
                "job_description": job_description or "",
                "company_url": company_url or "",
                "company_extra": company_extra or "",
                "user_reason": user_reason or "",
                "error": user_error(exc),
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
                "company_extra": company_extra or "",
                "user_reason": user_reason or "",
                "error": user_error(exc),
            })
        except HTTPException:
            return RedirectResponse(url="/login", status_code=303)


@app.get("/company/angles", response_class=HTMLResponse)
def company_angles_page(request: Request):
    try:
        user = app_user_for_request(request)
        user_id = str(user["id"])
        access_token, refresh_token = get_request_session_tokens(request)
        job_application_id = request.session.get("job_application_id")
        if not job_application_id:
            return RedirectResponse(url="/profile/job-input", status_code=303)
        job_application = get_job_application(user_id, job_application_id=job_application_id, access_token=access_token, refresh_token=refresh_token)
        if not job_application:
            return RedirectResponse(url="/profile/job-input", status_code=303)
        anchors = load_anchors(job_application)
        if not anchors:
            return RedirectResponse(url="/profile/job-input", status_code=303)
        db_company_url = job_application.get("company_url") if isinstance(job_application, dict) else ""
        db_job_description = job_application.get("job_description") if isinstance(job_application, dict) else ""
        company_url = (db_company_url or request.session.get("company_url") or "").strip()
        job_description = (db_job_description or request.session.get("job_description") or "").strip()
        return templates.TemplateResponse("company_angles.html", {
            "request": request,
            "user": user,
            "company_url": company_url,
            "job_description": job_description,
            "job_application_id": str(job_application["id"]),
            "anchors": anchors,
            "notice": request.session.pop("research_notice", None),  # shown once
            "error": None,
        })
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)


@app.post("/company/angles", response_class=HTMLResponse)
def select_company_angle(
    request: Request,
    selected_anchor: str = Form(...),
    job_description: str = Form(""),
    company_url: str = Form(""),
    job_application_id: str = Form(""),
):
    verified_job: dict[str, Any] | None = None
    try:
        user = app_user_for_request(request)
        if not selected_anchor:
            raise ValueError("Please choose one angle.")

        try:
            selected_index = int(selected_anchor)
        except (TypeError, ValueError):
            raise ValueError("The selected angle was not valid.") from None

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

        verified_job = job_application
        anchors = load_anchors(job_application)
        if not anchors:
            raise ValueError("No company angles were found for this application. Please go back to Job Input and generate angles again.")
        if selected_index < 0 or selected_index >= len(anchors):
            raise ValueError("The selected angle is outside the generated list.")
        enforce_daily_limit(request, user_id)

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
        resume_rows = get_resumes_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
        candidate_name = letter_name_for(request, resume_rows)

        job_application_id = str(job_application["id"])
        existing_chain = get_generated_cover_letters_for_job(user_id, job_application_id, access_token=access_token, refresh_token=refresh_token)
        if existing_chain:
            # Never add a second revision 0 to an existing chain: start a fresh application with the same job details.
            job_application = save_job_application(user_id, jd, company_url_value, anchors=anchors, access_token=access_token, refresh_token=refresh_token)
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
        try:
            source_texts = build_source_texts(resume_rows, previous_letters, jd, anchors[selected_index], candidate_name, company_url_value)
        except Exception:
            source_texts = None  # the letter is still generated, just without the soft fact/style checks
        letter = generate_cover_letter(
            job_description=jd,
            selected_anchors=selected_anchors,
            style_profile=style_profile,
            previous_letters=previous_letters,
            company_url=company_url_value,
            candidate_name=candidate_name,
            resume_facts=resume_facts_for(resume_rows, jd, anchors[selected_index], previous_letters),
            source_texts=source_texts,
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
        return render_cover_letter_page(request, user, job_application_id, chain, source_texts=source_texts)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    except Exception as exc:
        # Only reload angles for an application already verified as this user's, never the raw form value.
        fallback_anchors = load_anchors(verified_job)
        return templates.TemplateResponse("company_angles.html", {
            "request": request,
            "user": app_user_for_request(request),
            "company_url": company_url or request.session.get("company_url") or "",
            "job_description": job_description or request.session.get("job_description") or "",
            "job_application_id": request.session.get("job_application_id") or job_application_id or "",
            "anchors": fallback_anchors,
            "error": user_error(exc),
        })


@app.post("/cover-letter/revise", response_class=HTMLResponse)
def revise_cover_letter(request: Request, job_application_id: str = Form(...), feedback: str = Form("")):
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
        enforce_daily_limit(request, user_id)

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
        # One resume query, used for both the candidate's name and the resume facts.
        resume_rows = get_resumes_for_user(user_id, access_token=access_token, refresh_token=refresh_token)
        revision_job_description = str(job_application.get("job_description") or "")
        revision_company_url = str(job_application.get("company_url") or "")
        revision_candidate_name = letter_name_for(request, resume_rows)

        revised_letter = generate_cover_letter_revision(
            current_letter=str(current.get("content") or ""),
            user_feedback=feedback_text,
            job_description=revision_job_description,
            selected_anchors=[selected_anchor],
            style_profile=style_profile,
            previous_letters=previous_letters,
            company_url=revision_company_url,
            feedback_history=[record["feedback"] for record in chain if record.get("feedback")],
            candidate_name=revision_candidate_name,
            resume_facts=resume_facts_for(resume_rows, revision_job_description, selected_anchor, previous_letters),
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
        try:
            source_texts = build_source_texts(resume_rows, previous_letters, revision_job_description, selected_anchor, revision_candidate_name, revision_company_url)
        except Exception:
            source_texts = None  # the page still shows the other checks
        return render_cover_letter_page(request, user, job_application_id, chain, source_texts=source_texts)
    except Exception as exc:
        return render_cover_letter_page(request, user, job_application_id, chain, error=user_error(exc))


@app.post("/cover-letter/accept", response_class=HTMLResponse)
def accept_cover_letter(request: Request, job_application_id: str = Form(...), cover_letter_id: str = Form(...)):
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
        source_texts = load_letter_source_texts(request, user_id, job_application_id)
        return render_cover_letter_page(request, user, job_application_id, chain, source_texts=source_texts)
    except Exception as exc:
        return render_cover_letter_page(request, user, job_application_id, chain, error=user_error(exc))


MAX_LETTER_CHARS = 10_000


@app.post("/cover-letter/edit", response_class=HTMLResponse)
def edit_cover_letter(request: Request, job_application_id: str = Form(...), cover_letter_id: str = Form(...), content: str = Form("")):
    """Save the user's own edits to the letter shown. Not an AI revision, so it doesn't use one up."""
    try:
        user = app_user_for_request(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)

    chain: list[dict[str, Any]] = []
    try:
        user_id = str(user["id"])
        access_token, refresh_token = get_request_session_tokens(request)
        chain = get_generated_cover_letters_for_job(user_id, job_application_id, access_token=access_token, refresh_token=refresh_token)
        record = next((row for row in chain if str(row.get("id")) == cover_letter_id), None)
        if record is None:
            raise ValueError("The selected cover letter does not belong to this job application.")
        text = content.replace("\r\n", "\n").strip()
        if not text:
            raise ValueError("Your letter can't be empty. Undo your changes or add some text.")
        if len(text) > MAX_LETTER_CHARS:
            raise ValueError(f"That's a bit long for a cover letter: keep it under {MAX_LETTER_CHARS:,} characters.")
        if text != str(record.get("content") or "").strip():
            update_generated_cover_letter_content(user_id, cover_letter_id, text, access_token=access_token, refresh_token=refresh_token)
        return RedirectResponse(url="/cover-letter", status_code=303)
    except Exception as exc:
        return render_cover_letter_page(request, user, job_application_id, chain, error=user_error(exc))


@app.get("/cover-letter", response_class=HTMLResponse)
async def view_cover_letter(request: Request):
    """Show the current workflow's letter chain (id from the session, never the URL) so refreshes keep their state."""
    try:
        user = app_user_for_request(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)

    job_application_id = request.session.get("job_application_id")
    if not job_application_id:
        return RedirectResponse(url="/profile/job-input", status_code=303)
    access_token, refresh_token = get_request_session_tokens(request)
    chain = get_generated_cover_letters_for_job(str(user["id"]), job_application_id, access_token=access_token, refresh_token=refresh_token)
    if not chain:
        return RedirectResponse(url="/company/angles", status_code=303)
    # In a worker thread so the parallel reads don't block the event loop.
    source_texts = await anyio.to_thread.run_sync(load_letter_source_texts, request, str(user["id"]), job_application_id)
    return render_cover_letter_page(request, user, job_application_id, chain, source_texts=source_texts)


@app.post("/cover-letter/satisfaction", response_class=HTMLResponse)
async def submit_satisfaction(request: Request, job_application_id: str = Form(...), satisfied: str = Form(...), feedback: str = Form("")):
    """Post-revision satisfaction: YES accepts the current letter as final; NO stores product feedback (no new revision)."""
    try:
        user = app_user_for_request(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)

    chain: list[dict[str, Any]] = []
    try:
        user_id = str(user["id"])
        access_token, refresh_token = get_request_session_tokens(request)
        chain = get_generated_cover_letters_for_job(user_id, job_application_id, access_token=access_token, refresh_token=refresh_token)
        if not chain:
            raise ValueError("No generated cover letter exists for this job application yet.")
        current = chain[-1]
        if int(current.get("revision_number") or 0) < MAX_REVISIONS:
            raise ValueError("This question is only available after the final revision.")
        current_id = str(current["id"])

        if satisfied == "yes":
            save_letter_satisfaction(user_id, current_id, "satisfied", None, access_token=access_token, refresh_token=refresh_token)
            mark_generated_cover_letter_final(user_id, job_application_id, current_id, access_token=access_token, refresh_token=refresh_token)
        elif satisfied == "no":
            feedback_text = (feedback or "").strip()
            if not feedback_text:
                raise ValueError("Please write your feedback before submitting.")
            save_letter_satisfaction(user_id, current_id, "not_satisfied", feedback_text, access_token=access_token, refresh_token=refresh_token)
        else:
            raise ValueError("Please choose Yes or No.")

        request.session["job_application_id"] = job_application_id
        return RedirectResponse(url="/cover-letter", status_code=303)
    except Exception as exc:
        if isinstance(exc, ValueError):
            message = str(exc)
        else:
            logger.exception("Saving the post-revision satisfaction response failed.")
            message = "We couldn't save your response. Please try again."
        retry_feedback = satisfied == "no"
        return render_cover_letter_page(
            request, user, job_application_id, chain, error=message,
            dialog_step="feedback" if retry_feedback else None,
            feedback_draft=feedback if retry_feedback else "",
        )


@app.get("/cover-letter/pdf")
async def download_cover_letter_pdf(request: Request):
    """Download the accepted (final) letter of the current workflow as a PDF."""
    try:
        user = app_user_for_request(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)

    job_application_id = request.session.get("job_application_id")
    if not job_application_id:
        return RedirectResponse(url="/profile/job-input", status_code=303)
    access_token, refresh_token = get_request_session_tokens(request)
    chain = get_generated_cover_letters_for_job(str(user["id"]), job_application_id, access_token=access_token, refresh_token=refresh_token)
    final_letter = next((record for record in chain if record.get("is_final")), None)
    if not final_letter:
        return RedirectResponse(url="/cover-letter", status_code=303)

    return Response(
        content=build_cover_letter_pdf(str(final_letter.get("content") or "")),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="cover_letter.pdf"'},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
