"""Authenticated session cookie persistence (never stores credentials)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger('buying')

CACHE_PREFIX = 'buying:connector:session:'


def _cache_key(supplier_code: str) -> str:
    return f'{CACHE_PREFIX}{supplier_code}'


def save_session_cookies(supplier_code: str, cookies: dict[str, str], *, ttl: int | None = None) -> None:
    ttl = ttl if ttl is not None else int(getattr(settings, 'BUYING_AUTH_SESSION_TTL_SECONDS', 86400))
    payload = {'cookies': cookies}
    try:
        cache.set(_cache_key(supplier_code), payload, timeout=ttl)
    except Exception:
        logger.exception('Failed to persist session cookies for %s', supplier_code)
        _save_file_fallback(supplier_code, payload)


def load_session_cookies(supplier_code: str) -> dict[str, str]:
    try:
        payload = cache.get(_cache_key(supplier_code))
        if isinstance(payload, dict):
            cookies = payload.get('cookies') or {}
            if isinstance(cookies, dict):
                return {str(k): str(v) for k, v in cookies.items()}
    except Exception:
        logger.exception('Failed to load session cookies for %s', supplier_code)
    return _load_file_fallback(supplier_code)


def clear_session_cookies(supplier_code: str) -> None:
    try:
        cache.delete(_cache_key(supplier_code))
    except Exception:
        pass
    path = _fallback_path(supplier_code)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _fallback_dir() -> Path:
    base = getattr(settings, 'BUYING_AUTH_SESSION_DIR', '/tmp/buying_sessions')
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fallback_path(supplier_code: str) -> Path:
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in supplier_code)
    return _fallback_dir() / f'{safe}.json'


def _save_file_fallback(supplier_code: str, payload: dict) -> None:
    path = _fallback_path(supplier_code)
    path.write_text(json.dumps(payload), encoding='utf-8')


def _load_file_fallback(supplier_code: str) -> dict[str, str]:
    path = _fallback_path(supplier_code)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        cookies = data.get('cookies') or {}
        return {str(k): str(v) for k, v in cookies.items()} if isinstance(cookies, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
