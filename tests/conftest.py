from unittest.mock import patch

import pytest

import app


@pytest.fixture(autouse=True)
def no_daily_limit_lookup():
    # The daily AI limit counts rows in Supabase; tests that don't care about it shouldn't reach the network.
    # tests/test_security.py patches it again where the limit is the point.
    with patch.object(app, "count_recent_ai_actions", return_value=0):
        yield


@pytest.fixture(autouse=True)
def no_real_storage_or_auth_writes():
    # Uploads, file removal and name saves would otherwise reach the real Supabase from .env.
    # tests/test_documents.py and tests/test_letter_name_and_edit.py patch these again where they are the point.
    with patch.object(app, "documents_bucket_bytes", return_value=0), \
         patch.object(app, "upload_document", side_effect=lambda auth_id, kind, name, data, **_: f"{auth_id}/{kind}/test-{name}"), \
         patch.object(app, "remove_documents"), \
         patch.object(app, "download_document", return_value=b""), \
         patch.object(app, "save_user_full_name"):
        yield
