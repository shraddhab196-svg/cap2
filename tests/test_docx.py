"""Word (.docx) resumes and cover letters."""
import io
import unittest
import zipfile
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from src.profile_builder import extract_docx_text, extract_candidate_name

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def para(*runs: str) -> str:
    return "<w:p>" + "".join(f"<w:r>{run}</w:r>" for run in runs) + "</w:p>"


def t(text: str) -> str:
    return f"<w:t xml:space=\"preserve\">{text}</w:t>"


def docx(body: str, header: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", f"<w:document {W}><w:body>{body}</w:body></w:document>")
        if header is not None:
            archive.writestr("word/header1.xml", f"<w:hdr {W}>{header}</w:hdr>")
    return buffer.getvalue()


class ExtractDocxTests(unittest.TestCase):
    def test_paragraphs_runs_tabs_and_breaks(self):
        body = para(t("Dear "), t("team,")) + para() + para(t("I led"), "<w:tab/>", t("a project."), "<w:br/>", t("Next line."))
        self.assertEqual(extract_docx_text(docx(body)), "Dear team,\n\nI led\ta project.\nNext line.")

    def test_tables_are_read_once_each(self):
        cell = lambda text: f"<w:tc>{para(t(text))}</w:tc>"
        body = f"<w:tbl><w:tr>{cell('Python')}{cell('5 years')}</w:tr></w:tbl>" + para(t("After the table."))
        self.assertEqual(extract_docx_text(docx(body)), "Python\n5 years\nAfter the table.")

    def test_name_in_the_page_header_comes_first(self):
        text = extract_docx_text(docx(para(t("Software Engineer")) + para(t("Built pipelines.")), header=para(t("JANE DOE"))))
        self.assertTrue(text.startswith("JANE DOE\n"))
        self.assertEqual(extract_candidate_name(text), "Jane Doe")

    def test_broken_or_unsafe_files_get_a_clear_message(self):
        evil = io.BytesIO()
        with zipfile.ZipFile(evil, "w") as archive:
            archive.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY a "aaaa">]><w:document/>')
        no_body = io.BytesIO()
        with zipfile.ZipFile(no_body, "w") as archive:
            archive.writestr("readme.txt", "hi")
        for data in (b"not a zip at all", evil.getvalue(), no_body.getvalue()):
            with self.subTest(data=data[:20]):
                with self.assertRaisesRegex(ValueError, "Word file"):
                    extract_docx_text(data)


class DocxUploadTests(unittest.TestCase):
    def test_docx_cover_letter_uploads(self):
        client = TestClient(app.app, follow_redirects=False)
        letter = docx(para(t("Dear team,")) + para(t("I rebuilt the alerting pipeline.")))
        with patch.object(app, "app_user_for_request", return_value={"id": "u1", "email": "j@x.com"}), \
             patch.object(app, "get_request_session_tokens", return_value=("t", "r")), \
             patch.object(app, "get_cover_letters_for_user", return_value=[]), \
             patch.object(app, "save_cover_letter") as save:
            response = client.post("/profile/cover-letters", files={"files": ("letter.docx", letter, app.DOCX_MIME)})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(save.call_args.args[2], "Dear team,\nI rebuilt the alerting pipeline.")

    def test_old_doc_files_get_a_helpful_message(self):
        client = TestClient(app.app)
        with patch.object(app, "app_user_for_request", return_value={"id": "u1", "email": "j@x.com"}), \
             patch.object(app, "get_request_session_tokens", return_value=("t", "r")), \
             patch.object(app, "get_resumes_for_user", return_value=[]), \
             patch.object(app, "get_cover_letters_for_user", return_value=[]), \
             patch.object(app, "get_candidate_profile", return_value=None), \
             patch.object(app, "get_style_profile", return_value=None):
            response = client.post("/profile/cover-letters", files={"files": ("old.doc", b"\xd0\xcf\x11\xe0", "application/msword")})
        self.assertIn("PDF, Word (.docx) or .txt", response.text)


if __name__ == "__main__":
    unittest.main()
