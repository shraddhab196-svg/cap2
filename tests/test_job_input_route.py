import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient

import app

SAMPLE_ANCHORS = json.loads((app.BASE_DIR / "company_anchors.json").read_text(encoding="utf-8"))["anchors"]
USER = {"id": "00000000-0000-0000-0000-000000000001", "email": "demo@example.com"}
JOB_APPLICATION = {
    "id": "job-1",
    "user_id": "00000000-0000-0000-0000-000000000001",
    "job_description": "Senior AI engineer",
    "company_url": "https://example.com",
    "anchors": SAMPLE_ANCHORS,
}


class JobInputRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)
        job_patchers = [
            patch.object(app, "save_job_application", return_value=JOB_APPLICATION),
            patch.object(app, "get_job_application", return_value=JOB_APPLICATION),
            patch.object(app, "update_job_application_anchor", return_value=JOB_APPLICATION),
            patch.object(app, "get_generated_cover_letters_for_job", return_value=[]),
        ]
        self.mock_save_job, self.mock_get_job, self.mock_update_anchor, self.mock_get_chain = [patcher.start() for patcher in job_patchers]
        for patcher in job_patchers:
            self.addCleanup(patcher.stop)

    def submit_job_input(self):
        with patch.object(app, "app_user_for_request", return_value=USER), \
             patch.object(app, "research_company", return_value={"company_research": "company narrative"}), \
             patch.object(app, "get_cover_letters_for_user", return_value=[{"filename": "letter1.txt", "content": "I led a project"}]), \
             patch.object(app, "generate_anchors", return_value={"anchors": []}):
            return self.client.post(
                "/profile/job-input",
                data={"job_description": "Senior AI engineer", "company_url": "https://example.com"},
                follow_redirects=False,
            )

    def generation_mocks(self, stack: ExitStack):
        stack.enter_context(patch.object(app, "app_user_for_request", return_value=USER))
        stack.enter_context(patch.object(app, "get_style_profile", return_value={"profile": {"tone": "professional"}}))
        stack.enter_context(patch.object(app, "get_candidate_profile", return_value={"profile": {"skills": ["python"]}}))
        stack.enter_context(patch.object(app, "get_cover_letters_for_user", return_value=[{"filename": "letter1.txt", "content": "I built systems."}]))
        stack.enter_context(patch.object(app, "generate_cover_letter", return_value="Fresh letter for this application."))
        return stack.enter_context(patch.object(app, "save_generated_cover_letter", return_value={"id": "new-letter"}))

    def test_job_input_page_renders(self):
        with patch.object(app, "app_user_for_request", return_value={"id": "user-123", "email": "demo@example.com"}):
            response = self.client.get("/profile/job-input")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Job Description", response.text)
        self.assertIn("Company URL", response.text)
        # Shared header: initials avatar with Profile Setup + Log out in the dropdown, no standalone links.
        self.assertIn('<span class="avatar">D</span>', response.text)
        self.assertIn('href="/profile/setup">Profile Setup</a>', response.text)
        self.assertIn('href="/logout">Log out</a>', response.text)
        self.assertNotIn("Edit profile", response.text)
        self.assertNotIn("demo@example.com", response.text)

    def test_job_input_valid_submission_calls_existing_workflow(self):
        user = {"id": "00000000-0000-0000-0000-000000000001", "email": "demo@example.com"}
        with patch.object(app, "app_user_for_request", return_value=user), \
             patch.object(app, "research_company", return_value={"company_research": "company narrative"}) as mock_research, \
             patch.object(app, "get_cover_letters_for_user", return_value=[{"filename": "letter1.txt", "content": "I led a project"}]) as mock_letters, \
             patch.object(app, "generate_anchors", return_value={"company_url": "https://example.com", "anchors": [{"title": "Example anchor"}]}) as mock_generate, \
             patch.object(app, "get_candidate_profile", return_value=None), \
             patch.object(app, "get_style_profile", return_value=None):
            response = self.client.post(
                "/profile/job-input",
                data={
                    "job_description": "We need a senior engineer.",
                    "company_url": "https://example.com",
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers.get("location"), "/company/angles")
        self.assertEqual(mock_research.call_count, 1)
        self.assertEqual(mock_generate.call_count, 1)
        self.assertEqual(self.mock_save_job.call_count, 1)
        self.assertEqual(self.mock_save_job.call_args.kwargs["anchors"], [{"title": "Example anchor"}])
        self.assertEqual(mock_letters.call_count, 1)
        self.assertEqual(mock_generate.call_args.kwargs["letters"], [("letter1.txt", "I led a project")])

    def test_company_angles_page_renders(self):
        first_title = json.loads((app.BASE_DIR / "company_anchors.json").read_text(encoding="utf-8"))["anchors"][0]["title"]
        self.submit_job_input()
        with patch.object(app, "app_user_for_request", return_value={"id": "user-123", "email": "demo@example.com"}):
            response = self.client.get("/company/angles")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Generated angles", response.text)
        self.assertIn(first_title, response.text)
        self.assertIn('value="Senior AI engineer"', response.text)
        # The internal id is only present as a hidden form value, never as visible text.
        self.assertIn('<input type="hidden" name="job_application_id" value="job-1" />', response.text)
        self.assertEqual(response.text.count("job-1"), 1)
        self.assertEqual(self.mock_get_job.call_args.kwargs.get("job_application_id"), "job-1")

    def test_company_angles_page_redirects_without_job_application(self):
        self.mock_get_job.return_value = None
        with patch.object(app, "app_user_for_request", return_value={"id": "user-123", "email": "demo@example.com"}):
            response = self.client.get("/company/angles", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers.get("location"), "/profile/job-input")

    def test_angle_submission_without_job_application_returns_to_job_input(self):
        self.mock_get_job.return_value = None
        with patch.object(app, "app_user_for_request", return_value={"id": "user-123", "email": "demo@example.com"}), \
             patch.object(app, "generate_cover_letter") as mock_generate:
            response = self.client.post("/company/angles", data={"selected_anchor": "0", "job_application_id": "another-users-job"}, follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Paste the role details", response.text)
        self.assertIn("No saved job details were found", response.text)
        self.assertNotIn("another-users-job", response.text)
        self.assertEqual(mock_generate.call_count, 0)
        self.assertEqual(self.mock_update_anchor.call_count, 0)

    def test_resubmitting_same_job_creates_fresh_application_and_chain(self):
        applications = {
            "job-A": {**JOB_APPLICATION, "id": "job-A"},
            "job-B": {**JOB_APPLICATION, "id": "job-B"},
        }
        chains = {
            "job-A": [
                {"id": "a0", "revision_number": 0, "content": "Old letter A", "feedback": None, "is_final": False},
                {"id": "a1", "revision_number": 1, "content": "Old revision A", "feedback": "old feedback from A", "is_final": True},
            ],
        }
        self.mock_save_job.side_effect = [applications["job-A"], applications["job-B"]]
        self.mock_get_job.side_effect = lambda user_id, job_application_id=None, **_: applications.get(job_application_id)
        self.mock_get_chain.side_effect = lambda user_id, job_application_id, **_: chains.get(job_application_id, [])

        self.submit_job_input()
        self.submit_job_input()
        with ExitStack() as stack:
            mock_save_letter = self.generation_mocks(stack)
            angles = self.client.get("/company/angles")
            response = self.client.post("/company/angles", data={"selected_anchor": "0", "job_application_id": "job-B"})

        self.assertEqual(self.mock_save_job.call_count, 2)
        self.assertIn('value="job-B"', angles.text)
        self.assertNotIn("job-A", angles.text)
        self.assertEqual(self.mock_update_anchor.call_args.args[1], "job-B")
        self.assertEqual(mock_save_letter.call_args.kwargs["job_application_id"], "job-B")
        self.assertEqual(mock_save_letter.call_args.kwargs["revision_number"], 0)
        self.assertFalse(mock_save_letter.call_args.kwargs["is_final"])
        self.assertIsNone(mock_save_letter.call_args.kwargs.get("feedback"))
        self.assertIn("Original draft", response.text)
        self.assertIn("Fresh letter for this application.", response.text)
        for inherited in ("Old revision A", "old feedback from A", "Final version accepted", "job-A"):
            self.assertNotIn(inherited, response.text)
        self.assertEqual(response.text.count("job-B"), response.text.count('value="job-B"'))

    def test_back_to_angles_on_existing_chain_starts_new_application(self):
        existing = {**JOB_APPLICATION, "id": "job-A"}
        self.mock_get_job.side_effect = lambda user_id, job_application_id=None, **_: existing if job_application_id == "job-A" else None
        self.mock_get_chain.side_effect = lambda user_id, job_application_id, **_: (
            [{"id": "a0", "revision_number": 0, "content": "Old letter A", "feedback": None, "is_final": False}] if job_application_id == "job-A" else []
        )
        self.mock_save_job.return_value = {**JOB_APPLICATION, "id": "job-C"}

        with ExitStack() as stack:
            mock_save_letter = self.generation_mocks(stack)
            response = self.client.post("/company/angles", data={"selected_anchor": "0", "job_application_id": "job-A"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.mock_save_job.call_count, 1)
        self.assertEqual(self.mock_save_job.call_args.args[1:3], ("Senior AI engineer", "https://example.com"))
        self.assertEqual(self.mock_save_job.call_args.kwargs["anchors"], SAMPLE_ANCHORS)  # angles carry over to the new application
        self.assertEqual(self.mock_update_anchor.call_args.args[1], "job-C")
        self.assertEqual(mock_save_letter.call_count, 1)
        self.assertEqual(mock_save_letter.call_args.kwargs["job_application_id"], "job-C")
        self.assertEqual(mock_save_letter.call_args.kwargs["revision_number"], 0)
        self.assertNotIn("Old letter A", response.text)

    def test_all_authenticated_pages_share_one_header(self):
        self.submit_job_input()
        pages = {}
        with ExitStack() as stack:
            self.generation_mocks(stack)
            stack.enter_context(patch.object(app, "get_resumes_for_user", return_value=[]))
            pages["profile_setup"] = self.client.get("/profile/setup")
            pages["profile_ready"] = self.client.get("/profile/ready")
            pages["job_input"] = self.client.get("/profile/job-input")
            pages["company_angles"] = self.client.get("/company/angles")
            pages["generated_letter"] = self.client.post("/company/angles", data={"selected_anchor": "0", "job_application_id": "job-1"})

        for name, response in pages.items():
            with self.subTest(name):
                html = response.text
                self.assertEqual(response.status_code, 200)
                self.assertEqual(html.count('<details class="account-menu">'), 1)
                dropdown = html.split('<div class="account-dropdown">', 1)[1].split("</div>", 1)[0]
                self.assertEqual([line.strip() for line in dropdown.strip().splitlines()], ['<a href="/profile/setup">Profile Setup</a>', '<a href="/logout">Log out</a>'])
                self.assertEqual(html.count("menu.open = false"), 1)
                for old_link in ("Edit profile", "Back to angles", 'class="topbar"', "demo@example.com"):
                    self.assertNotIn(old_link, html)

    def test_job_input_requires_both_fields(self):
        with patch.object(app, "app_user_for_request", return_value={"id": "user-123", "email": "demo@example.com"}):
            response = self.client.post(
                "/profile/job-input",
                data={
                    "job_description": "",
                    "company_url": "",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("required", response.text.lower())

    def test_angle_submission_generates_cover_letter(self):
        user = {"id": "00000000-0000-0000-0000-000000000001", "email": "demo@example.com"}
        with patch.object(app, "app_user_for_request", return_value=user), \
             patch.object(app, "get_style_profile", return_value={"profile": {"tone": "professional"}}), \
             patch.object(app, "get_candidate_profile", return_value={"profile": {"skills": ["python"]}}), \
             patch.object(app, "get_cover_letters_for_user", return_value=[{"filename": "letter1.txt", "content": "I built systems."}]), \
             patch.object(app, "generate_cover_letter", return_value="Dear hiring manager, I am excited to apply.") as mock_generate, \
             patch.object(app, "save_generated_cover_letter", return_value={"id": "cover-1"}) as mock_save:
            response = self.client.post(
                "/company/angles",
                data={
                    "selected_anchor": "0",
                    "job_description": "Senior AI engineer",
                    "company_url": "https://example.com",
                    "job_application_id": "job-1",
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("GENERATED COVER LETTER", response.text)
        self.assertEqual(mock_generate.call_count, 1)
        self.assertEqual(mock_save.call_count, 1)

    def test_angle_submission_shows_generation_error(self):
        user = {"id": "00000000-0000-0000-0000-000000000001", "email": "demo@example.com"}
        with patch.object(app, "app_user_for_request", return_value=user), \
             patch.object(app, "get_style_profile", return_value={"profile": {"tone": "professional"}}), \
             patch.object(app, "get_candidate_profile", return_value={"profile": {"skills": ["python"]}}), \
             patch.object(app, "get_cover_letters_for_user", return_value=[{"filename": "letter1.txt", "content": "I built systems."}]), \
             patch.object(app, "generate_cover_letter", side_effect=RuntimeError("Groq API is unavailable")):
            response = self.client.post(
                "/company/angles",
                data={
                    "selected_anchor": "1",
                    "job_description": "Senior AI engineer",
                    "company_url": "https://example.com",
                    "job_application_id": "job-1",
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Groq API is unavailable", response.text)


if __name__ == "__main__":
    unittest.main()
