"""National Bank of Georgia FX rates HTTP client.

Network I/O only — persistence and resolution live in services/engine.
Official JSON endpoint (no HTML scraping):
  https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

logger = logging.getLogger('buying')

NBG_JSON_URL = 'https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/'


class NbgApiError(Exception):
    """NBG API request failed or response could not be parsed."""


@dataclass(frozen=True)
class FxRateData:
    currency: str
    rate_gel: Decimal
    quantity: int
    rate_date: date
    published_at: datetime | None
    raw: dict


def _parse_decimal(value) -> Decimal:
    if value in (None, ''):
        raise InvalidOperation('empty rate')
    return Decimal(str(value))


def _parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value)
    if 'T' in text:
        dt = parse_datetime(text.replace('Z', '+00:00'))
        if dt is None:
            return None
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.utc)
        return dt.date()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    dt = parse_datetime(str(value).replace('Z', '+00:00'))
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


class NbgRateProvider:
    """Fetches official NBG exchange rates as FxRateData rows."""

    def __init__(self, *, timeout: float | None = None, base_url: str | None = None):
        self.timeout = timeout if timeout is not None else float(
            getattr(settings, 'BUYING_NBG_TIMEOUT_SECONDS', 15)
        )
        self.base_url = base_url or getattr(settings, 'BUYING_NBG_API_URL', NBG_JSON_URL)

    def fetch_rates(
        self,
        rate_date: date | None = None,
        currencies: list[str] | None = None,
    ) -> list[FxRateData]:
        params: dict[str, str] = {}
        if rate_date is not None:
            params['date'] = rate_date.isoformat()
        if currencies:
            params['currencies'] = ','.join(c.upper() for c in currencies)

        try:
            response = httpx.get(self.base_url, params=params or None, timeout=self.timeout)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise NbgApiError(f'NBG API timeout after {self.timeout}s') from exc
        except httpx.HTTPError as exc:
            raise NbgApiError(f'NBG API HTTP error: {exc}') from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise NbgApiError('NBG API returned non-JSON body') from exc

        return self._parse_payload(payload, requested_date=rate_date)

    def _parse_payload(self, payload, *, requested_date: date | None) -> list[FxRateData]:
        if not isinstance(payload, list) or not payload:
            raise NbgApiError('NBG API returned an empty or unexpected payload')

        results: list[FxRateData] = []
        for day_block in payload:
            if not isinstance(day_block, dict):
                continue
            block_date = _parse_date(day_block.get('date')) or requested_date
            currencies = day_block.get('currencies') or []
            if not isinstance(currencies, list):
                raise NbgApiError('NBG API currencies field is not a list')
            for entry in currencies:
                parsed = self._parse_currency_entry(entry, block_date=block_date)
                if parsed:
                    results.append(parsed)

        if not results:
            raise NbgApiError('NBG API response contained no usable currency rates')
        return results

    def _parse_currency_entry(self, entry: dict, *, block_date: date | None) -> FxRateData | None:
        if not isinstance(entry, dict):
            return None
        code = str(entry.get('code') or '').upper()
        if len(code) != 3:
            logger.warning('Skipping NBG entry with invalid currency code: %r', entry.get('code'))
            return None
        try:
            # Prefer the formatted string to avoid float binary artefacts.
            rate = _parse_decimal(entry.get('rateFormated') or entry.get('rate'))
            quantity = int(entry.get('quantity') or 1)
        except (InvalidOperation, TypeError, ValueError) as exc:
            logger.warning('Skipping NBG entry for %s: %s', code, exc)
            return None
        if quantity <= 0 or rate <= 0:
            logger.warning('Skipping NBG entry for %s: non-positive rate/quantity', code)
            return None

        rate_date = (
            _parse_date(entry.get('validFromDate'))
            or block_date
            or _parse_date(entry.get('date'))
        )
        if rate_date is None:
            logger.warning('Skipping NBG entry for %s: missing rate date', code)
            return None

        published_at = _parse_datetime(entry.get('date')) or _parse_datetime(
            entry.get('validFromDate')
        )
        return FxRateData(
            currency=code,
            rate_gel=rate,
            quantity=quantity,
            rate_date=rate_date,
            published_at=published_at,
            raw=dict(entry),
        )
