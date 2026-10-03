import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import litellm
from litellm import Router

from src import llm_client


def router(*responses):
    """A real LiteLLM Router whose models answer with LiteLLM's built-in mocks (no network)."""
    model_list = [
        {"model_name": f"m{i}", "litellm_params": {"model": f"groq/model-{i}", "api_key": "test", "mock_response": response}}
        for i, response in enumerate(responses)
    ]
    fallbacks = [{"m0": [f"m{i}" for i in range(1, len(responses))]}] if len(responses) > 1 else []
    return Router(model_list=model_list, fallbacks=fallbacks, num_retries=0)


MESSAGES = [{"role": "user", "content": "hi"}]


class GatewayTests(unittest.TestCase):
    def test_falls_back_when_the_primary_is_rate_limited(self):
        with patch.object(llm_client, "get_router", return_value=router("litellm.RateLimitError", "from the backup")):
            response = llm_client.complete("letter", MESSAGES)
        self.assertEqual(response.choices[0].message.content, "from the backup")

    def test_logs_one_usage_line_per_call(self):
        with patch.object(llm_client, "get_router", return_value=router("ok")), self.assertLogs("llm", "INFO") as logs:
            llm_client.chat_client().chat.completions.create(model="ignored", step="anchors", messages=MESSAGES)
        self.assertEqual(len(logs.output), 1)
        self.assertRegex(logs.output[0], r"llm usage step=anchors model=groq/\S+ prompt=\d+ completion=\d+ cost=")

    def test_rate_limit_becomes_a_friendly_message_without_provider_details(self):
        error = litellm.RateLimitError(message="Rate limit reached in organization org_SECRET123", llm_provider="groq", model="m")
        busy_router = SimpleNamespace(completion=Mock(side_effect=error))
        with patch.object(llm_client, "get_router", return_value=busy_router), self.assertLogs("llm", "WARNING"):
            with self.assertRaises(llm_client.LLMBusyError) as caught:
                llm_client.complete("letter", MESSAGES)
        self.assertEqual(str(caught.exception), "Lots of people are writing right now. Please try again in a minute.")
        self.assertIn("org_SECRET123", caught.exception.detail)  # kept for logs and code, never shown

    def test_other_failures_hide_provider_details(self):
        broken = SimpleNamespace(completion=Mock(side_effect=litellm.APIConnectionError(message="connect to 10.0.0.5 failed", llm_provider="groq", model="m")))
        with patch.object(llm_client, "get_router", return_value=broken), self.assertLogs("llm", "ERROR"):
            with self.assertRaises(llm_client.LLMError) as caught:
                llm_client.complete("letter", MESSAGES)
        self.assertNotIn("10.0.0.5", str(caught.exception))

    def test_missing_key_is_a_clear_configuration_error(self):
        llm_client.get_router.cache_clear()
        self.addCleanup(llm_client.get_router.cache_clear)
        with patch.dict(os.environ, {"LLM_MODEL": "groq/qwen/qwen3.8-27b"}):
            os.environ.pop("GROQ_API_KEY", None)  # restored by patch.dict
            with self.assertRaisesRegex(ValueError, "GROQ_API_KEY"):
                llm_client.get_router()

    def test_model_settings_come_from_the_environment(self):
        with patch.dict(os.environ, {"GROQ_MODEL": "llama-x", "LLM_FALLBACK_MODELS": " openrouter/a , together_ai/b ,"}, clear=False):
            os.environ.pop("LLM_MODEL", None)
            self.assertEqual(llm_client.primary_model(), "groq/llama-x")
            self.assertEqual(llm_client.fallback_models(), ["openrouter/a", "together_ai/b"])


class ModuleIntegrationTests(unittest.TestCase):
    def test_busy_message_reaches_the_user_unchanged(self):
        from src import anchor_generator

        error = litellm.RateLimitError(message="org_SECRET123 over limit", llm_provider="groq", model="m")
        with patch.object(llm_client, "get_router", return_value=SimpleNamespace(completion=Mock(side_effect=error))), \
             patch.object(anchor_generator, "load_environment"), self.assertLogs("llm", "WARNING"):
            with self.assertRaises(llm_client.LLMBusyError) as caught:
                anchor_generator.generate_anchors("https://example.com", "JD", "research", [("letter", "text")])
        self.assertNotIn("org_", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
