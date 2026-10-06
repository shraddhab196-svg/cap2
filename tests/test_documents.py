"""Original resume and cover-letter files kept in Supabase Storage (bucket "documents")."""
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from src import database

USER = {"id": "app-user-1", "email": "jane@example.com"}


class DocumentStorageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app, follow_redirects=False)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        patch_ = lambda name, **kw: self.stack.enter_context(patch.object(app, name, **kw))
        patch_("app_user_for_request", return_value=USER)
        patch_("get_request_session_tokens", return_value=("token", "refresh"))
        patch_("get_candidate_profile", return_value=None)
        patch_("get_style_profile", return_value=None)
        patch_("get_resumes_for_user", return_value=[])  # the error page re-renders profile setup
        self.upload = patch_("upload_document", side_effect=lambda auth_id, kind, name, data, **_: f"{auth_id}/{kind}/abc-{name}")
        self.remove = patch_("remove_documents")
        self.save_resume = patch_("save_resume")
        self.save_letter = patch_("save_cover_letter")
        self.delete_all = patch_("delete_all_resumes_for_user")

    def test_resume_original_is_stored_and_the_old_one_removed(self):
        with patch.object(app, "get_resumes_for_user", return_value=[{"id": "r0", "storage_path": "auth-1/resume/old-cv.pdf"}]):
            response = self.client.post("/profile/resume", files={"resume": ("cv.txt", b"Jane Doe\nEngineer", "text/plain")})
        self.assertEqual(response.status_code, 303)
        kind, name, data = self.upload.call_args.args[1:4]
        self.assertEqual((kind, name, data), ("resume", "cv.txt", b"Jane Doe\nEngineer"))
        self.assertTrue(self.save_resume.call_args.args[2].endswith("/resume/abc-cv.txt"))  # storage_path saved on the row
        self.assertEqual(self.save_resume.call_args.args[3], "Jane Doe\nEngineer")  # text still extracted
        self.remove.assert_called_once_with(["auth-1/resume/old-cv.pdf"], access_token="token")

    def test_several_cover_letters_upload_in_one_go(self):
        with patch.object(app, "get_cover_letters_for_user", return_value=[]):
            files = [("files", (f"l{i}.txt", f"Letter {i}".encode(), "text/plain")) for i in (1, 2, 3)]
            response = self.client.post("/profile/cover-letters", files=files)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.save_letter.call_count, 3)
        self.assertEqual([c.kwargs["storage_path"].rsplit("-", 1)[-1] for c in self.save_letter.call_args_list], ["l1.txt", "l2.txt", "l3.txt"])

    def test_more_letters_than_free_slots_are_refused_before_anything_is_saved(self):
        with patch.object(app, "get_cover_letters_for_user", return_value=[{"id": f"c{i}"} for i in range(4)]):
            files = [("files", (f"l{i}.txt", b"text", "text/plain")) for i in (1, 2)]
            response = self.client.post("/profile/cover-letters", files=files)
        self.assertIn("You can add 1 more cover letter (maximum 5).", response.text)
        self.save_letter.assert_not_called()
        self.upload.assert_not_called()

    def test_storage_failure_still_saves_the_text(self):
        self.upload.side_effect = RuntimeError("Bucket not found")
        with patch.object(app, "get_cover_letters_for_user", return_value=[]), self.assertLogs("app", "WARNING") as logs:
            response = self.client.post("/profile/cover-letters", files={"files": ("l.txt", b"Dear team", "text/plain")})
        self.assertEqual(response.status_code, 303)
        self.assertIsNone(self.save_letter.call_args.kwargs["storage_path"])
        self.assertIn("original file not stored", "\n".join(logs.output))

    def test_deleting_a_letter_removes_its_file(self):
        rows = [{"id": "c1", "storage_path": "auth-1/letters/a-l.txt"}, {"id": "c2", "storage_path": "auth-1/letters/b-m.txt"}]
        with patch.object(app, "get_cover_letters_for_user", return_value=rows), patch.object(app, "delete_cover_letter_for_user"):
            self.client.post("/profile/cover-letters/delete", data={"cover_letter_id": "c2"})
        self.remove.assert_called_once_with(["auth-1/letters/b-m.txt"], access_token="token")

    def test_download_only_serves_the_users_own_stored_file(self):
        rows = [{"id": "c1", "filename": "My letter.pdf", "storage_path": "auth-1/letters/a-My_letter.pdf"},
                {"id": "c9", "filename": "old.txt", "storage_path": "/resumes/old.txt"}]  # pre-storage label, no file
        with patch.object(app, "get_cover_letters_for_user", return_value=rows), \
             patch.object(app, "download_document", return_value=b"%PDF-1.4") as download:
            ok = self.client.get("/profile/files/letter/c1")
            missing = self.client.get("/profile/files/letter/someone-elses-id")
            label_only = self.client.get("/profile/files/letter/c9")
            bad_kind = self.client.get("/profile/files/users/c1")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.content, b"%PDF-1.4")
        self.assertEqual(ok.headers["content-type"], "application/pdf")
        self.assertIn("filename*=UTF-8''My%20letter.pdf", ok.headers["content-disposition"])
        download.assert_called_once_with("auth-1/letters/a-My_letter.pdf", access_token="token")
        # Anything unavailable goes back to profile setup with a quiet note, never an error page.
        for response in (missing, label_only, bad_kind):
            self.assertEqual((response.status_code, response.headers["location"]), (303, "/profile/setup?file=unavailable"))

    def test_storage_outage_on_download_is_a_quiet_note(self):
        rows = [{"id": "c1", "filename": "l.pdf", "storage_path": "auth-1/letters/a-l.pdf"}]
        with patch.object(app, "get_cover_letters_for_user", return_value=rows),              patch.object(app, "download_document", side_effect=RuntimeError("storage down")), self.assertLogs("app", "WARNING"):
            response = self.client.get("/profile/files/letter/c1")
            page = self.client.get(response.headers["location"])
        self.assertEqual(response.headers["location"], "/profile/setup?file=unavailable")
        self.assertIn("That file isn&#39;t available to download right now.", page.text)
        self.assertNotIn('role="alert"', page.text)

    def test_storage_outage_on_delete_still_deletes_quietly(self):
        self.remove.side_effect = RuntimeError("storage down")
        rows = [{"id": "c1", "storage_path": "auth-1/letters/a-l.txt"}]
        with patch.object(app, "get_cover_letters_for_user", return_value=rows),              patch.object(app, "delete_cover_letter_for_user") as delete_row, self.assertLogs("app", "WARNING"):
            response = self.client.post("/profile/cover-letters/delete", data={"cover_letter_id": "c1"})
        self.assertEqual((response.status_code, response.headers["location"]), (303, "/profile/setup"))
        delete_row.assert_called_once()

    def test_database_failure_on_delete_shows_a_friendly_message_not_a_crash(self):
        with patch.object(app, "get_cover_letters_for_user", side_effect=[RuntimeError("db down"), []]), self.assertLogs("app", "ERROR"):
            response = self.client.post("/profile/cover-letters/delete", data={"cover_letter_id": "c1"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(app.GENERIC_ERROR, response.text)


class StoragePathTests(unittest.TestCase):
    def test_file_names_are_made_safe_for_storage(self):
        self.assertEqual(database.safe_filename("../My CV (final).pdf"), "My_CV__final_.pdf")
        self.assertEqual(database.safe_filename("résumé.txt"), "r_sum_.txt")
        self.assertEqual(database.safe_filename(""), "file")

    def test_old_label_paths_are_never_sent_for_removal(self):
        with patch.object(database, "_documents") as bucket:
            database.remove_documents(["/resumes/cv.pdf", None, ""], access_token="t")
        bucket.assert_not_called()


if __name__ == "__main__":
    unittest.main()
