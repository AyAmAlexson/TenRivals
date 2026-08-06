"""FX rates for converting supplier currencies to GEL.

Resolution order for a new CostScenario:

1. explicit manual unit-rate override (scenario/rebuild overrides);
2. active CalculationRule(rule_type='fx_rate') — staff-configured manual rate;
3. official NBG rate for Georgia-today;
4. latest available NBG rate (marked stale);
5. otherwise FxRateUnavailable → calculation_blocked.

Every return includes a full immutable snapshot for calculation_details['fx'].
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from buying.models import CalculationRule, FxRate
from buying.services.fx_rates import georgia_today, sync_nbg_rates

logger = logging.getLogger('buying')

UNIT_RATE_QUANT = Decimal('0.00000001')


class FxRateUnavailable(Exception):
    pass


def _snapshot(
    *,
    currency: str,
    unit_rate: Decimal,
    source: str,
    stale: bool,
    quantity: int | Decimal = 1,
    official_rate: Decimal | None = None,
    rate_date=None,
    published_at=None,
    fetched_at=None,
    rule_id=None,
    rule_version=None,
) -> dict:
    fetched_at = fetched_at or timezone.now()
    official = official_rate if official_rate is not None else unit_rate * Decimal(quantity)
    return {
        'currency': currency,
        'quantity': str(quantity),
        'official_rate': str(official),
        'unit_rate_gel': str(unit_rate),
        # Backward-compatible alias used by pricing.calculation_details merge.
        'rate_gel': str(unit_rate),
        'rate_date': rate_date.isoformat() if rate_date else None,
        'published_at': (
            published_at.isoformat() if hasattr(published_at, 'isoformat') else published_at
        ),
        'fetched_at': (
            fetched_at.isoformat() if hasattr(fetched_at, 'isoformat') else fetched_at
        ),
        'source': source,
        'stale': stale,
        'rule_id': rule_id,
        'rule_version': rule_version,
    }


def _from_nbg_row(row: FxRate, *, stale: bool) -> tuple[Decimal, dict]:
    unit = row.unit_rate_gel
    source = 'nbg_stale' if stale else 'nbg_official'
    return unit, _snapshot(
        currency=row.currency,
        unit_rate=unit,
        source=source,
        stale=stale,
        quantity=row.quantity,
        official_rate=row.rate_gel,
        rate_date=row.rate_date,
        published_at=row.published_at,
        fetched_at=row.fetched_at,
    )


def _from_configured_rule(rule: CalculationRule, currency: str) -> tuple[Decimal, dict] | None:
    try:
        rate = Decimal(str(rule.params['rate_gel']))
    except (KeyError, InvalidOperation):
        return None
    if rate <= 0:
        return None
    return rate, _snapshot(
        currency=currency,
        unit_rate=rate,
        source='configured_rule',
        stale=False,
        quantity=1,
        official_rate=rate,
        rate_date=None,
        published_at=rule.active_from or rule.updated_at,
        fetched_at=timezone.now(),
        rule_id=rule.pk,
        rule_version=rule.version,
    )


def get_fx_rate_to_gel(
    currency: str,
    at=None,
    *,
    manual_unit_rate: Decimal | None = None,
    allow_live_fetch: bool = True,
) -> tuple[Decimal, dict]:
    """Return (unit_rate_gel, provenance snapshot)."""
    currency = (currency or '').upper()
    if currency in ('', 'GEL'):
        return Decimal('1'), _snapshot(
            currency='GEL',
            unit_rate=Decimal('1'),
            source='identity',
            stale=False,
            quantity=1,
            official_rate=Decimal('1'),
            rate_date=georgia_today(at),
            fetched_at=timezone.now(),
        )

    if manual_unit_rate is not None:
        rate = Decimal(str(manual_unit_rate))
        if rate <= 0:
            raise FxRateUnavailable(f'Manual FX override for {currency} must be positive')
        return rate, _snapshot(
            currency=currency,
            unit_rate=rate,
            source='manual_override',
            stale=False,
            quantity=1,
            official_rate=rate,
            fetched_at=timezone.now(),
        )

    at = at or timezone.now()
    rules = (
        CalculationRule.objects.filter(rule_type=CalculationRule.RuleType.FX_RATE, enabled=True)
        .filter(Q(active_from__isnull=True) | Q(active_from__lte=at))
        .filter(Q(active_to__isnull=True) | Q(active_to__gte=at))
        .order_by('-priority', '-version')
    )
    for rule in rules:
        if str(rule.params.get('currency', '')).upper() != currency:
            continue
        found = _from_configured_rule(rule, currency)
        if found:
            return found

    today = georgia_today(at)
    fresh = (
        FxRate.objects.filter(
            currency=currency, rate_date=today, source=FxRate.Source.NBG
        ).first()
    )
    if fresh:
        return _from_nbg_row(fresh, stale=False)

    latest = (
        FxRate.objects.filter(currency=currency, source=FxRate.Source.NBG)
        .order_by('-rate_date')
        .first()
    )
    if latest:
        return _from_nbg_row(latest, stale=True)

    if allow_live_fetch and getattr(settings, 'BUYING_NBG_LIVE_FETCH_ON_MISS', True):
        try:
            sync_nbg_rates(currencies=[currency], force=True)
        except Exception:
            logger.exception('Live NBG fetch failed while resolving FX for %s', currency)
        else:
            return get_fx_rate_to_gel(
                currency, at=at, manual_unit_rate=None, allow_live_fetch=False
            )

    raise FxRateUnavailable(
        f'No FX rate available for {currency}. '
        f'Run "manage.py fetch_nbg_rates" or add an "FX rate" pricing rule.'
    )
