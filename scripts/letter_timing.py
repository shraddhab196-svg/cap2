"""Read-only report: minutes from starting a job application to finishing its letter.

    python scripts/letter_timing.py --email you@example.com      # asks for the password

Signs in as that user with the anon key, so row-level security limits the report to their own applications.
Only SELECTs; nothing is written. Not used by the app.

Start: job_applications.created_at.
End, using timestamps the schema already has:
  - generated_cover_letters.satisfaction_submitted_at (the post-revision question was answered), else
  - created_at of the letter marked final. "Accept as final" stores no time, so this is when the accepted
    draft was written: the real finish was at or after it.
Applications with neither are listed as not finished.
"""
from __future__ import annotations

import argparse
import getpass
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SATISFACTION = "satisfaction answered"
FINAL_DRAFT = "final draft written*"


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def timing_rows(applications: list[dict[str, Any]], letters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per application, oldest first: start, end, elapsed minutes (None when not finished) and the end basis."""
    rows = []
    for application in sorted(applications, key=lambda row: str(row.get("created_at") or "")):
        started = parse_time(application.get("created_at"))
        chain = [letter for letter in letters if str(letter.get("job_application_id")) == str(application.get("id"))]
        answered = [time for time in (parse_time(letter.get("satisfaction_submitted_at")) for letter in chain) if time]
        final = next((letter for letter in chain if letter.get("is_final")), None)
        if answered:
            ended, basis = max(answered), SATISFACTION
        elif final and parse_time(final.get("created_at")):
            ended, basis = parse_time(final.get("created_at")), FINAL_DRAFT
        else:
            ended, basis = None, "not finished" if chain else "no letter"
        minutes = round((ended - started).total_seconds() / 60, 1) if started and ended else None
        rows.append({
            "application": str(application.get("id") or "")[:8],
            "started": started,
            "ended": ended,
            "minutes": minutes,
            "letters": len(chain),
            "basis": basis,
        })
    return rows


def format_report(rows: list[dict[str, Any]]) -> str:
    def when(value: datetime | None) -> str:
        return value.strftime("%Y-%m-%d %H:%M") if value else "-"

    lines = [f"{'application':<12} {'started (UTC)':<17} {'ended (UTC)':<17} {'minutes':>8} {'letters':>7}  end"]
    for row in rows:
        minutes = f"{row['minutes']:.1f}" if row["minutes"] is not None else "-"
        lines.append(f"{row['application']:<12} {when(row['started']):<17} {when(row['ended']):<17} {minutes:>8} {row['letters']:>7}  {row['basis']}")
    finished = [row["minutes"] for row in rows if row["minutes"] is not None]
    lines.append("")
    lines.append(f"{len(rows)} applications, {len(finished)} finished" + (f", median {statistics.median(finished):.1f} min" if finished else ""))
    if any(row["basis"] == FINAL_DRAFT for row in rows):
        lines.append("* accept time is not stored; the real finish was at or after this time.")
    return "\n".join(lines)


def fetch_rows(email: str, password: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sign in as the user and read their applications and letter timestamps (SELECT only, no letter text)."""
    from supabase import create_client

    from src.database import load_supabase_env

    url, key = load_supabase_env()
    client = create_client(url, key)
    auth = client.auth.sign_in_with_password({"email": email, "password": password})
    client.postgrest.auth(auth.session.access_token)
    users = client.table("users").select("id").eq("auth_user_id", auth.user.id).limit(1).execute().data or []
    if not users:
        return [], []
    user_id = users[0]["id"]
    applications = client.table("job_applications").select("id, created_at").eq("user_id", user_id).execute().data or []
    letters = (
        client.table("generated_cover_letters")
        .select("job_application_id, revision_number, is_final, created_at, satisfaction_submitted_at")
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )
    return applications, letters


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    applications, letters = fetch_rows(args.email, getpass.getpass("Password: "))
    print(format_report(timing_rows(applications, letters)))


if __name__ == "__main__":
    main()
