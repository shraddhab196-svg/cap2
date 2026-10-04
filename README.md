# Cover Letter AI

FastAPI app that learns your writing style from past cover letters and your resume, researches a company, and drafts a tailored cover letter (Groq LLM, Supabase for auth and storage).

## Setup

Python 3.10+.

```bash
pip install -r requirements.txt
```

Create a `.env` in the project root:

```text
SUPABASE_URL=...
SUPABASE_ANON_KEY=...        # or SUPABASE_KEY
GROQ_API_KEY=...
GROQ_MODEL=qwen/qwen3.8-27b  # optional, this is the default
# LLM_MODEL=groq/qwen/qwen3.8-27b             # optional: any LiteLLM model name, overrides GROQ_MODEL
# LLM_FALLBACK_MODELS=openrouter/qwen/qwen3-32b  # optional: tried in order when the primary is busy or down
SESSION_SECRET=...           # long random string; required in production (without it, logins reset on every restart)
```

In the Supabase SQL editor, run in order: `supabase/schema.sql`, `migration_add_uuid_defaults.sql`, `migration_chunk9_schema_reconciliation.sql`, `migration_add_job_applications.sql`, `migration_chunk10_revision_loop.sql`.

## LLM providers

Every LLM call goes through `src/llm_client.py` ([LiteLLM](https://docs.litellm.ai/)), so switching or adding a provider is configuration, not code:

- `LLM_MODEL` picks the primary model, e.g. `groq/qwen/qwen3.8-27b`, `openrouter/...`, `together_ai/...`. Each provider reads its own key (`GROQ_API_KEY`, `OPENROUTER_API_KEY`, `TOGETHERAI_API_KEY`, ...).
- `LLM_FALLBACK_MODELS` (comma-separated) is tried in order when the primary is rate-limited or failing. Off by default.
- Every call logs one line with the step, the model that answered, tokens and cost: `llm usage step=letter model=groq/qwen/qwen3.8-27b prompt=2140 completion=812 cost=$0.00110 seconds=3.1`.
- Users never see raw provider errors (they can contain account ids); a rate limit shows "Lots of people are writing right now".

Before adding a provider: it will receive users' resumes and letters, so check its data-retention and training terms (and EU hosting if users are in the EU). Fallback models must also support JSON output, and prompts are tuned for Qwen, so try a few real letters on a new model first.

## Run

```bash
uvicorn app:app --reload
```

Open http://localhost:8000.

For more than one process (e.g. `--workers 4`), `SESSION_SECRET` must be set so every worker accepts the same login cookie. Each process runs slow Groq/Supabase calls on up to `WORKER_THREADS` threads (default 100), so one person's letter never blocks anyone else.

### Without any keys

```bash
python scripts/fake_backend.py
```

Runs the real app against an in-memory Supabase and a fake Groq that waits `FAKE_LATENCY` seconds (default 3). Sign up with any email. To see the error paths: an email starting with `fail` can't log in, a company URL containing `fail` times out, and a job description containing `FAIL` makes generation fail. Data resets on restart.

## Website

The public site (landing, privacy, terms, imprint, 404) is built from the app's own templates and published to GitHub Pages by `.github/workflows/pages.yml` on every push to `main`.

```bash
python scripts/build_site.py
```

Writes `_site/`. Company details for the legal pages live in `site/config.json`; until they're filled in, the pages show "to be added". The repository variables `SITE_URL` and `APP_URL` override the URLs there (e.g. once the app has its real address). `static/img/og.png` (the link preview card) and `icon-192.png` are screenshots of `site/og.html`: open it in Chrome headless with `--window-size=1200,630 --screenshot` (append `?icon` and use 192x192 for the icon).

One-time setup, by a repository admin: Settings > Pages > Source: **GitHub Actions**.

## Tests

```bash
python -m pytest
```

The evaluator test calls the live Groq API and is skipped without `GROQ_API_KEY`. `tests/test_full_journey.py` walks the whole flow (signup to accepted letter) on the fake backend, and `tests/test_traffic_and_isolation.py` checks that slow requests don't block other users and that users never see each other's data.
