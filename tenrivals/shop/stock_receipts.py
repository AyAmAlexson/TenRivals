"""Stock receipts: weighted-average landed cost updates (staff-only economics).

Receipts update the cost math and (optionally) add the batch to the STOCK
listing / size grid. Sold order lines keep their frozen landed cost snapshots
regardless of receipts.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from .models import Product, ProductListing, ProductListingChannel, StockReceipt
from .sales_order_stock import adjust_product_variant_stock


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


def _receive_one(
    *,
    product: Product,
    quantity: int,
    unit_landed_cost_gel: Decimal,
    on_hand_before: int | None,
    note: str,
    created_by,
    add_to_stock: bool,
    variant_key: str,
) -> StockReceipt:
    """Persist one receipt line. Caller must be inside transaction.atomic()."""
    locked = Product.objects.select_for_update().get(pk=product.pk)
    before = locked.landed_cost_gel
    oh = stock_on_hand(locked.pk) if on_hand_before is None else int(on_hand_before)
    if oh < 0:
        raise ValueError(f'On-hand quantity must be 0 or more for {locked.name}.')
    after = weighted_average_cost(oh, before, quantity, unit_landed_cost_gel)
    locked.landed_cost_gel = after
    locked.save(update_fields=['landed_cost_gel', 'updated_at'])
    if add_to_stock:
        # First batch of a new product may have no STOCK row yet.
        ProductListing.objects.get_or_create(
            product=locked,
            channel=ProductListingChannel.STOCK,
            defaults={'quantity': 0},
        )
        adjust_product_variant_stock(locked, variant_key, quantity)
    return StockReceipt.objects.create(
        product=locked,
        quantity=quantity,
        unit_landed_cost_gel=unit_landed_cost_gel,
        on_hand_before=oh,
        landed_cost_before=before,
        landed_cost_after=after,
        variant_label=(variant_key or '').strip()[:48],
        stock_added=add_to_stock,
        note=(note or '').strip()[:200],
        created_by=created_by,
    )


def receive_stock_batch(
    *,
    product: Product,
    quantity: int,
    unit_landed_cost_gel: Decimal,
    on_hand_before: int | None = None,
    note: str = '',
    created_by=None,
    add_to_stock: bool = True,
    variant_key: str = '',
) -> StockReceipt:
    """Record a single product batch (cost update ± stock increment)."""
    with transaction.atomic():
        return _receive_one(
            product=product,
            quantity=quantity,
            unit_landed_cost_gel=unit_landed_cost_gel,
            on_hand_before=on_hand_before,
            note=note,
            created_by=created_by,
            add_to_stock=add_to_stock,
            variant_key=variant_key,
        )


def receive_stock_lines(
    *,
    lines: list[dict],
    note: str = '',
    created_by=None,
) -> list[StockReceipt]:
    """Record several product lines as one supplier batch (one shared note).

    Each ``lines`` item::

        {
            'product': Product,
            'quantity': int,
            'unit_landed_cost_gel': Decimal,
            'on_hand_before': int | None,  # None → live STOCK qty at write time
            'add_to_stock': bool,
            'variant_key': str,
        }

    Runs in one transaction: a failure on any line rolls everything back.
    Sequential same-product lines see updated on-hand / average from prior lines.
    """
    if not lines:
        raise ValueError('Add at least one product line.')
    with transaction.atomic():
        return [
            _receive_one(
                product=line['product'],
                quantity=line['quantity'],
                unit_landed_cost_gel=line['unit_landed_cost_gel'],
                on_hand_before=line.get('on_hand_before'),
                note=note,
                created_by=created_by,
                add_to_stock=bool(line.get('add_to_stock', True)),
                variant_key=(line.get('variant_key') or ''),
            )
            for line in lines
        ]


def latest_receipt_ids_by_product(product_ids: list[int] | None = None) -> set[int]:
    """PK of the newest receipt per product (safe undo targets)."""
    qs = StockReceipt.objects.all()
    if product_ids is not None:
        qs = qs.filter(product_id__in=product_ids)
    # Order newest first; first seen per product wins.
    latest: dict[int, int] = {}
    for pid, pk in qs.order_by('-created_at', '-pk').values_list('product_id', 'pk'):
        if pid not in latest:
            latest[pid] = pk
    return set(latest.values())


def undo_stock_receipt(receipt: StockReceipt | int) -> dict:
    """Reverse one receipt: stock (−qty if added), restore average cost, delete row.

    Only the chronologically latest receipt for that product may be undone —
    otherwise the weighted-average chain would be wrong. Raises ValueError
    with a staff-readable message on any refusal.
    """
    receipt_pk = receipt.pk if isinstance(receipt, StockReceipt) else int(receipt)
    with transaction.atomic():
        locked_receipt = (
            StockReceipt.objects.select_for_update()
            .select_related('product')
            .filter(pk=receipt_pk)
            .first()
        )
        if locked_receipt is None:
            raise ValueError('Receipt not found (already undone?).')

        product = Product.objects.select_for_update().get(pk=locked_receipt.product_id)

        later = (
            StockReceipt.objects.filter(product_id=product.pk)
            .filter(
                Q(created_at__gt=locked_receipt.created_at)
                | Q(created_at=locked_receipt.created_at, pk__gt=locked_receipt.pk)
            )
            .order_by('created_at', 'pk')
            .first()
        )
        if later is not None:
            raise ValueError(
                f'Cannot undo: a newer receipt #{later.pk} exists for this product. '
                f'Undo newer receipts first (LIFO).'
            )

        stock_reversed = False
        if locked_receipt.stock_added:
            try:
                adjust_product_variant_stock(
                    product,
                    locked_receipt.variant_label or '',
                    -int(locked_receipt.quantity),
                )
            except ValueError as exc:
                raise ValueError(
                    f'Cannot undo stock for {product.name}: {exc}. '
                    f'Reduce sales reservations or fix listing qty first.'
                ) from exc
            stock_reversed = True

        before = locked_receipt.landed_cost_before
        product.landed_cost_gel = before
        product.save(update_fields=['landed_cost_gel', 'updated_at'])

        summary = {
            'product_name': product.name,
            'quantity': int(locked_receipt.quantity),
            'variant': (locked_receipt.variant_label or '').strip(),
            'stock_reversed': stock_reversed,
            'cost_restored_to': before,
            'unit_cost': locked_receipt.unit_landed_cost_gel,
            'receipt_id': locked_receipt.pk,
        }
        locked_receipt.delete()
        return summary
