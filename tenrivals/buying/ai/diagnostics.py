"""AI diagnostics: config snapshot, last error, API probe (no secrets)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger('buying')

CACHE_LAST_ERROR = 'buying:ai:last_error'
CACHE_LAST_OK = 'buying:ai:last_ok'


@dataclass
class AIDiagnostics:
    provider: str
    normalization_model: str
    matching_model: str
    temperature: float
    timeout_seconds: float
    max_retries: int
    api_key_configured: bool
    api_status: str
    api_detail: str
    last_error: str
    last_error_at: str
    last_success_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def record_ai_error(message: str, *, operation: str = '') -> None:
    payload = {
        'message': (message or '')[:1000],
        'operation': operation,
        'at': timezone.now().isoformat(),
    }
    try:
        cache.set(CACHE_LAST_ERROR, payload, timeout=60 * 60 * 24 * 7)
    except Exception:
        logger.exception('Failed to persist AI last error')


def record_ai_success(*, operation: str = '') -> None:
    payload = {'operation': operation, 'at': timezone.now().isoformat()}
    try:
        cache.set(CACHE_LAST_OK, payload, timeout=60 * 60 * 24 * 7)
    except Exception:
        logger.exception('Failed to persist AI last success')


def get_last_error() -> dict:
    try:
        data = cache.get(CACHE_LAST_ERROR) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def get_last_success() -> dict:
    try:
        data = cache.get(CACHE_LAST_OK) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def build_diagnostics(*, probe: bool = False) -> AIDiagnostics:
    provider_name = getattr(settings, 'BUYING_AI_PROVIDER', '') or ''
    norm_model = getattr(settings, 'BUYING_NORMALIZATION_MODEL', '') or ''
    match_model = getattr(settings, 'BUYING_MATCH_MODEL', '') or ''
    temperature = float(getattr(settings, 'BUYING_AI_TEMPERATURE', 0) or 0)
    timeout = float(getattr(settings, 'BUYING_AI_TIMEOUT', 45) or 45)
    max_retries = int(getattr(settings, 'BUYING_AI_MAX_RETRIES', 2) or 0)
    api_key = getattr(settings, 'BUYING_OPENAI_API_KEY', '') or ''
    key_ok = bool(api_key.strip())

    api_status = 'not_configured' if not key_ok else 'unknown'
    api_detail = 'OPENAI_API_KEY is not set' if not key_ok else 'Not probed yet'
    last = get_last_error()
    ok = get_last_success()

    if probe:
        try:
            from buying.ai.registry import get_ai_provider

            provider = get_ai_provider()
            if hasattr(provider, 'probe'):
                result = provider.probe()
                api_status = result.get('status') or ('ok' if result.get('ok') else 'error')
                api_detail = result.get('detail') or ''
            else:
                api_status = 'unsupported'
                api_detail = f'Provider "{provider.name}" has no probe()'
        except Exception as exc:
            api_status = 'error'
            api_detail = str(exc)[:300]
            record_ai_error(api_detail, operation='probe')
            last = get_last_error()
    elif key_ok and last.get('message'):
        api_status = 'degraded'
        api_detail = 'See last error'
    elif key_ok and ok.get('at'):
        api_status = 'ok'
        api_detail = 'Last call succeeded'

    return AIDiagnostics(
        provider=provider_name,
        normalization_model=norm_model,
        matching_model=match_model,
        temperature=temperature,
        timeout_seconds=timeout,
        max_retries=max_retries,
        api_key_configured=key_ok,
        api_status=api_status,
        api_detail=api_detail,
        last_error=last.get('message') or '',
        last_error_at=last.get('at') or '',
        last_success_at=ok.get('at') or '',
    )
