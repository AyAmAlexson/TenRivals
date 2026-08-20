"""Persist BuyingBatch quotes: recalculate all applicable routes."""

from __future__ import annotations

import logging

from django.db import transaction

from buying.engine.batch_pricing import BatchLineInput, build_batch_route_quote
from buying.engine.pricing import PricingError
from buying.engine.routes import applicable_routes
from buying.models import BuyingBatch, BuyingBatchQuote

logger = logging.getLogger('buying')

_ACTIVE = (
    BuyingBatchQuote.Status.CALCULATED,
    BuyingBatchQuote.Status.CALCULATION_BLOCKED,
    BuyingBatchQuote.Status.FAILED,
)


def _line_inputs(batch: BuyingBatch) -> list[BatchLineInput]:
    return [
        BatchLineInput(
            line_id=line.pk,
            title=line.title,
            unit_price=line.unit_price,
            currency=line.currency or batch.supplier.currency,
            quantity=int(line.quantity or 1),
            category=line.category or '',
            weight_g=line.weight_g,
            sort_order=int(line.sort_order or 1),
        )
        for line in batch.lines.all()
    ]


@transaction.atomic
def recalculate_batch(batch: BuyingBatch) -> list[BuyingBatchQuote]:
    """Build combined-shipment quotes for every applicable route; supersede old ones."""
    supplier = batch.supplier
    lines = _line_inputs(batch)
    routes = applicable_routes(supplier, category='')

    previous = list(
        BuyingBatchQuote.objects.select_for_update()
        .filter(batch=batch, status__in=_ACTIVE, partition_key='all')
        .order_by('id')
    )

    new_quotes: list[BuyingBatchQuote] = []
    if not lines:
        batch.status = BuyingBatch.Status.DRAFT
        batch.save(update_fields=['status', 'updated_at'])
        for old in previous:
            old.status = BuyingBatchQuote.Status.SUPERSEDED
            old.save(update_fields=['status'])
        return []

    if not routes:
        # Persist a failed placeholder? Prefer empty + status draft with warning via UI.
        logger.info('No applicable routes for batch %s supplier %s', batch.pk, supplier.pk)
        for old in previous:
            old.status = BuyingBatchQuote.Status.SUPERSEDED
            old.save(update_fields=['status'])
        batch.status = BuyingBatch.Status.CALCULATED
        batch.save(update_fields=['status', 'updated_at'])
        return []

    by_route_old = {q.fulfillment_route_id: q for q in previous}

    for route in routes:
        try:
            quote = build_batch_route_quote(supplier, lines, route)
        except PricingError as exc:
            quote = BuyingBatchQuote(
                fulfillment_route=route,
                scenario_kind=BuyingBatchQuote.ScenarioKind.COMBINED_SHIPMENT,
                partition_key='all',
                status=BuyingBatchQuote.Status.CALCULATION_BLOCKED,
                calculation_version='batch-combined-2026.08',
                calculation_details={'blocking_issues': [str(exc)], 'error': str(exc)},
                line_results=[],
                warnings=[f'blocking:{exc}'],
                confidence=BuyingBatchQuote.Confidence.ESTIMATED,
            )
        quote.batch = batch
        quote.save()
        new_quotes.append(quote)
        old = by_route_old.pop(route.pk, None)
        if old:
            old.status = BuyingBatchQuote.Status.SUPERSEDED
            old.superseded_by = quote
            old.save(update_fields=['status', 'superseded_by'])

    for leftover in by_route_old.values():
        leftover.status = BuyingBatchQuote.Status.SUPERSEDED
        leftover.save(update_fields=['status'])

    # Rank calculated quotes by customer_price ascending.
    ranked = sorted(
        [q for q in new_quotes if q.status == BuyingBatchQuote.Status.CALCULATED],
        key=lambda q: (q.customer_price, q.landed_cost, q.pk or 0),
    )
    for i, q in enumerate(ranked, start=1):
        q.rank = i
        q.save(update_fields=['rank'])
    for q in new_quotes:
        if q.status != BuyingBatchQuote.Status.CALCULATED and q.rank is not None:
            q.rank = None
            q.save(update_fields=['rank'])

    batch.status = BuyingBatch.Status.CALCULATED
    batch.save(update_fields=['status', 'updated_at'])
    return new_quotes
