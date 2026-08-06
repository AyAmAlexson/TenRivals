"""Shared httpx client for supplier connectors."""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from django.conf import settings

from buying.connectors.exceptions import CaptchaDetected, RateLimited

logger = logging.getLogger('buying')

DEFAULT_UA = (
    'Mozilla/5.0 (compatible; TenRivalsBuying/1.0; +https://tenrivals.com)'
)

CAPTCHA_PATTERNS = (
    re.compile(r'cf-challenge|cloudflare|attention required|captcha|challenge-platform', re.I),
    re.compile(r'g-recaptcha|hcaptcha|px-captcha', re.I),
)

SECRET_KEYS = frozenset({
    'password', 'passwd', 'secret', 'token', 'api_key', 'apikey', 'authorization',
    'cookie', 'set-cookie', 'credit_card', 'card_number', 'cvv',
})


def sanitize_for_storage(value: Any, *, depth: int = 0) -> Any:
    """Strip credentials / secrets from payloads before DB / logs / Sentry."""
    if depth > 12:
        return '[truncated]'
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            key_l = str(key).lower().replace('-', '_')
            if key_l in SECRET_KEYS or any(s in key_l for s in ('password', 'secret', 'token')):
                out[key] = '[redacted]'
            else:
                out[key] = sanitize_for_storage(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [sanitize_for_storage(v, depth=depth + 1) for v in value[:500]]
    if isinstance(value, str) and len(value) > 200_000:
        return value[:200_000] + '…[truncated]'
    return value


def detect_block_page(body: str, status_code: int) -> None:
    if status_code == 429:
        raise RateLimited('HTTP 429 rate limited')
    if status_code in (403, 503) and body:
        sample = body[:8000]
        for pattern in CAPTCHA_PATTERNS:
            if pattern.search(sample):
                raise CaptchaDetected(f'Captcha/challenge page detected (HTTP {status_code})')


class ConnectorHttpClient:
    """httpx wrapper with retries, timeouts and basic bot-challenge detection."""

    def __init__(
        self,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        user_agent: str | None = None,
        cookies: httpx.Cookies | None = None,
        headers: dict | None = None,
    ):
        self.timeout = timeout if timeout is not None else float(
            getattr(settings, 'BUYING_CONNECTOR_TIMEOUT_SECONDS', 25)
        )
        self.max_retries = max_retries if max_retries is not None else int(
            getattr(settings, 'BUYING_CONNECTOR_MAX_RETRIES', 2)
        )
        self.user_agent = user_agent or getattr(settings, 'BUYING_CONNECTOR_USER_AGENT', DEFAULT_UA)
        self._headers = {
            'User-Agent': self.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.8',
            **(headers or {}),
        }
        self._client = httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            cookies=cookies,
            headers=self._headers,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def cookies(self) -> httpx.Cookies:
        return self._client.cookies

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        return self._request('GET', url, params=params, headers=headers)

    def post(
        self,
        url: str,
        *,
        data=None,
        json: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        return self._request('POST', url, data=data, json=json, headers=headers)

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        host = urlparse(url).netloc
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            started = time.monotonic()
            try:
                response = self._client.request(method, url, **kwargs)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                body = response.text
                detect_block_page(body, response.status_code)
                response.extensions = getattr(response, 'extensions', {}) or {}
                # stash timing for health checks
                response._buying_elapsed_ms = elapsed_ms  # noqa: SLF001
                logger.debug('Connector HTTP %s %s → %s (%sms)', method, host, response.status_code, elapsed_ms)
                return response
            except (CaptchaDetected, RateLimited):
                raise
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(0.4 * (attempt + 1))
        raise last_exc or httpx.HTTPError(f'Request to {url} failed')
