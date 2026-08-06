"""Persist and refresh official FX rates from NBG."""

from __future__ import annotations

import logging
from datetime import date
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from buying.integrations.nbg import FxRateData, NbgApiError, NbgRateProvider
from buying.models import FxRate

logger = logging.getLogger('buying')


def georgia_today(now=None) -> date:
    """Calendar date in Georgia (Asia/Tbilisi), not bare UTC."""
    tz_name = getattr(settings, 'BUYING_FX_TIMEZONE', 'Asia/Tbilisi')
    now = now or timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, timezone.utc)
    return now.astimezone(ZoneInfo(tz_name)).date()


def default_currencies() -> list[str]:
    return list(getattr(settings, 'BUYING_NBG_CURRENCIES', ['USD', 'EUR', 'GBP', 'CNY']))


@transaction.atomic
def store_fx_rates(rows: list[FxRateData], *, source: str = FxRate.Source.NBG) -> dict:
    """Upsert FxRate rows. Never deletes existing rates. Returns counters."""
    created = updated = 0
    fetched_at = timezone.now()
    for row in rows:
        _, was_created = FxRate.objects.update_or_create(
            currency=row.currency,
            rate_date=row.rate_date,
            source=source,
            defaults={
                'rate_gel': row.rate_gel,
                'quantity': row.quantity,
                'published_at': row.published_at,
                'fetched_at': fetched_at,
                'raw_data': row.raw,
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1
    return {'created': created, 'updated': updated, 'total': len(rows)}


def sync_nbg_rates(
    *,
    rate_date: date | None = None,
    currencies: list[str] | None = None,
    force: bool = False,
    provider: NbgRateProvider | None = None,
) -> dict:
    """Fetch from NBG and store. Existing rates are never deleted on failure.

    If force is False and every requested currency already has a rate for the
    target date, the network call is skipped.
    """
    currencies = [c.upper() for c in (currencies or default_currencies())]
    target_date = rate_date or georgia_today()

    if not force:
        existing = set(
            FxRate.objects.filter(
                currency__in=currencies,
                rate_date=target_date,
                source=FxRate.Source.NBG,
            ).values_list('currency', flat=True)
        )
        if existing.issuperset(set(currencies)):
            logger.info(
                'NBG rates for %s already present for %s — skip fetch',
                target_date, ','.join(currencies),
            )
            return {
                'skipped': True,
                'rate_date': target_date.isoformat(),
                'created': 0,
                'updated': 0,
                'total': 0,
            }

    provider = provider or NbgRateProvider()
    try:
        rows = provider.fetch_rates(rate_date=rate_date, currencies=currencies)
    except NbgApiError:
        logger.exception('Failed to fetch NBG FX rates for %s', target_date)
        try:
            import sentry_sdk
            sentry_sdk.capture_exception()
        except Exception:  # pragma: no cover — sentry may be unconfigured
            pass
        raise

    # Keep only requested currencies when the API returns a broader set.
    wanted = set(currencies)
    rows = [r for r in rows if r.currency in wanted] or rows
    stats = store_fx_rates(rows)
    stats['skipped'] = False
    stats['rate_date'] = target_date.isoformat()
    logger.info(
        'NBG FX sync for %s: created=%s updated=%s total=%s',
        stats['rate_date'], stats['created'], stats['updated'], stats['total'],
    )
    return stats
