"""OpenAI implementation of AIProvider.

Talks to the Chat Completions HTTP API via httpx with Structured Outputs.
Models, temperature, timeout and retries come exclusively from Django settings
(Heroku Config Vars) — never hardcoded call sites.
"""

from __future__ import annotations

import json
import logging
import time

import httpx
from django.conf import settings

from ..base import AIProvider, AIProviderError, AIProviderNotConfigured, AIResult
from ..diagnostics import record_ai_error, record_ai_success
from ..prompts import (
    MATCH_PROMPT_VERSION,
    MATCH_SYSTEM_PROMPT,
    NORMALIZATION_PROMPT_VERSION,
    NORMALIZATION_SYSTEM_PROMPT,
)
from ..schemas import (
    MATCH_JSON_SCHEMA,
    NORMALIZATION_JSON_SCHEMA,
    SchemaValidationError,
    validate_match,
    validate_normalization,
)

logger = logging.getLogger('buying')

API_URL = 'https://api.openai.com/v1/chat/completions'


class OpenAIProvider(AIProvider):
    name = 'openai'

    def __init__(
        self,
        api_key: str | None = None,
        normalization_model: str | None = None,
        match_model: str | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ):
        self.api_key = api_key if api_key is not None else getattr(settings, 'BUYING_OPENAI_API_KEY', '')
        self.normalization_model = normalization_model or getattr(
            settings, 'BUYING_NORMALIZATION_MODEL', 'gpt-5.5'
        )
        self.match_model = match_model or getattr(settings, 'BUYING_MATCH_MODEL', self.normalization_model)
        self.temperature = (
            temperature if temperature is not None else float(getattr(settings, 'BUYING_AI_TEMPERATURE', 0))
        )
        self.timeout = timeout if timeout is not None else float(
            getattr(settings, 'BUYING_AI_TIMEOUT', getattr(settings, 'BUYING_AI_TIMEOUT_SECONDS', 45))
        )
        self.max_retries = max_retries if max_retries is not None else int(
            getattr(settings, 'BUYING_AI_MAX_RETRIES', 2)
        )

    @property
    def model(self) -> str:
        """Backwards-compatible alias used in older tests/logs."""
        return self.normalization_model

    def normalize(self, text: str) -> AIResult:
        self._require_key()
        last_error: Exception | None = None
        attempts = max(1, self.max_retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                raw = self._chat(
                    model=self.normalization_model,
                    system_prompt=NORMALIZATION_SYSTEM_PROMPT,
                    user_content=text,
                    json_schema=NORMALIZATION_JSON_SCHEMA,
                )
                data = validate_normalization(self._extract_json(raw))
            except (AIProviderError, SchemaValidationError) as exc:
                last_error = exc
                record_ai_error(str(exc), operation='normalize')
                logger.warning('OpenAI normalization failed (attempt %s): %s', attempt, exc)
                if attempt < attempts:
                    time.sleep(0.3 * attempt)
                continue
            record_ai_success(operation='normalize')
            return AIResult(
                data=data,
                model_version=raw.get('model', self.normalization_model),
                prompt_version=NORMALIZATION_PROMPT_VERSION,
                raw_response=raw,
            )
        raise AIProviderError(f'Normalization failed after {attempts} attempt(s): {last_error}')

    def match(self, normalized: dict, candidate: dict) -> AIResult:
        self._require_key()
        payload = json.dumps({'normalized': normalized, 'candidate': candidate}, ensure_ascii=False)
        last_error: Exception | None = None
        attempts = max(1, self.max_retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                raw = self._chat(
                    model=self.match_model,
                    system_prompt=MATCH_SYSTEM_PROMPT,
                    user_content=payload,
                    json_schema=MATCH_JSON_SCHEMA,
                )
                data = validate_match(self._extract_json(raw))
            except (AIProviderError, SchemaValidationError) as exc:
                last_error = exc
                record_ai_error(str(exc), operation='match')
                logger.warning('OpenAI match failed (attempt %s): %s', attempt, exc)
                if attempt < attempts:
                    time.sleep(0.3 * attempt)
                continue
            record_ai_success(operation='match')
            return AIResult(
                data=data,
                model_version=raw.get('model', self.match_model),
                prompt_version=MATCH_PROMPT_VERSION,
                raw_response=raw,
            )
        raise AIProviderError(f'Match failed after {attempts} attempt(s): {last_error}')

    def probe(self) -> dict:
        """Lightweight status check for the diagnostics page (no business data)."""
        if not self.api_key:
            record_ai_error('OPENAI_API_KEY is not configured', operation='probe')
            return {'ok': False, 'status': 'not_configured', 'detail': 'OPENAI_API_KEY is missing'}
        try:
            response = httpx.get(
                'https://api.openai.com/v1/models',
                headers={'Authorization': f'Bearer {self.api_key}'},
                timeout=min(self.timeout, 15),
            )
        except httpx.HTTPError as exc:
            record_ai_error(f'OpenAI probe failed: {exc}', operation='probe')
            return {'ok': False, 'status': 'unreachable', 'detail': str(exc)[:300]}
        if response.status_code == 200:
            record_ai_success(operation='probe')
            return {'ok': True, 'status': 'ok', 'detail': 'API reachable'}
        detail = f'HTTP {response.status_code}: {response.text[:300]}'
        record_ai_error(detail, operation='probe')
        status = 'auth_error' if response.status_code in (401, 403) else 'error'
        return {'ok': False, 'status': status, 'detail': detail}

    def _require_key(self) -> None:
        if not self.api_key:
            raise AIProviderNotConfigured(
                'OpenAI API key is not configured (set the OPENAI_API_KEY environment variable).'
            )

    def _chat(self, *, model: str, system_prompt: str, user_content: str, json_schema: dict) -> dict:
        payload = {
            'model': model,
            'temperature': self.temperature,
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
        except httpx.TimeoutException as exc:
            raise AIProviderError(f'OpenAI request timed out after {self.timeout}s') from exc
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
