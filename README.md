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
SESSION_SECRET=...           # any long random string
```

In the Supabase SQL editor, run in order: `supabase/schema.sql`, `migration_add_uuid_defaults.sql`, `migration_chunk9_schema_reconciliation.sql`, `migration_add_job_applications.sql`, `migration_chunk10_revision_loop.sql`.

## Run

```bash
uvicorn app:app --reload
```

Open http://localhost:8000.

## Tests

```bash
python -m pytest
```

The evaluator test calls the live Groq API and is skipped without `GROQ_API_KEY`.
