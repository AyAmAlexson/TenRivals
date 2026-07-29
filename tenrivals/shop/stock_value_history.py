"""Daily stock shelf / landed value series for staff sales analytics.

Going forward: ``StockValueSnapshot`` (source=measured) is upserted from the
live STOCK listings (nightly Celery + on analytics page load for today).

Going backward: approximate reconstruction from StockReceipt (stock_added) and
currently-reserving SalesOrderLine quantities, valued at *today's* shelf prices
and landed costs. Manual listing edits and pre-receipt history are invisible.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from .models import (
    Product,
    ProductListing,
    ProductListingChannel,
    SalesOrder,
    SalesOrderLine,
    StockReceipt,
    StockValueSnapshot,
)
from .sales_order_stock import order_status_reserves_stock
from .sales_order_utils import product_unit_gross_price

_Q2 = Decimal('0.01')


def _q2(x: Decimal) -> Decimal:
    return Decimal(x).quantize(_Q2)


def compute_stock_values_now() -> dict:
    """Exact current STOCK totals (same shelf formula as staff stock stats)."""
    listings = list(
        ProductListing.objects.filter(
            channel=ProductListingChannel.STOCK,
            quantity__gt=0,
        ).select_related('product')
    )
    units = 0
    shelf = Decimal('0.00')
    landed = Decimal('0.00')
    for row in listings:
        p = row.product
        if not p or not p.is_active:
            continue
        q = int(row.quantity)
        units += q
        shelf += product_unit_gross_price(p) * q
        if p.landed_cost_gel is not None:
            landed += p.landed_cost_gel * q
    return {
        'units': units,
        'shelf_value_gel': _q2(shelf),
        'landed_value_gel': _q2(landed),
    }


def upsert_stock_snapshot(
    *,
    snapshot_date: date | None = None,
    source: str = StockValueSnapshot.Source.MEASURED,
    values: dict | None = None,
) -> StockValueSnapshot:
    """Insert or update one daily snapshot. Measured rows win over estimated."""
    d = snapshot_date or timezone.localdate()
    vals = values or compute_stock_values_now()
    with transaction.atomic():
        existing = (
            StockValueSnapshot.objects.select_for_update()
            .filter(snapshot_date=d)
            .first()
        )
        if existing and existing.source == StockValueSnapshot.Source.MEASURED:
            if source == StockValueSnapshot.Source.ESTIMATED:
                return existing  # never downgrade measured → estimated
            existing.units = vals['units']
            existing.shelf_value_gel = vals['shelf_value_gel']
            existing.landed_value_gel = vals['landed_value_gel']
            existing.source = StockValueSnapshot.Source.MEASURED
            existing.save(
                update_fields=[
                    'units',
                    'shelf_value_gel',
                    'landed_value_gel',
                    'source',
                    'updated_at',
                ]
            )
            return existing
        obj, _ = StockValueSnapshot.objects.update_or_create(
            snapshot_date=d,
            defaults={
                'units': vals['units'],
                'shelf_value_gel': vals['shelf_value_gel'],
                'landed_value_gel': vals['landed_value_gel'],
                'source': source,
            },
        )
        return obj


def _product_price_maps() -> tuple[dict[int, Decimal], dict[int, Decimal | None]]:
    shelf: dict[int, Decimal] = {}
    landed: dict[int, Decimal | None] = {}
    for p in Product.objects.filter(is_active=True).only(
        'id', 'initial_price', 'actual_price', 'landed_cost_gel'
    ):
        shelf[p.pk] = product_unit_gross_price(p)
        landed[p.pk] = p.landed_cost_gel
    return shelf, landed


def _current_qty_map() -> dict[int, int]:
    return {
        int(pid): int(q)
        for pid, q in ProductListing.objects.filter(
            channel=ProductListingChannel.STOCK
        ).values_list('product_id', 'quantity')
    }


def _event_deltas_by_day(start: date, end: date) -> dict[date, dict[int, int]]:
    """Net qty change on each calendar day: +receipts (stock_added), −reserving sales.

    Approximation: sales attributed to ``order_date`` of currently-reserving orders.
    """
    events: dict[date, dict[int, int]] = defaultdict(lambda: defaultdict(int))

    receipts = StockReceipt.objects.filter(
        stock_added=True,
        created_at__date__gte=start,
        created_at__date__lte=end,
    ).values_list('created_at', 'product_id', 'quantity')
    for created_at, pid, qty in receipts:
        d = timezone.localtime(created_at).date() if timezone.is_aware(created_at) else created_at.date()
        events[d][int(pid)] += int(qty)

    reserving = list(
        SalesOrder.objects.filter(order_date__gte=start, order_date__lte=end)
        .exclude(status__in=(SalesOrder.Status.CANCELLED, SalesOrder.Status.REFUNDED))
        .prefetch_related(
            Prefetch(
                'lines',
                queryset=SalesOrderLine.objects.filter(product_id__isnull=False).only(
                    'order_id', 'product_id', 'quantity'
                ),
            )
        )
    )
    for o in reserving:
        if not order_status_reserves_stock(o.status):
            continue
        for line in o.lines.all():
            if line.product_id:
                events[o.order_date][int(line.product_id)] -= int(line.quantity)

    return events


def _values_from_qty(
    qty_map: dict[int, int],
    shelf: dict[int, Decimal],
    landed: dict[int, Decimal | None],
) -> dict:
    units = 0
    shelf_v = Decimal('0.00')
    landed_v = Decimal('0.00')
    for pid, q in qty_map.items():
        if q <= 0:
            continue
        units += q
        shelf_v += shelf.get(pid, Decimal('0')) * q
        lc = landed.get(pid)
        if lc is not None:
            landed_v += lc * q
    return {
        'units': units,
        'shelf_value_gel': _q2(shelf_v),
        'landed_value_gel': _q2(landed_v),
    }


def estimate_stock_value_series(start: date, end: date) -> list[dict]:
    """Back-estimate daily shelf/landed value from today walking events backwards.

    Valued at *current* shelf prices and landed costs. Returns one row per day
    in ``[start, end]`` inclusive (clamped so end ≤ today).
    """
    today = timezone.localdate()
    if end > today:
        end = today
    if start > end:
        return []

    shelf, landed = _product_price_maps()
    qty = defaultdict(int, _current_qty_map())
    # Include products that appear in events even if current qty is 0.
    events = _event_deltas_by_day(start, today)

    # Walk from today back to start; record end-of-day values.
    by_day: dict[date, dict] = {}
    d = today
    while d >= start:
        if d <= end:
            by_day[d] = _values_from_qty(qty, shelf, landed)
        # Undo this day's events → end of previous day.
        for pid, delta in events.get(d, {}).items():
            qty[pid] = int(qty[pid]) - int(delta)
            if qty[pid] < 0:
                qty[pid] = 0
        d -= timedelta(days=1)

    rows = []
    cur = start
    while cur <= end:
        vals = by_day.get(cur) or {
            'units': 0,
            'shelf_value_gel': Decimal('0.00'),
            'landed_value_gel': Decimal('0.00'),
        }
        rows.append(
            {
                'date': cur,
                'label': cur.strftime('%Y-%m-%d'),
                'units': vals['units'],
                'shelf_value_gel': vals['shelf_value_gel'],
                'landed_value_gel': vals['landed_value_gel'],
                'source': 'estimated',
            }
        )
        cur += timedelta(days=1)
    return rows


def build_stock_value_series(start: date, end: date) -> dict:
    """Series for the chart: measured snapshots preferred, gaps filled by estimate.

    Always refreshes today's measured snapshot first.
    """
    today = timezone.localdate()
    chart_end = min(end, today)
    chart_start = start
    if chart_start > chart_end:
        return {
            'labels': [],
            'shelf': [],
            'landed': [],
            'units': [],
            'sources': [],
            'today': compute_stock_values_now(),
            'estimated_days': 0,
            'measured_days': 0,
        }

    upsert_stock_snapshot(snapshot_date=today, source=StockValueSnapshot.Source.MEASURED)

    existing = {
        row.snapshot_date: row
        for row in StockValueSnapshot.objects.filter(
            snapshot_date__gte=chart_start,
            snapshot_date__lte=chart_end,
        )
    }

    missing = []
    cur = chart_start
    while cur <= chart_end:
        if cur not in existing:
            missing.append(cur)
        cur += timedelta(days=1)

    estimated_map: dict[date, dict] = {}
    if missing:
        for row in estimate_stock_value_series(chart_start, chart_end):
            estimated_map[row['date']] = row
            if row['date'] in missing:
                upsert_stock_snapshot(
                    snapshot_date=row['date'],
                    source=StockValueSnapshot.Source.ESTIMATED,
                    values={
                        'units': row['units'],
                        'shelf_value_gel': row['shelf_value_gel'],
                        'landed_value_gel': row['landed_value_gel'],
                    },
                )

    labels = []
    shelf = []
    landed_vals = []
    units = []
    sources = []
    measured_days = 0
    estimated_days = 0
    cur = chart_start
    while cur <= chart_end:
        labels.append(cur.strftime('%Y-%m-%d'))
        row = existing.get(cur)
        if row is None and cur in estimated_map:
            e = estimated_map[cur]
            shelf.append(float(e['shelf_value_gel']))
            landed_vals.append(float(e['landed_value_gel']))
            units.append(int(e['units']))
            sources.append('estimated')
            estimated_days += 1
        elif row is not None:
            shelf.append(float(row.shelf_value_gel))
            landed_vals.append(float(row.landed_value_gel))
            units.append(int(row.units))
            sources.append(row.source)
            if row.source == StockValueSnapshot.Source.MEASURED:
                measured_days += 1
            else:
                estimated_days += 1
        else:
            shelf.append(0.0)
            landed_vals.append(0.0)
            units.append(0)
            sources.append('estimated')
            estimated_days += 1
        cur += timedelta(days=1)

    return {
        'labels': labels,
        'shelf': shelf,
        'landed': landed_vals,
        'units': units,
        'sources': sources,
        'today': compute_stock_values_now(),
        'estimated_days': estimated_days,
        'measured_days': measured_days,
    }
