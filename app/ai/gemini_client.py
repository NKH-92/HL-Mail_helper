"""AI client for Gemini REST and Hanlim chat-completions providers."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI
import requests

from app.core.config_manager import (
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_HANLIM,
    AppConfig,
    DEFAULT_HANLIM_AI_BASE_URL,
    LEGACY_AI_PROVIDER_HANLIM,
)
from app.core.security import GEMINI_API_KEY, HANLIM_API_KEY, SecretStore

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiClient:
    """Client for Gemini REST and Hanlim structured JSON output."""

    def __init__(self, secret_store: SecretStore, logger: logging.Logger) -> None:
        self.secret_store = secret_store
        self.logger = logger

    def has_api_key(self, config: AppConfig | None = None) -> bool:
        """Return whether the active provider API key is configured."""

        if config is None:
            return self.secret_store.has_secret(GEMINI_API_KEY) or self.secret_store.has_secret(HANLIM_API_KEY)
        return self.secret_store.has_secret(self._resolve_secret_key(config))

    def generate_json(
        self,
        config: AppConfig,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a structured JSON response."""

        secret_key_name = self._resolve_secret_key(config)
        api_key = self.secret_store.get_secret(secret_key_name)
        if not api_key:
            raise ValueError(f"{self._provider_label(config)} API key is not stored in the OS credential store.")

        if self._is_hanlim_provider(config):
            return self._generate_json_with_hanlim_chat(
                config=config,
                api_key=api_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=response_schema,
            )

        return self._generate_json_with_gemini_rest(
            config=config,
            api_key=api_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
        )

    def _generate_json_with_gemini_rest(
        self,
        config: AppConfig,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self._build_payload(
            model_name=config.gemini_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
            compatibility_mode=self._uses_compatibility_payload(config),
        )
        response = requests.post(
            self._build_api_url(config),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            data=json.dumps(payload, ensure_ascii=False),
            timeout=config.gemini_timeout_seconds,
        )
        if not response.ok:
            detail = self._extract_error_message(response)
            raise requests.HTTPError(
                f"{response.status_code} {response.reason}: {detail}",
                response=response,
            )
        body = response.json()
        candidates = body.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"{self._provider_label(config)} response candidates missing: {body}")

        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts).strip()
        if not text:
            raise RuntimeError(f"{self._provider_label(config)} response body missing: {body}")
        return self._load_json_text(text)

    def _generate_json_with_hanlim_chat(
        self,
        config: AppConfig,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        client = OpenAI(
            base_url=self._build_api_url(config),
            api_key=api_key,
            default_headers={"User-Agent": "python-requests/2.32.3"},
        )
        response = client.chat.completions.create(
            model=config.gemini_model,
            messages=[
                {
                    "role": "system",
                    "content": self._build_hanlim_system_prompt(system_prompt, response_schema),
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            temperature=0.1,
            top_p=0.95,
            timeout=config.gemini_timeout_seconds,
        )
        text = self._extract_openai_message_text(response)
        if not text:
            raise RuntimeError(f"{self._provider_label(config)} response body missing: {response}")
        return self._load_json_text(text)

    def _build_api_url(self, config: AppConfig) -> str:
        if self._is_hanlim_provider(config):
            return str(config.ai_base_url or DEFAULT_HANLIM_AI_BASE_URL).strip().rstrip("/")
        return GEMINI_API_URL.format(model=config.gemini_model)

    def _build_payload(
        self,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        *,
        compatibility_mode: bool = False,
    ) -> dict[str, Any]:
        """Build a request payload with compatibility rules per model family."""

        if compatibility_mode or self._is_gemma_model(model_name):
            merged_prompt = (
                f"{system_prompt}\n\n"
                "Return exactly one JSON object. Do not add code fences or extra explanation.\n\n"
                f"{user_prompt}"
            )
            return {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": merged_prompt}],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.1,
                    "topP": 0.95,
                },
            }

        return {
            "systemInstruction": {
                "parts": [{"text": system_prompt}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt}],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": response_schema,
                "temperature": 0.1,
                "topP": 0.95,
            },
        }

    def _resolve_secret_key(self, config: AppConfig) -> str:
        return HANLIM_API_KEY if self._is_hanlim_provider(config) else GEMINI_API_KEY

    def _uses_compatibility_payload(self, config: AppConfig) -> bool:
        return self._is_gemma_model(config.gemini_model)

    @staticmethod
    def _provider_label(config: AppConfig) -> str:
        return "Hanlim AI" if GeminiClient._is_hanlim_provider(config) else "Google AI"

    @staticmethod
    def _is_hanlim_provider(config: AppConfig) -> bool:
        provider = str(config.ai_provider or AI_PROVIDER_GEMINI).strip().lower()
        return provider in {AI_PROVIDER_HANLIM, LEGACY_AI_PROVIDER_HANLIM}

    @staticmethod
    def _build_hanlim_system_prompt(system_prompt: str, response_schema: dict[str, Any]) -> str:
        return (
            f"{system_prompt}\n\n"
            "Return exactly one JSON object matching this JSON schema. "
            "Do not add code fences or extra explanation.\n"
            f"{json.dumps(response_schema, ensure_ascii=False)}"
        )

    @staticmethod
    def _is_gemma_model(model_name: str) -> bool:
        """Return whether the selected model belongs to the Gemma family."""

        return model_name.strip().lower().startswith("gemma-")

    @staticmethod
    def _extract_openai_message_text(response: Any) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""

        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                else:
                    text = getattr(item, "text", None)
                if isinstance(text, str) and text.strip():
                    parts.append(text)
            return "".join(parts).strip()
        return ""

    @staticmethod
    def _load_json_text(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        return json.loads(cleaned)

    @staticmethod
    def _extract_error_message(response: requests.Response) -> str:
        """Return a short API error message for debugging and UI display."""

        try:
            body = response.json()
        except ValueError:
            return response.text[:500]
        error = body.get("error") or {}
        return str(error.get("message") or body)[:500]
