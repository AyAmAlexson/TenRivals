"""OpenAI implementation of AIProvider.

Talks to the Chat Completions HTTP API directly via httpx (already a project
dependency) with strict structured output. No SDK dependency needed.
"""

from __future__ import annotations

import json
import logging

import httpx
from django.conf import settings

from ..base import AIProvider, AIProviderError, AIProviderNotConfigured, AIResult
from ..prompts import NORMALIZATION_PROMPT_VERSION, NORMALIZATION_SYSTEM_PROMPT
from ..schemas import NORMALIZATION_JSON_SCHEMA, SchemaValidationError, validate_normalization

logger = logging.getLogger('buying')

API_URL = 'https://api.openai.com/v1/chat/completions'


class OpenAIProvider(AIProvider):
    name = 'openai'

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key if api_key is not None else settings.BUYING_OPENAI_API_KEY
        self.model = model or settings.BUYING_OPENAI_MODEL
        self.timeout = settings.BUYING_AI_TIMEOUT_SECONDS

    def normalize(self, text: str) -> AIResult:
        if not self.api_key:
            raise AIProviderNotConfigured(
                'OpenAI API key is not configured (set the OPENAI_API_KEY environment variable).'
            )
        last_error: Exception | None = None
        # One retry on schema violations: strict mode makes them rare but possible.
        for attempt in (1, 2):
            raw = self._chat(
                system_prompt=NORMALIZATION_SYSTEM_PROMPT,
                user_content=text,
                json_schema=NORMALIZATION_JSON_SCHEMA,
            )
            try:
                data = validate_normalization(self._extract_json(raw))
            except SchemaValidationError as exc:
                last_error = exc
                logger.warning('OpenAI normalization schema violation (attempt %s): %s', attempt, exc)
                continue
            return AIResult(
                data=data,
                model_version=raw.get('model', self.model),
                prompt_version=NORMALIZATION_PROMPT_VERSION,
                raw_response=raw,
            )
        raise AIProviderError(f'Normalization response failed schema validation: {last_error}')

    def _chat(self, system_prompt: str, user_content: str, json_schema: dict) -> dict:
        payload = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_content},
            ],
            'response_format': {'type': 'json_schema', 'json_schema': json_schema},
        }
        try:
            response = httpx.post(
                API_URL,
                json=payload,
                headers={'Authorization': f'Bearer {self.api_key}'},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise AIProviderError(f'OpenAI request failed: {exc}') from exc
        if response.status_code != 200:
            # Never log the key; response bodies for auth errors are safe.
            raise AIProviderError(
                f'OpenAI returned HTTP {response.status_code}: {response.text[:500]}'
            )
        return response.json()

    @staticmethod
    def _extract_json(raw: dict) -> dict:
        try:
            content = raw['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError(f'Unexpected OpenAI response shape: {exc}') from exc
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise AIProviderError(f'OpenAI response is not valid JSON: {exc}') from exc
