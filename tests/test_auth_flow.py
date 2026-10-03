import base64
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app


def jwt(exp_in_seconds: int) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"sub": "u1", "exp": int(time.time()) + exp_in_seconds}).encode()).decode().rstrip("=")
    return f"h.{claims}.s"


def auth_result(access_token=None, user_id="auth-1"):
    session = SimpleNamespace(access_token=access_token, refresh_token="r1") if access_token else None
    return SimpleNamespace(user=SimpleNamespace(id=user_id), session=session)


class ValidationTests(unittest.TestCase):
    def test_email_rules(self):
        for good in ("a@b.co", "first.last+tag@company.de"):
            self.assertIsNone(app.email_error(good), good)
        for bad in ("", "no-at-sign", "a@b", "a b@c.de", "@b.co", "x" * 250 + "@b.co"):
            self.assertIsNotNone(app.email_error(bad), bad)

    def test_password_rules(self):
        self.assertIsNone(app.password_error("letters123", "jane@x.de"))
        self.assertIn("8 characters", app.password_error("ab1", "jane@x.de"))
        self.assertIn("letter and one number", app.password_error("onlyletters", "jane@x.de"))
        self.assertIn("letter and one number", app.password_error("12345678", "jane@x.de"))
        self.assertIn("72", app.password_error("a1" * 40, "jane@x.de"))
        self.assertIn("email", app.password_error("jane1234@x.de".upper(), "jane1234@x.de"))


class SignupLoginTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app, follow_redirects=False)
        self.supabase = MagicMock()
        patcher = patch.object(app, "get_supabase_client", return_value=self.supabase)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_invalid_input_never_reaches_supabase_and_keeps_the_email(self):
        response = self.client.post("/signup", data={"email": "  Jane@Example.COM ", "password": "short"})
        self.assertIn("at least 8 characters", response.text)
        self.assertIn('value="jane@example.com"', response.text)
        self.supabase.auth.sign_up.assert_not_called()

    def test_confirmation_required_shows_check_your_inbox(self):
        self.supabase.auth.sign_up.return_value = auth_result(access_token=None)
        response = self.client.post("/signup", data={"email": "jane@example.com", "password": "letters123"})
        self.assertIn("Check your inbox", response.text)
        self.assertIn("jane@example.com", response.text)
        sent = self.supabase.auth.sign_up.call_args.args[0]
        self.assertTrue(sent["options"]["email_redirect_to"].endswith("/login?confirmed=1"))

    def test_signup_without_confirmation_goes_straight_to_profile(self):
        self.supabase.auth.sign_up.return_value = auth_result(access_token=jwt(3600))
        response = self.client.post("/signup", data={"email": "jane@example.com", "password": "letters123"})
        self.assertEqual(response.headers["location"], "/profile/setup")

    def test_supabase_errors_are_shown_in_plain_words(self):
        cases = {
            "Invalid login credentials": "Email or password is incorrect.",
            "Email not confirmed": "Please confirm your email first.",
            "email rate limit exceeded": "Too many attempts.",
            "connection reset by db.internal:5432": "Something went wrong.",
        }
        for raw, shown in cases.items():
            with self.subTest(raw):
                self.supabase.auth.sign_in_with_password.side_effect = RuntimeError(raw)
                with self.assertLogs("app", "ERROR") if "connection" in raw else nullcontext():
                    response = self.client.post("/login", data={"email": "jane@example.com", "password": "x"})
                self.assertIn(shown, response.text)
                self.assertNotIn(raw, response.text)

    def test_confirmed_link_lands_on_login_with_a_notice(self):
        self.assertIn("Email confirmed. Log in to continue.", self.client.get("/login?confirmed=1").text)

    def test_signed_in_users_skip_login_and_signup(self):
        self.supabase.auth.sign_in_with_password.return_value = auth_result(access_token=jwt(3600))
        self.client.post("/login", data={"email": "jane@example.com", "password": "letters123"})
        for path in ("/login", "/signup"):
            self.assertEqual(self.client.get(path).headers["location"], "/profile/setup")


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app, follow_redirects=False)
        self.supabase = MagicMock()
        patcher = patch.object(app, "get_supabase_client", return_value=self.supabase)
        patcher.start()
        self.addCleanup(patcher.stop)

    def login(self, access_token):
        self.supabase.auth.sign_in_with_password.return_value = auth_result(access_token=access_token)
        self.client.post("/login", data={"email": "jane@example.com", "password": "letters123"})

    def page(self):
        with patch.object(app, "get_or_create_app_user", return_value={"id": "app-1", "name": "jane"}) as lookup, \
             patch.object(app, "load_profile_materials", return_value={"resumes": [], "cover_letters": [], "style_profile": None, "candidate_profile": None}):
            response = self.client.get("/profile/setup")
        return response, lookup

    def test_fresh_token_is_not_refreshed_and_app_user_is_cached(self):
        self.login(jwt(3600))
        first, lookup1 = self.page()
        second, lookup2 = self.page()
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(lookup1.call_count + lookup2.call_count, 1)
        self.supabase.auth.refresh_session.assert_not_called()

    def test_expiring_token_is_refreshed_once_and_saved(self):
        self.login(jwt(10))
        self.supabase.auth.refresh_session.return_value = SimpleNamespace(session=SimpleNamespace(access_token=jwt(3600), refresh_token="r2"))
        self.page()
        self.page()
        self.supabase.auth.refresh_session.assert_called_once_with("r1")

    def test_failed_refresh_logs_out_instead_of_looping(self):
        self.login(jwt(10))
        self.supabase.auth.refresh_session.side_effect = RuntimeError("Invalid Refresh Token: Already Used")
        response, _ = self.page()
        self.assertEqual(response.headers["location"], "/login")
        self.assertEqual(self.client.get("/login").status_code, 200)  # not bounced back to /profile/setup


class nullcontext:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    unittest.main()
