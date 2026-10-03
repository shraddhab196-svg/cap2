import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
import uvicorn
from fastapi.testclient import TestClient

import app


def job(job_id):
    return {"id": job_id, "job_description": "Senior AI engineer", "company_url": "https://example.com"}


class AngleIsolationTests(unittest.TestCase):
    def setUp(self):
        anchors_dir = tempfile.TemporaryDirectory()
        self.addCleanup(anchors_dir.cleanup)
        patcher = patch.object(app, "ANCHORS_DIR", Path(anchors_dir.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def submit_job(self, client, job_id, angle_title):
        with patch.object(app, "app_user_for_request", return_value={"id": f"user-{job_id}", "email": "x@example.com"}), \
             patch.object(app, "save_job_application", return_value=job(job_id)), \
             patch.object(app, "research_company", return_value={"company_research": "notes"}), \
             patch.object(app, "get_cover_letters_for_user", return_value=[{"filename": "l.txt", "content": "text"}]), \
             patch.object(app, "generate_anchors", return_value={"anchors": [{"title": angle_title}]}):
            client.post("/profile/job-input", data={"job_description": "jd", "company_url": "https://example.com"}, follow_redirects=False)

    def angles_page(self, client, job_id):
        with patch.object(app, "app_user_for_request", return_value={"id": f"user-{job_id}", "email": "x@example.com"}), \
             patch.object(app, "get_job_application", return_value=job(job_id)):
            return client.get("/company/angles").text

    def test_two_users_never_see_each_others_angles(self):
        alice, bob = TestClient(app.app), TestClient(app.app)
        self.submit_job(alice, "job-alice", "Alice's angle")
        self.submit_job(bob, "job-bob", "Bob's angle")  # used to overwrite the one shared file
        alice_page, bob_page = self.angles_page(alice, "job-alice"), self.angles_page(bob, "job-bob")
        self.assertIn("Alice&#39;s angle", alice_page)
        self.assertNotIn("Bob", alice_page)
        self.assertIn("Bob&#39;s angle", bob_page)
        self.assertNotIn("Alice", bob_page)

    def test_job_ids_cannot_escape_the_angles_folder(self):
        for bad_id in ("../app", "..\app", "a/b", "", "x" * 65):
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(ValueError):
                    app.anchors_path(bad_id)
                self.assertEqual(app.load_anchors(bad_id), [])


class TrafficTests(unittest.TestCase):
    """A real server: one user's slow request must not stall anyone else."""

    SLOW_SECONDS = 2.0

    def setUp(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        port = sock.getsockname()[1]
        sock.close()

        def slow_backend(_request):
            time.sleep(self.SLOW_SECONDS)  # stands in for a blocking Groq/Supabase call
            raise app.HTTPException(status_code=401)

        patcher = patch.object(app, "app_user_for_request", side_effect=slow_backend)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = uvicorn.Server(uvicorn.Config(app.app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=self.server.run, daemon=True)
        thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        self.addCleanup(thread.join, 5)
        self.addCleanup(setattr, self.server, "should_exit", True)

    def slow_request(self):
        requests.get(f"{self.base}/profile/setup", allow_redirects=False, timeout=30)

    def test_landing_page_answers_while_a_slow_request_runs(self):
        slow = threading.Thread(target=self.slow_request)
        slow.start()
        time.sleep(0.3)
        started = time.perf_counter()
        response = requests.get(f"{self.base}/", timeout=30)
        elapsed = time.perf_counter() - started
        slow.join()
        self.assertEqual(response.status_code, 200)
        self.assertLess(elapsed, 0.5, f"landing page waited {elapsed:.2f}s behind a slow request")

    def test_slow_requests_run_side_by_side(self):
        workers = [threading.Thread(target=self.slow_request) for _ in range(5)]
        started = time.perf_counter()
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, self.SLOW_SECONDS * 2, f"5 slow requests took {elapsed:.2f}s, so they queued")


class UploadSafetyTests(unittest.TestCase):
    def test_pdf_upload_name_never_becomes_a_server_path(self):
        import io
        import tempfile as tf

        from starlette.datastructures import UploadFile

        from src import profile_builder

        seen = []
        upload = UploadFile(file=io.BytesIO(b"%PDF-1.4 fake"), filename="../../app.py.pdf")
        with patch("src.pdf_extractor.extract_pdf_text", side_effect=lambda path: seen.append(Path(path)) or "text"):
            self.assertEqual(profile_builder.read_uploaded_text(upload), "text")
        self.assertEqual(Path(seen[0]).parent, Path(tf.gettempdir()))
        self.assertNotIn("app.py", seen[0].name)
        self.assertFalse(seen[0].exists(), "temp file should be removed")


if __name__ == "__main__":
    unittest.main()
