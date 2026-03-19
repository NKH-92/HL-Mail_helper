from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.ai.gemini_client import GeminiClient
from app.core.config_manager import AI_PROVIDER_HANLIM, AppConfig, DEFAULT_HANLIM_AI_BASE_URL


class _DummySecretStore:
    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = secrets or {}

    def get_secret(self, key: str) -> str:
        return self._secrets.get(key, "")

    def has_secret(self, key: str) -> bool:
        return bool(self.get_secret(key))


class GeminiClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = GeminiClient(_DummySecretStore(), logging.getLogger("test"))

    def test_gemma_payload_uses_compatibility_mode(self) -> None:
        payload = self.client._build_payload(
            model_name="gemma-3-27b-it",
            system_prompt="system",
            user_prompt="user",
            response_schema={"type": "object"},
        )

        self.assertNotIn("systemInstruction", payload)
        self.assertNotIn("responseMimeType", payload["generationConfig"])
        self.assertNotIn("responseJsonSchema", payload["generationConfig"])
        self.assertIn("Return exactly one JSON object.", payload["contents"][0]["parts"][0]["text"])

    def test_gemini_payload_keeps_structured_output(self) -> None:
        payload = self.client._build_payload(
            model_name="gemini-2.5-flash",
            system_prompt="system",
            user_prompt="user",
            response_schema={"type": "object"},
        )

        self.assertIn("systemInstruction", payload)
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(payload["generationConfig"]["responseJsonSchema"], {"type": "object"})

    def test_new_gemini_preview_model_keeps_structured_output(self) -> None:
        payload = self.client._build_payload(
            model_name="gemini-3.1-flash-lite-preview",
            system_prompt="system",
            user_prompt="user",
            response_schema={"type": "object"},
        )

        self.assertIn("systemInstruction", payload)
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(payload["generationConfig"]["responseJsonSchema"], {"type": "object"})

    def test_hanlim_provider_uses_openai_base_url(self) -> None:
        config = AppConfig(
            ai_provider=AI_PROVIDER_HANLIM,
            ai_base_url=DEFAULT_HANLIM_AI_BASE_URL,
            gemini_model="hanlimAI",
        )

        url = self.client._build_api_url(config)

        self.assertEqual(url, DEFAULT_HANLIM_AI_BASE_URL)

    @patch("app.ai.gemini_client.OpenAI")
    def test_hanlim_provider_uses_chat_completions(self, openai_class: Mock) -> None:
        openai_instance = Mock()
        openai_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"category":"FYI"}'))]
        )
        openai_class.return_value = openai_instance

        client = GeminiClient(
            _DummySecretStore(
                {
                    "hanlim_api_key": "corp-key",
                }
            ),
            logging.getLogger("test"),
        )

        result = client.generate_json(
            config=AppConfig(
                ai_provider=AI_PROVIDER_HANLIM,
                ai_base_url=DEFAULT_HANLIM_AI_BASE_URL,
                gemini_model="hanlimAI",
            ),
            system_prompt="system",
            user_prompt="user",
            response_schema={"type": "object"},
        )

        self.assertEqual(result, {"category": "FYI"})
        openai_class.assert_called_once_with(
            base_url=DEFAULT_HANLIM_AI_BASE_URL,
            api_key="corp-key",
            default_headers={"User-Agent": "python-requests/2.32.3"},
        )
        openai_instance.chat.completions.create.assert_called_once()
        _, kwargs = openai_instance.chat.completions.create.call_args
        self.assertEqual(kwargs["model"], "hanlimAI")
        self.assertEqual(kwargs["messages"][1], {"role": "user", "content": "user"})
        self.assertEqual(kwargs["timeout"], 60)
        self.assertIn("Return exactly one JSON object matching this JSON schema.", kwargs["messages"][0]["content"])

    def test_has_api_key_checks_selected_provider_secret(self) -> None:
        client = GeminiClient(
            _DummySecretStore(
                {
                    "hanlim_api_key": "corp-key",
                }
            ),
            logging.getLogger("test"),
        )

        self.assertTrue(
            client.has_api_key(
                AppConfig(
                    ai_provider=AI_PROVIDER_HANLIM,
                    gemini_model="hanlimAI",
                )
            )
        )
        self.assertFalse(client.has_api_key(AppConfig(ai_provider="gemini")))


if __name__ == "__main__":
    unittest.main()
