import re
import unittest
from pathlib import Path

from scripts import letter_timing

APPS = [
    {"id": "aaaaaaaa-1", "created_at": "2026-10-01T10:00:00+00:00"},
    {"id": "bbbbbbbb-2", "created_at": "2026-10-01T11:00:00Z"},
    {"id": "cccccccc-3", "created_at": "2026-10-01T12:00:00+00:00"},
    {"id": "dddddddd-4", "created_at": "2026-10-01T13:00:00+00:00"},
]
LETTERS = [
    # 1: answered the satisfaction question after revision 3 (that time wins over the final draft's time).
    {"job_application_id": "aaaaaaaa-1", "revision_number": 0, "is_final": False, "created_at": "2026-10-01T10:02:00+00:00"},
    {"job_application_id": "aaaaaaaa-1", "revision_number": 3, "is_final": True, "created_at": "2026-10-01T10:10:00+00:00", "satisfaction_submitted_at": "2026-10-01T10:12:30+00:00"},
    # 2: accepted as final early; no accept time is stored, so the final draft's time is used.
    {"job_application_id": "bbbbbbbb-2", "revision_number": 1, "is_final": True, "created_at": "2026-10-01T11:06:00+00:00", "satisfaction_submitted_at": None},
    # 3: a draft but never finished. 4: no letter at all.
    {"job_application_id": "cccccccc-3", "revision_number": 0, "is_final": False, "created_at": "2026-10-01T12:03:00+00:00"},
]


class LetterTimingTests(unittest.TestCase):
    def test_elapsed_minutes_per_application(self):
        rows = letter_timing.timing_rows(APPS, LETTERS)
        self.assertEqual([row["minutes"] for row in rows], [12.5, 6.0, None, None])
        self.assertEqual([row["basis"] for row in rows], [letter_timing.SATISFACTION, letter_timing.FINAL_DRAFT, "not finished", "no letter"])
        self.assertEqual([row["letters"] for row in rows], [2, 1, 1, 0])

    def test_report_marks_unfinished_and_estimated_rows(self):
        report = letter_timing.format_report(letter_timing.timing_rows(APPS, LETTERS))
        self.assertIn("4 applications, 2 finished, median 9.2 min", report)
        self.assertIn("not finished", report)
        self.assertIn("accept time is not stored", report)

    def test_script_only_reads(self):
        source = Path(letter_timing.__file__).read_text(encoding="utf-8")
        table_calls = re.findall(r"\.table\([^)]*\)\s*\.(\w+)\(", source)
        self.assertTrue(table_calls)
        self.assertEqual(set(table_calls), {"select"})
        self.assertNotIn(".rpc(", source)


if __name__ == "__main__":
    unittest.main()
