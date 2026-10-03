"""One place for Groq client settings, so a slow or failing Groq call ends in under a minute.

The SDK default is a 60s timeout with 2 retries (about 3 minutes worst case per call).
"""
from groq import Groq

TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 1


def make_groq_client(api_key: str) -> Groq:
    return Groq(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)
