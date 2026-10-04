import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app

USER = {"id": "00000000-0000-0000-0000-000000000001", "email": "demo@example.com"}
RESUMES = [{"id": "r1", "filename": "resume.pdf", "extracted_text": "Resume text"}]
LETTERS = [{"id": f"c{i}", "filename": f"letter{i}.txt", "content": f"Letter {i} text"} for i in range(1, 6)]
BUILD_BUTTON = "Build My Profile"
CONTINUE_FORM = 'action="/profile/ready"'


def built_profile(resumes, letters):
    return {"profile": {"candidate_evidence": {}, "source_materials_fingerprint": app.profile_materials_fingerprint(resumes, letters)}}


class ProfileSetupButtonTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)

    def render(self, resumes, letters, candidate_profile, style_profile=None, user=USER):
        with patch.object(app, "app_user_for_request", return_value=user), \
             patch.object(app, "get_resumes_for_user", return_value=resumes), \
             patch.object(app, "get_cover_letters_for_user", return_value=letters), \
             patch.object(app, "get_candidate_profile", return_value=candidate_profile), \
             patch.object(app, "get_style_profile", return_value=style_profile if style_profile is not None else ({"profile": {"tone": "x"}} if candidate_profile else None)):
            response = self.client.get("/profile/setup")
        self.assertEqual(response.status_code, 200)
        return response.text

    def assert_build(self, html):
        self.assertIn(BUILD_BUTTON, html)
        self.assertNotIn(CONTINUE_FORM, html)

    def test_never_built_shows_build(self):
        self.assert_build(self.render(RESUMES, LETTERS, None))

    def test_built_and_unchanged_shows_continue(self):
        html = self.render(RESUMES, LETTERS, built_profile(RESUMES, LETTERS))
        self.assertIn(CONTINUE_FORM, html)
        self.assertIn(">Continue</button>", html)
        self.assertNotIn(BUILD_BUTTON, html)
        self.assertNotIn('action="/profile/build"', html)

    def test_any_material_change_shows_build(self):
        profile = built_profile(RESUMES, LETTERS)
        changes = {
            "cover letter removed": (RESUMES, LETTERS[:4]),
            "cover letter replaced": (RESUMES, LETTERS[:4] + [{"id": "c9", "filename": "new.txt", "content": "New letter"}]),
            "cover letter updated": (RESUMES, LETTERS[:4] + [{**LETTERS[4], "content": "Edited text"}]),
            "resume removed": ([], LETTERS),
            "resume replaced": ([{"id": "r2", "filename": "resume2.pdf", "extracted_text": "Resume text"}], LETTERS),
            "resume updated": ([{**RESUMES[0], "extracted_text": "Edited resume"}], LETTERS),
            "resume added": (RESUMES + [{"id": "r3", "filename": "extra.pdf", "extracted_text": "Extra"}], LETTERS),
        }
        for label, (resumes, letters) in changes.items():
            with self.subTest(label):
                self.assert_build(self.render(resumes, letters, profile))

    def test_profile_built_before_tracking_shows_build(self):
        self.assert_build(self.render(RESUMES, LETTERS, {"profile": {"candidate_evidence": {}}}))

    def test_progress_card_reflects_status(self):
        never_built = self.render(RESUMES, LETTERS[:3], None)
        self.assertIn("3 of 5 uploaded", never_built)
        self.assertIn("Not built yet", never_built)

        current = self.render(RESUMES, LETTERS, built_profile(RESUMES, LETTERS))
        self.assertIn("5 of 5 uploaded", current)
        self.assertIn("Up to date", current)

        stale = self.render(RESUMES, LETTERS[:4] + [{"id": "c9", "filename": "new.txt", "content": "New"}], built_profile(RESUMES, LETTERS))
        self.assertIn("Needs updating", stale)

    def test_header_shows_initials_not_identity_and_explainer_is_removed(self):
        html = self.render(RESUMES, LETTERS, None, user={**USER, "name": "jane.doe", "email": "jane.doe@example.com"})
        self.assertIn('<span class="avatar">JD</span>', html)
        self.assertNotIn("jane.doe", html)
        self.assertIn('href="/logout"', html)
        self.assertNotIn("Three grounded outputs", html)

        email_only = self.render(RESUMES, LETTERS, None, user={"id": USER["id"], "email": "shraddha_bhalerao@example.com"})
        self.assertIn('<span class="avatar">SB</span>', email_only)
        self.assertNotIn("shraddha_bhalerao", email_only)

    def letters(self, count):
        return [{"id": f"x{i}", "filename": f"letter{i}.txt", "content": f"Letter {i}"} for i in range(1, count + 1)]

    def assert_build_enabled(self, html, enabled):
        self.assertIn(BUILD_BUTTON, html)
        self.assertEqual('cta-button" disabled' not in html, enabled)
        self.assertEqual(app.COVER_LETTER_REQUIREMENT_MESSAGE in html, not enabled)

    def test_cover_letter_count_rule_one_to_five(self):
        self.assertEqual(
            app.COVER_LETTER_REQUIREMENT_MESSAGE,
            "Upload a resume and at least 1 previous cover letter (maximum 5) to enable profile building.",
        )
        cases = {
            "0 letters": (RESUMES, 0, False),
            "1 letter": (RESUMES, 1, True),
            "5 letters": (RESUMES, 5, True),
            "6 letters": (RESUMES, 6, False),
            "no resume + 1 letter": ([], 1, False),
        }
        for label, (resumes, count, enabled) in cases.items():
            with self.subTest(label):
                html = self.render(resumes, self.letters(count), None)
                self.assert_build_enabled(html, enabled)
                self.assertIn(f"{count} of 5 uploaded", html)
                # Checklist: resume step done if uploaded; cover-letter step done only for 1-5 letters; profile never built here.
                expected_done = (1 if resumes else 0) + (1 if 1 <= count <= 5 else 0)
                self.assertEqual(html.count("step-icon done"), expected_done)

    def test_sixth_cover_letter_upload_is_blocked(self):
        with patch.object(app, "app_user_for_request", return_value=USER), \
             patch.object(app, "get_resumes_for_user", return_value=RESUMES), \
             patch.object(app, "get_cover_letters_for_user", return_value=self.letters(5)), \
             patch.object(app, "get_candidate_profile", return_value=None), \
             patch.object(app, "get_style_profile", return_value=None), \
             patch.object(app, "save_cover_letter") as mock_save:
            response = self.client.post("/profile/cover-letters", files={"files": ("letter6.txt", b"Sixth letter", "text/plain")})
        self.assertEqual(response.status_code, 200)
        self.assertIn("You can upload a maximum of 5 previous cover letters.", response.text)
        self.assertEqual(mock_save.call_count, 0)

    def test_build_route_accepts_one_letter_and_rejects_zero(self):
        bundle = {"candidate_evidence": {}, "writing_style_profile": {"tone": "x"}, "professional_profile": {}}
        for count, expected_status in ((1, 303), (0, 200)):
            with self.subTest(count=count), \
                 patch.object(app, "app_user_for_request", return_value=USER), \
                 patch.object(app, "get_resumes_for_user", return_value=RESUMES), \
                 patch.object(app, "get_cover_letters_for_user", return_value=self.letters(count)), \
                 patch.object(app, "get_candidate_profile", return_value=None), \
                 patch.object(app, "get_style_profile", return_value=None), \
                 patch.object(app, "build_profile_bundle", return_value=dict(bundle)), \
                 patch.object(app, "save_candidate_profile") as mock_save_profile, \
                 patch.object(app, "save_style_profile"):
                response = self.client.post("/profile/build", follow_redirects=False)
            self.assertEqual(response.status_code, expected_status)
            self.assertEqual(mock_save_profile.call_count, 1 if count else 0)
            if not count:
                self.assertIn(app.COVER_LETTER_REQUIREMENT_MESSAGE, response.text)

    def test_account_menu_items_per_page(self):
        setup_html = self.render(RESUMES, LETTERS, None)
        self.assertIn('href="/profile/setup">Profile Setup</a>', setup_html)
        self.assertIn('href="/logout">Log out</a>', setup_html)

        with patch.object(app, "app_user_for_request", return_value={**USER, "name": "jane.doe"}), \
             patch.object(app, "get_candidate_profile", return_value={"profile": {}}), \
             patch.object(app, "get_style_profile", return_value={"profile": {}}):
            ready_html = self.client.get("/profile/ready").text
        self.assertIn('<span class="avatar">JD</span>', ready_html)
        self.assertIn('href="/profile/setup">Profile Setup</a>', ready_html)
        self.assertIn('href="/logout">Log out</a>', ready_html)
        self.assertNotIn("Edit profile", ready_html)
        self.assertNotIn("Ready to use", ready_html)
        self.assertIn('action="/profile/job-input"', ready_html)

    def test_build_stores_fingerprint_of_materials_used(self):
        bundle = {"candidate_evidence": {}, "writing_style_profile": {"tone": "x"}, "professional_profile": {}}
        with patch.object(app, "app_user_for_request", return_value=USER), \
             patch.object(app, "get_resumes_for_user", return_value=RESUMES), \
             patch.object(app, "get_cover_letters_for_user", return_value=LETTERS), \
             patch.object(app, "build_profile_bundle", return_value=bundle), \
             patch.object(app, "save_candidate_profile") as mock_save_profile, \
             patch.object(app, "save_style_profile"):
            response = self.client.post("/profile/build", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers.get("location"), "/profile/ready")
        saved = mock_save_profile.call_args.args[1]
        self.assertEqual(saved["source_materials_fingerprint"], app.profile_materials_fingerprint(RESUMES, LETTERS))


if __name__ == "__main__":
    unittest.main()
