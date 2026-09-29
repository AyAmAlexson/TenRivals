"""FIFO shelf stack: one row per physical unit.

Receipts mint units. Sales and write-offs consume the oldest dated units
first (undated units last). Cancels and write-off undos put those same
units back. Catalog price is not stored here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    Product,
    ProductListing,
    ProductListingChannel,
    ProductType,
    StockReceipt,
    StockUnit,
    StockWriteOff,
    StockWriteOffLine,
)
from .sales_order_stock import get_variant_qty_map, product_requires_variant

_Q2 = Decimal('0.01')


def _q2(x: Decimal) -> Decimal:
    return Decimal(x).quantize(_Q2)


def _variant(label: str) -> str:
    return (label or '').strip()[:48]


def at_noon(d: date) -> datetime:
    naive = datetime.combine(d, time(12, 0))
    return timezone.make_aware(naive, timezone.get_current_timezone())


def local_day(dt: datetime | None) -> date | None:
    if dt is None:
        return None
    if timezone.is_aware(dt):
        return timezone.localtime(dt).date()
    return dt.date()


def mint_units(
    *,
    product: Product,
    quantity: int,
    variant_label: str = '',
    received_at: datetime | None = None,
    unit_landed_cost_gel: Decimal | None = None,
    receipt: StockReceipt | None = None,
) -> list[StockUnit]:
    qty = int(quantity)
    if qty < 1:
        return []
    vk = _variant(variant_label)
    cost = unit_landed_cost_gel
    if cost is not None:
        cost = _q2(Decimal(cost))
    rows = [
        StockUnit(
            product=product,
            variant_label=vk,
            received_at=received_at,
            unit_landed_cost_gel=cost,
            receipt=receipt,
            status=StockUnit.Status.ON_HAND,
        )
        for _ in range(qty)
    ]
    return StockUnit.objects.bulk_create(rows)


def _on_hand_qs(product: Product, variant_label: str):
    return (
        StockUnit.objects.select_for_update()
        .filter(
            product=product,
            variant_label=_variant(variant_label),
            status=StockUnit.Status.ON_HAND,
        )
        .order_by(F('received_at').asc(nulls_last=True), 'id')
    )


def consume_on_hand(
    *,
    product: Product,
    variant_label: str,
    quantity: int,
    status: str,
    at: datetime,
    sales_order_line=None,
    write_off_line=None,
) -> list[StockUnit]:
    """Mark ``quantity`` oldest on-hand units as sold or written off.

    If the stack is short (stock that predates the ledger), mint undated
    units for the gap and consume those last.
    """
    qty = int(quantity)
    if qty < 1:
        return []
    rows = list(_on_hand_qs(product, variant_label)[:qty])
    missing = qty - len(rows)
    if missing:
        rows.extend(
            mint_units(
                product=product,
                quantity=missing,
                variant_label=variant_label,
                received_at=None,
                unit_landed_cost_gel=product.landed_cost_gel,
            )
        )
        # Newly minted rows are not locked; they are exclusively ours.
    for unit in rows:
        unit.status = status
        unit.sales_order_line = sales_order_line
        unit.write_off_line = write_off_line
        if status == StockUnit.Status.SOLD:
            unit.sold_at = at
            unit.written_off_at = None
        elif status == StockUnit.Status.WRITTEN_OFF:
            unit.written_off_at = at
            unit.sold_at = None
        unit.save(
            update_fields=[
                'status',
                'sales_order_line',
                'write_off_line',
                'sold_at',
                'written_off_at',
            ]
        )
    return rows


def _restore_units(units: list[StockUnit], *, missing: int, product: Product, variant_label: str) -> None:
    ids = [u.pk for u in units]
    if ids:
        StockUnit.objects.filter(pk__in=ids).update(
            status=StockUnit.Status.ON_HAND,
            sold_at=None,
            written_off_at=None,
            sales_order_line=None,
            write_off_line=None,
        )
    if missing > 0:
        mint_units(
            product=product,
            quantity=missing,
            variant_label=variant_label,
            received_at=None,
            unit_landed_cost_gel=product.landed_cost_gel,
        )


def restore_sold_units(line) -> None:
    """Put units reserved by a sales line back on the shelf."""
    if line.product_id is None:
        return
    units = list(
        StockUnit.objects.select_for_update().filter(
            sales_order_line=line,
            status=StockUnit.Status.SOLD,
        )
    )
    missing = int(line.quantity) - len(units)
    _restore_units(
        units,
        missing=missing,
        product=line.product,
        variant_label=line.variant_label or '',
    )


def restore_write_off_units(line: StockWriteOffLine) -> None:
    units = list(
        StockUnit.objects.select_for_update().filter(
            write_off_line=line,
            status=StockUnit.Status.WRITTEN_OFF,
        )
    )
    missing = int(line.quantity) - len(units)
    _restore_units(
        units,
        missing=missing,
        product=line.product,
        variant_label=line.variant_label or '',
    )


def reverse_receipt_units(receipt: StockReceipt) -> None:
    """Drop units created by a receipt. Refuses if any of them already left the shelf.

    Legacy receipts (no linked units) drop the same count of newest undated
    on-hand units when those exist, and otherwise leave the stack alone.
    """
    if not receipt.stock_added:
        return
    linked = list(
        StockUnit.objects.select_for_update().filter(receipt=receipt).order_by('id')
    )
    if not linked:
        undated = list(
            _on_hand_qs(receipt.product, receipt.variant_label or '')
            .filter(received_at__isnull=True)
            .order_by('-id')[: int(receipt.quantity)]
        )
        if undated:
            StockUnit.objects.filter(pk__in=[u.pk for u in undated]).delete()
        return
    left = [u for u in linked if u.status != StockUnit.Status.ON_HAND]
    if left:
        raise ValueError(
            'Cannot undo: some units from this receipt were already sold or written off.'
        )
    if len(linked) != int(receipt.quantity):
        raise ValueError('Cannot undo: stack count for this receipt does not match the batch.')
    StockUnit.objects.filter(pk__in=[u.pk for u in linked]).delete()


def _targets_for_product(product: Product, listing_qty: int) -> dict[str, int]:
    if product_requires_variant(product):
        return {
            _variant(k): int(v)
            for k, v in get_variant_qty_map(product).items()
            if int(v or 0) > 0
        }
    if listing_qty > 0:
        return {'': int(listing_qty)}
    return {}


def sync_on_hand_gaps() -> int:
    """Create undated on-hand units so the stack covers current STOCK listings.

    Does not delete extras. Returns how many units were created.
    """
    created = 0
    listings = (
        ProductListing.objects.filter(channel=ProductListingChannel.STOCK, quantity__gt=0)
        .select_related('product', 'product__racket', 'product__shoe', 'product__apparel', 'product__string')
    )
    for listing in listings:
        product = listing.product
        targets = _targets_for_product(product, int(listing.quantity))
        have: dict[str, int] = defaultdict(int)
        for vk in StockUnit.objects.filter(
            product=product, status=StockUnit.Status.ON_HAND
        ).values_list('variant_label', flat=True):
            have[_variant(vk)] += 1
        for vk, need in targets.items():
            gap = need - have.get(vk, 0)
            if gap > 0:
                mint_units(
                    product=product,
                    quantity=gap,
                    variant_label=vk,
                    received_at=None,
                    unit_landed_cost_gel=product.landed_cost_gel,
                )
                created += gap
    return created


def stack_drift() -> list[dict]:
    """On-hand units that exceed the listing / variant qty (not auto-deleted)."""
    extras = []
    products = (
        Product.objects.filter(stock_units__status=StockUnit.Status.ON_HAND)
        .distinct()
        .select_related('racket', 'shoe', 'apparel', 'string')
    )
    for product in products:
        listing = ProductListing.objects.filter(
            product=product, channel=ProductListingChannel.STOCK
        ).first()
        listing_qty = int(listing.quantity) if listing else 0
        targets = _targets_for_product(product, listing_qty)
        have: dict[str, int] = defaultdict(int)
        for vk in StockUnit.objects.filter(
            product=product, status=StockUnit.Status.ON_HAND
        ).values_list('variant_label', flat=True):
            have[_variant(vk)] += 1
        keys = set(targets) | set(have)
        for vk in sorted(keys):
            extra = have.get(vk, 0) - targets.get(vk, 0)
            if extra > 0:
                extras.append(
                    {
                        'product': product,
                        'variant': vk,
                        'extra': extra,
                    }
                )
    return extras


def assign_received_on(*, product: Product, variant_label: str, quantity: int, received_on: date) -> int:
    qty = int(quantity)
    if qty < 1:
        raise ValueError('Quantity must be at least 1.')
    ids = list(
        _on_hand_qs(product, variant_label)
        .filter(received_at__isnull=True)
        .values_list('pk', flat=True)[:qty]
    )
    if len(ids) < qty:
        raise ValueError(
            f'Only {len(ids)} undated unit(s) on hand for {product.name}'
            + (f' ({_variant(variant_label)})' if _variant(variant_label) else '')
            + '.'
        )
    StockUnit.objects.filter(pk__in=ids).update(received_at=at_noon(received_on))
    return qty


def redate_on_hand_batch(
    *,
    product: Product,
    variant_label: str,
    from_day: date,
    to_day: date,
) -> int:
    if from_day == to_day:
        return 0
    units = list(
        _on_hand_qs(product, variant_label).exclude(received_at__isnull=True)
    )
    ids = [u.pk for u in units if local_day(u.received_at) == from_day]
    if not ids:
        return 0
    StockUnit.objects.filter(pk__in=ids).update(received_at=at_noon(to_day))
    return len(ids)


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return int(round((ordered[mid - 1] + ordered[mid]) / 2))


def _age_days(received_at: datetime | None, until: date) -> int | None:
    day = local_day(received_at)
    if day is None:
        return None
    return max(0, (until - day).days)


def build_shelf_age_report(start: date, end: date, *, today: date | None = None) -> dict:
    """Current shelf age plus completed shelf life of units sold in [start, end].

    Write-offs in the same window are listed by reason. They are not sales.
    """
    today = today or timezone.localdate()
    on_hand = list(
        StockUnit.objects.filter(status=StockUnit.Status.ON_HAND).select_related('product')
    )
    sold = list(
        StockUnit.objects.filter(
            status=StockUnit.Status.SOLD,
            sold_at__date__gte=start,
            sold_at__date__lte=end,
        ).select_related('product')
    )

    by_type: dict[str, dict] = {}

    def bucket(product: Product) -> dict:
        key = product.type or ''
        row = by_type.get(key)
        if row is None:
            row = {
                'type': key,
                'label': dict(ProductType.choices).get(key, key or '—'),
                'on_hand': 0,
                'dated': 0,
                'undated': 0,
                'ages': [],
                'landed': Decimal('0.00'),
                'sold_units': 0,
                'sold_days': [],
            }
            by_type[key] = row
        return row

    aging = {
        '0–30': {'units': 0, 'landed': Decimal('0.00')},
        '31–60': {'units': 0, 'landed': Decimal('0.00')},
        '61–90': {'units': 0, 'landed': Decimal('0.00')},
        '91+': {'units': 0, 'landed': Decimal('0.00')},
        'Undated': {'units': 0, 'landed': Decimal('0.00')},
    }
    oldest = []
    for unit in on_hand:
        row = bucket(unit.product)
        row['on_hand'] += 1
        cost = unit.unit_landed_cost_gel or Decimal('0.00')
        row['landed'] += cost
        age = _age_days(unit.received_at, today)
        if age is None:
            row['undated'] += 1
            aging['Undated']['units'] += 1
            aging['Undated']['landed'] += cost
        else:
            row['dated'] += 1
            row['ages'].append(age)
            if age <= 30:
                key = '0–30'
            elif age <= 60:
                key = '31–60'
            elif age <= 90:
                key = '61–90'
            else:
                key = '91+'
            aging[key]['units'] += 1
            aging[key]['landed'] += cost
            oldest.append(
                {
                    'product': unit.product,
                    'variant': unit.variant_label,
                    'received_on': local_day(unit.received_at),
                    'age_days': age,
                    'cost': cost,
                }
            )

    for unit in sold:
        age = _age_days(unit.received_at, local_day(unit.sold_at) or today)
        if age is None:
            continue
        row = bucket(unit.product)
        row['sold_units'] += 1
        row['sold_days'].append(age)

    type_order = {value: i for i, (value, _label) in enumerate(ProductType.choices)}
    categories = []
    for key, row in by_type.items():
        categories.append(
            {
                'type': key,
                'label': row['label'],
                'on_hand': row['on_hand'],
                'dated': row['dated'],
                'undated': row['undated'],
                'avg_age': int(round(sum(row['ages']) / len(row['ages']))) if row['ages'] else None,
                'median_age': _median(row['ages']),
                'landed': _q2(row['landed']),
                'sold_units': row['sold_units'],
                'sold_avg_days': (
                    int(round(sum(row['sold_days']) / len(row['sold_days'])))
                    if row['sold_days']
                    else None
                ),
                'sold_median_days': _median(row['sold_days']),
            }
        )
    categories.sort(key=lambda r: type_order.get(r['type'], 99))

    oldest.sort(key=lambda r: (-r['age_days'], r['product'].name))
    write_offs = list(
        StockWriteOff.objects.filter(written_on__gte=start, written_on__lte=end).prefetch_related(
            'lines'
        )
    )
    reason_rows: dict[str, dict] = {}
    for doc in write_offs:
        slot = reason_rows.setdefault(
            doc.reason,
            {
                'reason': doc.reason,
                'label': doc.get_reason_display(),
                'units': 0,
                'landed': Decimal('0.00'),
                'docs': 0,
            },
        )
        slot['docs'] += 1
        for line in doc.lines.all():
            slot['units'] += int(line.quantity)
            slot['landed'] += line.landed_cost_total or Decimal('0.00')
    reasons = []
    for _key, slot in reason_rows.items():
        slot['landed'] = _q2(slot['landed'])
        reasons.append(slot)
    reasons.sort(key=lambda r: (-r['landed'], r['label']))

    return {
        'categories': categories,
        'aging': [
            {'label': label, 'units': data['units'], 'landed': _q2(data['landed'])}
            for label, data in aging.items()
        ],
        'oldest': oldest[:12],
        'write_off_reasons': reasons,
        'write_off_units': sum(r['units'] for r in reasons),
        'write_off_landed': _q2(sum((r['landed'] for r in reasons), Decimal('0.00'))),
        'on_hand_units': len(on_hand),
        'dated_units': sum(1 for u in on_hand if u.received_at is not None),
        'undated_units': sum(1 for u in on_hand if u.received_at is None),
    }


def record_write_off(*, written_on: date, reason: str, note: str, lines: list[dict], created_by) -> StockWriteOff:
    """Remove units from the listing and the stack. ``lines`` items: product, variant_label, quantity."""
    from .sales_order_stock import adjust_product_variant_stock

    if reason not in dict(StockWriteOff.Reason.choices):
        raise ValueError('Choose a write-off reason.')
    if not lines:
        raise ValueError('Add at least one product line.')
    with transaction.atomic():
        doc = StockWriteOff.objects.create(
            written_on=written_on,
            reason=reason,
            note=(note or '')[:200],
            created_by=created_by,
        )
        for spec in lines:
            product = Product.objects.select_for_update().get(pk=spec['product'].pk)
            qty = int(spec['quantity'])
            variant = _variant(spec.get('variant_label') or '')
            if qty < 1:
                raise ValueError(f'Quantity must be at least 1 for {product.name}.')
            adjust_product_variant_stock(product, variant, -qty)
            line = StockWriteOffLine.objects.create(
                write_off=doc,
                product=product,
                variant_label=variant,
                quantity=qty,
            )
            taken = consume_on_hand(
                product=product,
                variant_label=variant,
                quantity=qty,
                status=StockUnit.Status.WRITTEN_OFF,
                at=at_noon(written_on),
                write_off_line=line,
            )
            total = sum((u.unit_landed_cost_gel or Decimal('0.00') for u in taken), Decimal('0.00'))
            line.landed_cost_total = _q2(total)
            line.save(update_fields=['landed_cost_total'])
        return doc


def undo_write_off(write_off: StockWriteOff | int) -> None:
    from .sales_order_stock import adjust_product_variant_stock

    pk = write_off.pk if isinstance(write_off, StockWriteOff) else int(write_off)
    with transaction.atomic():
        doc = (
            StockWriteOff.objects.select_for_update()
            .prefetch_related('lines__product')
            .filter(pk=pk)
            .first()
        )
        if doc is None:
            raise ValueError('Write-off not found.')
        for line in doc.lines.all():
            product = Product.objects.select_for_update().get(pk=line.product_id)
            restore_write_off_units(line)
            adjust_product_variant_stock(product, line.variant_label or '', int(line.quantity))
        doc.delete()
