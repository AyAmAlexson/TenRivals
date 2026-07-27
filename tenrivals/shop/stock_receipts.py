"""Stock receipts: weighted-average landed cost updates (staff-only economics).

Receipts only do cost math; listing / size-grid quantities are managed separately.
Sold order lines keep their frozen landed cost snapshots regardless of receipts.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from .models import Product, ProductListing, ProductListingChannel, StockReceipt


def stock_on_hand(product_id: int) -> int:
    """Units currently on the STOCK listing (0 when no row)."""
    row = ProductListing.objects.filter(
        product_id=product_id, channel=ProductListingChannel.STOCK
    ).first()
    return int(row.quantity) if row else 0


def weighted_average_cost(
    on_hand: int,
    current_avg: Decimal | None,
    batch_qty: int,
    batch_unit_cost: Decimal,
) -> Decimal:
    """New average cost after receiving a batch.

    When there is no averaging basis (nothing on hand or no current cost),
    the batch cost becomes the new average.
    """
    if on_hand <= 0 or current_avg is None:
        return Decimal(batch_unit_cost).quantize(Decimal('0.01'))
    total_value = (Decimal(on_hand) * current_avg) + (
        Decimal(batch_qty) * Decimal(batch_unit_cost)
    )
    return (total_value / Decimal(on_hand + batch_qty)).quantize(Decimal('0.01'))


def receive_stock_batch(
    *,
    product: Product,
    quantity: int,
    unit_landed_cost_gel: Decimal,
    on_hand_before: int,
    note: str = '',
    created_by=None,
) -> StockReceipt:
    """Record a batch and update the product's weighted-average landed cost."""
    with transaction.atomic():
        locked = Product.objects.select_for_update().get(pk=product.pk)
        before = locked.landed_cost_gel
        after = weighted_average_cost(
            on_hand_before, before, quantity, unit_landed_cost_gel
        )
        locked.landed_cost_gel = after
        locked.save(update_fields=['landed_cost_gel', 'updated_at'])
        return StockReceipt.objects.create(
            product=locked,
            quantity=quantity,
            unit_landed_cost_gel=unit_landed_cost_gel,
            on_hand_before=on_hand_before,
            landed_cost_before=before,
            landed_cost_after=after,
            note=(note or '').strip()[:200],
            created_by=created_by,
        )
