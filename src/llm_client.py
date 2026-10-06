"""One gateway for every LLM call, built on LiteLLM's Router.

Environment:
  LLM_MODEL            primary model as a LiteLLM name (default: groq/<GROQ_MODEL or qwen/qwen3.8-27b>)
  LLM_FALLBACK_MODELS  optional, comma-separated; tried in order when the primary is busy or failing.
                       Every provider added here also receives the user's resume and letters.
  Each provider reads its own key: GROQ_API_KEY, OPENROUTER_API_KEY, TOGETHERAI_API_KEY, ...

Every call logs one line: step, provider/model, tokens, cost. Users only ever see LLMError messages;
provider details (which can include account ids) go to the server log.
"""
from __future__ import annotations

import logging
import os
import re
import time
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

# Use the price list bundled with the pinned LiteLLM instead of fetching it from GitHub at import.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402
from litellm import Router  # noqa: E402

litellm.suppress_debug_info = True
# LiteLLM's own loggers describe every request at INFO, which can include prompt text (resumes). Keep them quiet.
for _name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
    logging.getLogger(_name).setLevel(logging.WARNING)

TIMEOUT_SECONDS = 30.0
NUM_RETRIES = 1
DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"
RATE_LIMIT_MAX_WAIT_SECONDS = 20.0
RATE_LIMIT_FALLBACK_WAIT_SECONDS = 10.0
# Groq says e.g. "Please try again in 7.66s.", "in 1m2.5s." or "in 450ms."
RETRY_AFTER = re.compile(r"try again in (?:(\d+)m)?(\d+(?:\.\d+)?)(ms|s)\b", re.IGNORECASE)

logger = logging.getLogger("llm")


class LLMError(RuntimeError):
    """A failed LLM call. str() is safe to show users; `detail` keeps the provider's message for code and logs."""

    def __init__(self, message: str = "The writing service had a problem. Please try again in a moment.", detail: str = ""):
        super().__init__(message)
        self.detail = detail


class LLMBusyError(LLMError):
    def __init__(self, detail: str = ""):
        super().__init__("Lots of people are writing right now. Please try again in a minute.", detail)


def primary_model() -> str:
    return os.getenv("LLM_MODEL") or f"groq/{os.getenv('GROQ_MODEL', DEFAULT_GROQ_MODEL)}"


def fallback_models() -> list[str]:
    return [model.strip() for model in os.getenv("LLM_FALLBACK_MODELS", "").split(",") if model.strip()]


@lru_cache(maxsize=1)
def get_router() -> Router:
    """Built once from the environment; call get_router.cache_clear() after changing it."""
    models = [primary_model(), *fallback_models()]
    missing = litellm.validate_environment(model=models[0]).get("missing_keys") or []
    if missing:
        raise ValueError(f"Missing {', '.join(missing)} for {models[0]}. Add it to your .env file.")
    params = [{"model": model} for model in models]
    if os.getenv("GROQ_API_KEY_2") and models[0].startswith("groq/"):
        # Same model on a second Groq key, tried first when the main key is rate-limited or failing.
        # Only helps if the key is from a different Groq account; limits are per account, not per key.
        params.insert(1, {"model": models[0], "api_key": os.environ["GROQ_API_KEY_2"]})
    model_list = [{"model_name": f"m{i}", "litellm_params": p} for i, p in enumerate(params)]
    fallbacks = [{"m0": [f"m{i}" for i in range(1, len(model_list))]}] if len(model_list) > 1 else []
    return Router(model_list=model_list, fallbacks=fallbacks, num_retries=NUM_RETRIES, timeout=TIMEOUT_SECONDS)


def rate_limit_wait_seconds(message: str) -> float:
    """The provider's suggested wait, capped; a fixed fallback when the message has none."""
    match = RETRY_AFTER.search(message or "")
    if not match:
        return RATE_LIMIT_FALLBACK_WAIT_SECONDS
    minutes, amount, unit = match.groups()
    seconds = float(amount) / 1000 if unit.lower() == "ms" else float(amount)
    return min(seconds + 60 * int(minutes or 0), RATE_LIMIT_MAX_WAIT_SECONDS)


def complete(step: str, messages: list[dict[str, Any]], **params: Any) -> Any:
    """Run one chat completion through the gateway and return the OpenAI-shaped response.

    A rate limit gets one more try after the provider's suggested wait (at most 20 seconds).
    """
    started = time.perf_counter()
    for attempt in range(2):
        try:
            response = get_router().completion(model="m0", messages=messages, **params)
            break
        except ValueError:
            raise  # configuration problem (e.g. missing key): keep the clear message
        except litellm.RateLimitError as exc:
            logger.warning("llm busy step=%s: %s", step, exc)
            if attempt:
                raise LLMBusyError(detail=str(exc)) from exc
            wait = rate_limit_wait_seconds(str(exc))
            logger.warning("llm retry step=%s wait=%.1fs", step, wait)
            time.sleep(wait)
        except Exception as exc:
            logger.error("llm failed step=%s: %s", step, exc)
            raise LLMError(detail=str(exc)) from exc
    log_usage(step, response, time.perf_counter() - started)
    return response


def log_usage(step: str, response: Any, seconds: float) -> None:
    usage = getattr(response, "usage", None)
    hidden = getattr(response, "_hidden_params", None) or {}
    cost = hidden.get("response_cost")
    logger.info(
        "llm usage step=%s model=%s prompt=%s completion=%s cost=%s seconds=%.1f",
        step,
        hidden.get("litellm_model_name") or getattr(response, "model", "?"),  # the deployment that answered, incl. fallbacks
        getattr(usage, "prompt_tokens", "?"),
        getattr(usage, "completion_tokens", "?"),
        f"${cost:.5f}" if isinstance(cost, (int, float)) else "unknown",
        seconds,
    )


class _Completions:
    def create(self, *, messages: list[dict[str, Any]], step: str = "llm", model: str | None = None, **params: Any) -> Any:
        # `model` is ignored: the gateway decides which provider/model answers.
        return complete(step, messages, **params)


def chat_client() -> Any:
    """An OpenAI-shaped client (`client.chat.completions.create(...)`) that routes through the gateway."""
    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
