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
SESSION_SECRET=...           # long random string; required in production (without it, logins reset on every restart)
```

In the Supabase SQL editor, run in order: `supabase/schema.sql`, `migration_add_uuid_defaults.sql`, `migration_chunk9_schema_reconciliation.sql`, `migration_add_job_applications.sql`, `migration_chunk10_revision_loop.sql`.

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

## Tests

```bash
python -m pytest
```

The evaluator test calls the live Groq API and is skipped without `GROQ_API_KEY`. `tests/test_full_journey.py` walks the whole flow (signup to accepted letter) on the fake backend, and `tests/test_traffic_and_isolation.py` checks that slow requests don't block other users and that users never see each other's data.
