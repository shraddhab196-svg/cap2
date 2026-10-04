from unittest.mock import patch

import pytest

import app


@pytest.fixture(autouse=True)
def no_daily_limit_lookup():
    # The daily AI limit counts rows in Supabase; tests that don't care about it shouldn't reach the network.
    # tests/test_security.py patches it again where the limit is the point.
    with patch.object(app, "count_recent_ai_actions", return_value=0):
        yield
