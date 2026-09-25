"""Staff-only sales analytics dashboard (landed-cost economics).

Canonical product/order metrics:
    Revenue        = customer payment (VAT-inclusive gross_total)
    VAT            = included 18% (stored vat_total)
    TAX            = 1% turnover tax on gross
    Acquiring      = 2% of the money actually received by card — summed over
                     the order's payments (each payment's method decides;
                     refunds are negative). Unpaid orders carry no acquiring.
    COGS           = Σ qty × landed_cost_gel over lines with a known cost
                   + Σ service contractor costs + delivery_cost_gel
    Net Revenue    = Revenue − VAT
    Net Proceeds   = Revenue − VAT − TAX − Acquiring
    Gross Profit   = Revenue − VAT − COGS
    Profit         = Revenue − VAT − TAX − Acquiring − COGS
                   = Net Proceeds − COGS
    Margin         = Profit / Revenue
    ROI            = Profit / COGS

Do not label Gross Profit, Net Proceeds, or Profit as Income.
Profit here is product/order-level, not company-wide net profit.

Cancelled / refunded orders are excluded everywhere. Lines without a landed
cost contribute to Revenue but not to COGS — the dashboard reports "cost
coverage" so profit figures can be read with the right confidence.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Prefetch
from django.shortcuts import render
from django.urls import reverse

from .models import SalesOrder, SalesOrderLine
from .sales_order_utils import order_services_and_delivery_cogs
from .stock_value_history import build_stock_value_series

_CHANNEL_ORDER = (
    SalesOrderLine.SaleChannel.STOCK,
    SalesOrderLine.SaleChannel.PREORDER,
)
_CHANNEL_LABELS = {
    SalesOrderLine.SaleChannel.STOCK: 'In stock',
    SalesOrderLine.SaleChannel.PREORDER: 'Preorder',
}

TURNOVER_TAX_RATE = Decimal('0.01')
ACQUIRING_RATE = Decimal('0.02')
_CARD_MARKERS = ('card', 'terminal', 'pos', 'visa', 'master', 'amex', 'acquir')

_EXCLUDED_STATUSES = (SalesOrder.Status.CANCELLED, SalesOrder.Status.REFUNDED)

_Q2 = Decimal('0.01')


def _q2(x: Decimal) -> Decimal:
    return x.quantize(_Q2)


def is_card_payment(payment_method: str) -> bool:
    pm = (payment_method or '').lower()
    return any(m in pm for m in _CARD_MARKERS)


def order_card_revenue(order: SalesOrder) -> Decimal:
    """Σ payments whose method looks like a card (refunds subtract).

    `order.payments` should be prefetched by callers iterating many orders.
    """
    total = Decimal('0.00')
    for p in order.payments.all():
        if is_card_payment(p.payment_method):
            total += p.amount_gross or Decimal('0.00')
    return _q2(total)


def order_acquiring(order: SalesOrder) -> tuple[Decimal, Decimal]:
    """(acquiring fee, card revenue) for one order, from its payments."""
    card_revenue = order_card_revenue(order)
    return _q2(card_revenue * ACQUIRING_RATE), card_revenue


def order_card_ratio(raw: dict) -> Decimal:
    """Share of order revenue collected by card — used to spread acquiring
    over line items when an order mixes card and cash."""
    revenue = raw.get('revenue') or Decimal('0')
    if revenue <= 0:
        return Decimal('0')
    ratio = (raw.get('card_revenue') or Decimal('0')) / revenue
    return min(max(ratio, Decimal('0')), Decimal('1'))


def _bucket_start(d: date, granularity: str) -> date:
    if granularity == 'day':
        return d
    if granularity == 'week':
        return d - timedelta(days=d.weekday())
    return d.replace(day=1)


def _next_bucket(d: date, granularity: str) -> date:
    if granularity == 'day':
        return d + timedelta(days=1)
    if granularity == 'week':
        return d + timedelta(days=7)
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _bucket_label(d: date, granularity: str) -> str:
    if granularity == 'day':
        return d.strftime('%Y-%m-%d')
    if granularity == 'week':
        return f'{d.strftime("%Y-%m-%d")} (W{d.isocalendar().week:02d})'
    return d.strftime('%Y-%m')


def _derived_money(m: dict) -> dict:
    revenue = m['revenue']
    vat = m['vat']
    tax = m['tax']
    acquiring = m['acquiring']
    cogs = m['cogs']
    net_proceeds = _q2(revenue - vat - tax - acquiring)
    return {
        'net': _q2(revenue - vat),
        'net_proceeds': net_proceeds,
        'gross_profit': _q2(revenue - vat - cogs),
        'profit': _q2(net_proceeds - cogs),
    }


def _zero_metrics() -> dict:
    return {
        'orders': 0,
        'revenue': Decimal('0.00'),
        'vat': Decimal('0.00'),
        'tax': Decimal('0.00'),
        'acquiring': Decimal('0.00'),
        'cogs': Decimal('0.00'),
        'net_proceeds': Decimal('0.00'),
        'gross_profit': Decimal('0.00'),
        'profit': Decimal('0.00'),
        'covered_gross': Decimal('0.00'),
        'product_gross': Decimal('0.00'),
        'card_revenue': Decimal('0.00'),
        'items': 0,
    }


def _finalize_metrics(m: dict) -> dict:
    """Fill ratios after summing. Keep per-order Net Proceeds / Gross Profit / Profit."""
    derived = _derived_money(m)
    m['net'] = derived['net']
    if not m.get('orders'):
        m['net_proceeds'] = derived['net_proceeds']
        m['gross_profit'] = derived['gross_profit']
        m['profit'] = derived['profit']
    else:
        m.setdefault('net_proceeds', derived['net_proceeds'])
        m.setdefault('gross_profit', derived['gross_profit'])
        m.setdefault('profit', derived['profit'])
    revenue = m['revenue']
    cogs = m['cogs']
    profit = m['profit']
    m['margin_pct'] = (profit / revenue * 100).quantize(_Q2) if revenue else None
    m['roi_pct'] = (profit / cogs * 100).quantize(_Q2) if cogs else None
    m['coverage_pct'] = (
        (m['covered_gross'] / m['product_gross'] * 100).quantize(_Q2)
        if m['product_gross']
        else None
    )
    m['aov'] = _q2(revenue / m['orders']) if m['orders'] else None
    m['card_share_pct'] = (
        (m['card_revenue'] / revenue * 100).quantize(_Q2) if revenue else None
    )
    return m


def compute_order_economics(order: SalesOrder) -> dict:
    """Raw metric values for one order (lines must be prefetched)."""
    gross = order.gross_total or Decimal('0.00')
    vat = order.vat_total if order.vat_total is not None else Decimal('0.00')
    cogs = Decimal('0.00')
    covered_gross = Decimal('0.00')
    product_gross = Decimal('0.00')
    items = 0
    for line in order.lines.all():
        product_gross += line.line_gross or Decimal('0.00')
        items += int(line.quantity or 0)
        if line.landed_cost_gel is not None:
            cogs += Decimal(line.quantity) * line.landed_cost_gel
            covered_gross += line.line_gross or Decimal('0.00')
    # Contractor costs for services + delivery (stored on the order only).
    cogs += order_services_and_delivery_cogs(order)
    tax = _q2(gross * TURNOVER_TAX_RATE)
    acquiring, card_revenue = order_acquiring(order)
    raw = {
        'orders': 1,
        'revenue': gross,
        'vat': vat,
        'tax': tax,
        'acquiring': acquiring,
        'cogs': _q2(cogs),
        'covered_gross': covered_gross,
        'product_gross': product_gross,
        'card_revenue': card_revenue,
        'items': items,
    }
    raw.update(_derived_money(raw))
    return raw


_ADDITIVE_KEYS = (
    'orders',
    'revenue',
    'vat',
    'tax',
    'acquiring',
    'cogs',
    'net_proceeds',
    'gross_profit',
    'profit',
    'covered_gross',
    'product_gross',
    'card_revenue',
    'items',
)


def _add_metrics(acc: dict, m: dict) -> None:
    for k in _ADDITIVE_KEYS:
        acc[k] += m[k]


def _parse_date(raw: str, default: date) -> date:
    try:
        return date.fromisoformat((raw or '').strip())
    except (TypeError, ValueError):
        return default


_WEEKDAY_LABELS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


def _build_profit_accumulation(
    daily_profit: dict[date, Decimal],
    start: date,
    end: date,
    today: date,
) -> dict:
    """Within-month cumulative (selected range) + current month vs average pace."""
    within_labels: list[str] = []
    within_values: list[float] = []
    running = Decimal('0.00')
    d = start
    while d <= end:
        if d.day == 1 or d == start:
            running = Decimal('0.00')
        running += daily_profit.get(d, Decimal('0.00'))
        within_labels.append(d.isoformat())
        within_values.append(float(_q2(running)))
        d += timedelta(days=1)

    # Months that actually had orders (days present in daily_profit).
    months_with_data = {(d.year, d.month) for d in daily_profit}

    month_day_profit: dict[tuple[int, int], dict[int, Decimal]] = defaultdict(dict)
    d = start
    while d <= end:
        month_day_profit[(d.year, d.month)][d.day] = daily_profit.get(
            d, Decimal('0.00')
        )
        d += timedelta(days=1)

    avg_sums = [Decimal('0.00')] * 32
    avg_counts = [0] * 32
    cur_ym = (today.year, today.month)
    months_in_average = 0

    for (y, m), day_map in sorted(month_day_profit.items()):
        if (y, m) == cur_ym:
            continue
        if (y, m) not in months_with_data:
            # Skip empty calendar months inside the filter — they dilute the average.
            continue
        month_start = date(y, m, 1)
        # Average only months that start inside the filter (full pace from day 1).
        if month_start < start:
            continue
        month_last = calendar.monthrange(y, m)[1]
        last_day = min(date(y, m, month_last), end).day
        months_in_average += 1
        total = Decimal('0.00')
        for day in range(1, last_day + 1):
            total += day_map.get(day, Decimal('0.00'))
            avg_sums[day] += total
            avg_counts[day] += 1

    max_day = 31
    avg_curve: list[float | None] = []
    for day in range(1, max_day + 1):
        if avg_counts[day]:
            avg_curve.append(float(_q2(avg_sums[day] / avg_counts[day])))
        else:
            avg_curve.append(None)

    current_curve: list[float | None] = [None] * max_day
    cur_map = month_day_profit.get(cur_ym, {})
    cur_start = date(today.year, today.month, 1)
    cur_end = date(
        today.year, today.month, calendar.monthrange(today.year, today.month)[1]
    )
    if not (cur_end < start or cur_start > end):
        through_date = min(today, end, cur_end)
        if through_date >= max(start, cur_start):
            total = Decimal('0.00')
            for day in range(1, through_date.day + 1):
                day_date = date(today.year, today.month, day)
                if day_date >= start:
                    total += cur_map.get(day, Decimal('0.00'))
                current_curve[day - 1] = float(_q2(total))

    return {
        'within_month_labels': within_labels,
        'within_month_cumulative': within_values,
        'pace_labels': [str(i) for i in range(1, max_day + 1)],
        'pace_average': avg_curve,
        'pace_current': current_curve,
        'pace_avg_months': months_in_average,
        'pace_current_label': cur_start.strftime('%B %Y'),
    }


def build_sales_analytics(
    start: date, end: date, granularity: str
) -> dict:
    orders = list(
        SalesOrder.objects.filter(order_date__gte=start, order_date__lte=end)
        .exclude(status__in=_EXCLUDED_STATUSES)
        .select_related('customer')
        .prefetch_related(
            Prefetch(
                'lines',
                queryset=SalesOrderLine.objects.select_related('product').order_by('id'),
            ),
            'payments',
        )
        .order_by('order_date', 'id')
    )

    totals = _zero_metrics()
    buckets: dict[date, dict] = {}
    weekdays: dict[int, dict] = {i: _zero_metrics() for i in range(7)}
    type_rows: dict[str, dict] = {}
    product_rows: dict[str, dict] = {}
    channel_rows: dict[str, dict] = {
        key: {
            'key': key,
            'label': _CHANNEL_LABELS[key],
            'qty': 0,
            'revenue': Decimal('0.00'),
            'cogs': Decimal('0.00'),
            'gross_profit': Decimal('0.00'),
            'covered_gross': Decimal('0.00'),
            'lines': 0,
        }
        for key in _CHANNEL_ORDER
    }
    customer_rows: dict[int, dict] = {}
    services_delivery_gross = Decimal('0.00')
    daily_profit: dict[date, Decimal] = {}

    for o in orders:
        raw = compute_order_economics(o)
        m = _finalize_metrics(dict(raw))
        card_ratio = order_card_ratio(raw)
        _add_metrics(totals, raw)
        b = _bucket_start(o.order_date, granularity)
        if b not in buckets:
            buckets[b] = _zero_metrics()
        _add_metrics(buckets[b], raw)
        _add_metrics(weekdays[o.order_date.weekday()], raw)
        services_delivery_gross += m['revenue'] - m['product_gross']
        daily_profit[o.order_date] = (
            daily_profit.get(o.order_date, Decimal('0.00')) + m['profit']
        )

        crow = customer_rows.setdefault(
            o.customer_id,
            {
                'pk': o.customer_id,
                'label': f'{o.customer.last_name} {o.customer.first_name}'.strip(),
                'orders': 0,
                'revenue': Decimal('0.00'),
                'profit': Decimal('0.00'),
                'last_order': o.order_date,
            },
        )
        crow['orders'] += 1
        crow['revenue'] += m['revenue']
        crow['profit'] += m['profit']
        if o.order_date > crow['last_order']:
            crow['last_order'] = o.order_date

        for line in o.lines.all():
            line_gross = line.line_gross or Decimal('0.00')
            line_net = line.line_net or Decimal('0.00')
            line_cogs = (
                Decimal(line.quantity) * line.landed_cost_gel
                if line.landed_cost_gel is not None
                else None
            )
            ch = line.sale_channel or SalesOrderLine.SaleChannel.STOCK
            if ch not in channel_rows:
                ch = SalesOrderLine.SaleChannel.STOCK
            crow_ch = channel_rows[ch]
            crow_ch['qty'] += int(line.quantity or 0)
            crow_ch['revenue'] += line_gross
            crow_ch['lines'] += 1
            if line_cogs is not None:
                crow_ch['cogs'] += line_cogs
                crow_ch['gross_profit'] += line_net - line_cogs
                crow_ch['covered_gross'] += line_gross

            type_label = line.analytics_category_label()
            trow = type_rows.setdefault(
                type_label,
                {
                    'label': type_label,
                    'qty': 0,
                    'revenue': Decimal('0.00'),
                    'cogs': Decimal('0.00'),
                    'gross_profit': Decimal('0.00'),
                    'covered_gross': Decimal('0.00'),
                },
            )
            trow['qty'] += int(line.quantity or 0)
            trow['revenue'] += line_gross
            if line_cogs is not None:
                trow['cogs'] += line_cogs
                trow['gross_profit'] += line_net - line_cogs
                trow['covered_gross'] += line_gross

            if line_cogs is not None:
                plabel = line.display_title()
                line_gross_profit = line_net - line_cogs
                line_tax = _q2(line_gross * TURNOVER_TAX_RATE)
                # Mixed card/cash orders: spread acquiring by the card share.
                line_acq = _q2(line_gross * ACQUIRING_RATE * card_ratio)
                line_profit = _q2(line_gross_profit - line_tax - line_acq)
                prow = product_rows.setdefault(
                    plabel,
                    {
                        'label': plabel,
                        'qty': 0,
                        'revenue': Decimal('0.00'),
                        'gross_profit': Decimal('0.00'),
                        'profit': Decimal('0.00'),
                    },
                )
                prow['qty'] += int(line.quantity or 0)
                prow['revenue'] += line_gross
                prow['gross_profit'] += line_gross_profit
                prow['profit'] += line_profit

    _finalize_metrics(totals)

    # Fill gaps so charts/tables show zero periods too (days/weeks/months
    # without sales must still appear on the timeline).
    cur = _bucket_start(start, granularity)
    last_bucket = _bucket_start(end, granularity)
    while cur <= last_bucket:
        buckets.setdefault(cur, _zero_metrics())
        cur = _next_bucket(cur, granularity)

    bucket_rows = []
    for b in sorted(buckets.keys()):
        row = _finalize_metrics(buckets[b])
        row['start'] = b
        row['label'] = _bucket_label(b, granularity)
        bucket_rows.append(row)

    type_breakdown = []
    for trow in sorted(type_rows.values(), key=lambda r: -r['revenue']):
        trow['coverage_pct'] = (
            (trow['covered_gross'] / trow['revenue'] * 100).quantize(_Q2)
            if trow['revenue']
            else None
        )
        # Category ROI/margin use line-level gross profit (before order-level
        # tax and acquiring, which cannot be attributed to a single line).
        trow['roi_pct'] = (
            (trow['gross_profit'] / trow['cogs'] * 100).quantize(_Q2)
            if trow['cogs']
            else None
        )
        trow['margin_pct'] = (
            (trow['gross_profit'] / trow['revenue'] * 100).quantize(_Q2)
            if trow['revenue']
            else None
        )
        trow['share_pct'] = (
            (trow['revenue'] / totals['revenue'] * 100).quantize(_Q2)
            if totals['revenue']
            else None
        )
        type_breakdown.append(trow)

    channel_line_revenue = sum(
        (r['revenue'] for r in channel_rows.values()), Decimal('0.00')
    )
    channel_breakdown = []
    for key in _CHANNEL_ORDER:
        crow = channel_rows[key]
        crow['coverage_pct'] = (
            (crow['covered_gross'] / crow['revenue'] * 100).quantize(_Q2)
            if crow['revenue']
            else None
        )
        crow['roi_pct'] = (
            (crow['gross_profit'] / crow['cogs'] * 100).quantize(_Q2)
            if crow['cogs']
            else None
        )
        crow['margin_pct'] = (
            (crow['gross_profit'] / crow['revenue'] * 100).quantize(_Q2)
            if crow['revenue']
            else None
        )
        crow['share_pct'] = (
            (crow['revenue'] / channel_line_revenue * 100).quantize(_Q2)
            if channel_line_revenue
            else None
        )
        channel_breakdown.append(crow)

    top_products = sorted(
        product_rows.values(), key=lambda r: (-r['profit'], r['label'])
    )[:10]

    weekday_rows = []
    for i in range(7):
        row = _finalize_metrics(weekdays[i])
        row['label'] = _WEEKDAY_LABELS[i]
        row['share_pct'] = (
            (row['revenue'] / totals['revenue'] * 100).quantize(_Q2)
            if totals['revenue']
            else None
        )
        weekday_rows.append(row)

    top_customers = sorted(
        customer_rows.values(), key=lambda r: (-r['revenue'], r['label'])
    )[:5]
    repeat_customers = sum(1 for r in customer_rows.values() if r['orders'] > 1)

    active_rows = [r for r in bucket_rows if r['orders']]
    best_bucket = max(active_rows, key=lambda r: r['profit']) if active_rows else None
    best_weekday = (
        max(weekday_rows, key=lambda r: r['revenue'])
        if totals['orders']
        else None
    )
    # Trend: last bucket with sales vs the bucket right before it (trailing
    # empty buckets would otherwise always read as -100%).
    revenue_growth_pct = None
    if active_rows:
        i = bucket_rows.index(active_rows[-1])
        if i >= 1 and bucket_rows[i - 1]['revenue']:
            revenue_growth_pct = (
                (bucket_rows[i]['revenue'] - bucket_rows[i - 1]['revenue'])
                / bucket_rows[i - 1]['revenue']
                * 100
            ).quantize(_Q2)
    avg_profit_per_order = (
        _q2(totals['profit'] / totals['orders']) if totals['orders'] else None
    )
    avg_items_per_order = (
        (Decimal(totals['items']) / totals['orders']).quantize(Decimal('0.1'))
        if totals['orders']
        else None
    )
    unknown_cost_gross = totals['product_gross'] - totals['covered_gross']

    chart = {
        'labels': [r['label'] for r in bucket_rows],
        'revenue': [float(r['revenue']) for r in bucket_rows],
        'net_proceeds': [float(r['net_proceeds']) for r in bucket_rows],
        'gross_profit': [float(r['gross_profit']) for r in bucket_rows],
        'profit': [float(r['profit']) for r in bucket_rows],
        'cogs': [float(r['cogs']) for r in bucket_rows],
        'orders': [r['orders'] for r in bucket_rows],
        'margin': [
            float(r['margin_pct']) if r['margin_pct'] is not None else None
            for r in bucket_rows
        ],
        'roi': [
            float(r['roi_pct']) if r['roi_pct'] is not None else None
            for r in bucket_rows
        ],
        'aov': [
            float(r['aov']) if r['aov'] is not None else None for r in bucket_rows
        ],
        'weekday_labels': list(_WEEKDAY_LABELS),
        'weekday_revenue': [float(r['revenue']) for r in weekday_rows],
        'weekday_orders': [r['orders'] for r in weekday_rows],
        'type_labels': [r['label'] for r in type_breakdown],
        'type_revenue': [float(r['revenue']) for r in type_breakdown],
        'channel_labels': [r['label'] for r in channel_breakdown],
        'channel_revenue': [float(r['revenue']) for r in channel_breakdown],
        'channel_gross_profit': [float(r['gross_profit']) for r in channel_breakdown],
        'channel_cogs': [float(r['cogs']) for r in channel_breakdown],
        'channel_qty': [r['qty'] for r in channel_breakdown],
        'channel_margin': [
            float(r['margin_pct']) if r['margin_pct'] is not None else None
            for r in channel_breakdown
        ],
    }
    chart.update(
        _build_profit_accumulation(daily_profit, start, end, date.today())
    )

    return {
        'totals': totals,
        'bucket_rows': bucket_rows,
        'weekday_rows': weekday_rows,
        'type_breakdown': type_breakdown,
        'channel_breakdown': channel_breakdown,
        'top_products': top_products,
        'top_customers': top_customers,
        'repeat_customers': repeat_customers,
        'unique_customers': len(customer_rows),
        'best_bucket': best_bucket,
        'best_weekday': best_weekday,
        'revenue_growth_pct': revenue_growth_pct,
        'avg_profit_per_order': avg_profit_per_order,
        'avg_items_per_order': avg_items_per_order,
        'unknown_cost_gross': _q2(unknown_cost_gross),
        'services_delivery_gross': _q2(services_delivery_gross),
        'chart': chart,
        'orders_scanned': len(orders),
        'stock_value_chart': build_stock_value_series(start, end),
    }


def _staff_ok(user):
    return bool(user.is_authenticated and user.is_superuser)


@login_required
@user_passes_test(_staff_ok)
def staff_sales_analytics(request):
    today = date.today()
    default_start = today - timedelta(days=365)
    start = _parse_date(request.GET.get('start'), default_start)
    end = _parse_date(request.GET.get('end'), today)
    if start > end:
        start, end = end, start
    granularity = request.GET.get('g') or 'month'
    if granularity not in ('day', 'week', 'month'):
        granularity = 'month'

    ctx = build_sales_analytics(start, end, granularity)
    month_metrics = build_current_month_metrics(today)

    ctx.update(
        {
            'start': start,
            'end': end,
            'granularity': granularity,
            'tax_rate_pct': TURNOVER_TAX_RATE * 100,
            'acquiring_rate_pct': ACQUIRING_RATE * 100,
            'staff_nav_active': 'sales_analytics',
            'page_heading': 'Sales analytics',
            'month_metrics': month_metrics,
            'ai_insights_url': reverse('administration:staff_sales_analytics_insights'),
            'ai_insight_kind': 'analytics',
        }
    )
    return render(request, 'shop/staff/sales_analytics.html', ctx)


def build_current_month_metrics(today: date | None = None) -> dict:
    """Calendar-month snapshot (1st → today), independent of the page date filter."""
    today = today or date.today()
    month_start = today.replace(day=1)
    orders = (
        SalesOrder.objects.filter(order_date__gte=month_start, order_date__lte=today)
        .exclude(status__in=_EXCLUDED_STATUSES)
        .prefetch_related(
            Prefetch(
                'lines',
                queryset=SalesOrderLine.objects.only(
                    'id',
                    'order_id',
                    'quantity',
                    'line_gross',
                    'landed_cost_gel',
                ),
            ),
            'payments',
        )
    )
    totals = _zero_metrics()
    for order in orders:
        _add_metrics(totals, compute_order_economics(order))
    metrics = _finalize_metrics(totals)
    return {
        'label': month_start.strftime('%B %Y'),
        'start': month_start,
        'end': today,
        'metrics': metrics,
    }


_ORDER_SORT_KEYS = frozenset(
    {
        'date',
        'invoice',
        'status',
        'customer',
        'revenue',
        'vat',
        'net',
        'net_proceeds',
        'cogs',
        'gross_profit',
        'income',
        'tax',
        'acquiring',
        'profit',
        'margin',
        'roi',
    }
)


def _order_row_sort_key(row: dict, sort: str, *, reverse: bool):
    order = row['order']
    m = row['metrics']

    def _nullable(v):
        # Keep empty ratios at the bottom for both directions.
        if v is None:
            return Decimal('-Infinity') if reverse else Decimal('Infinity')
        return v

    if sort == 'date':
        return order.order_date or date.min
    if sort == 'invoice':
        return (order.invoice_number or '').lower()
    if sort == 'status':
        return order.status or ''
    if sort == 'customer':
        cust = order.customer
        name = cust.display_name() if cust is not None else ''
        return name.lower()
    if sort == 'revenue':
        return m['revenue']
    if sort == 'vat':
        return m['vat']
    if sort == 'net':
        return m['net']
    if sort == 'net_proceeds':
        return m['net_proceeds']
    if sort == 'cogs':
        return m['cogs']
    if sort in ('gross_profit', 'income'):
        return m['gross_profit']
    if sort == 'tax':
        return m['tax']
    if sort == 'acquiring':
        return m['acquiring']
    if sort == 'profit':
        return m['profit']
    if sort == 'margin':
        return _nullable(m['margin_pct'])
    if sort == 'roi':
        return _nullable(m['roi_pct'])
    return order.order_date or date.min


def build_order_finance_table(
    start: date,
    end: date,
    *,
    invoice_q: str = '',
    sort: str = 'date',
    direction: str = 'desc',
) -> dict:
    """Per-order P&L rows for the Orders finance staff page."""
    qs = (
        SalesOrder.objects.filter(order_date__gte=start, order_date__lte=end)
        .select_related('customer')
        .prefetch_related(
            Prefetch(
                'lines',
                queryset=SalesOrderLine.objects.only(
                    'id',
                    'order_id',
                    'quantity',
                    'line_gross',
                    'landed_cost_gel',
                ),
            ),
            'payments',
        )
    )
    q = (invoice_q or '').strip()
    if q:
        qs = qs.filter(invoice_number__icontains=q)

    rows = []
    for order in qs:
        raw = compute_order_economics(order)
        metrics = _finalize_metrics(dict(raw))
        rows.append(
            {
                'order': order,
                'metrics': metrics,
                'raw': raw,
                'excluded': order.status in _EXCLUDED_STATUSES,
            }
        )

    if sort not in _ORDER_SORT_KEYS:
        sort = 'date'
    reverse = (direction or 'desc').lower() != 'asc'
    rows.sort(
        key=lambda r: _order_row_sort_key(r, sort, reverse=reverse),
        reverse=reverse,
    )

    totals_acc = _zero_metrics()
    included = 0
    for row in rows:
        if row['excluded']:
            continue
        _add_metrics(totals_acc, row['raw'])
        included += 1
    totals = _finalize_metrics(totals_acc)

    return {
        'rows': rows,
        'totals': totals,
        'orders_count': len(rows),
        'included_count': included,
        'sort': sort,
        'direction': 'asc' if not reverse else 'desc',
    }

@login_required
@user_passes_test(_staff_ok)
def staff_order_finance(request):
    """Separate tab: per-order financial breakdown with filters and sort."""
    today = date.today()
    default_start = today - timedelta(days=365)
    start = _parse_date(request.GET.get('start'), default_start)
    end = _parse_date(request.GET.get('end'), today)
    if start > end:
        start, end = end, start
    invoice_q = (request.GET.get('q') or '').strip()
    sort = (request.GET.get('sort') or 'date').strip()
    direction = (request.GET.get('dir') or 'desc').strip().lower()
    if direction not in ('asc', 'desc'):
        direction = 'desc'

    table = build_order_finance_table(
        start, end, invoice_q=invoice_q, sort=sort, direction=direction
    )
    month_metrics = build_current_month_metrics(today)
    ctx = {
        **table,
        'start': start,
        'end': end,
        'invoice_q': invoice_q,
        'tax_rate_pct': TURNOVER_TAX_RATE * 100,
        'acquiring_rate_pct': ACQUIRING_RATE * 100,
        'staff_nav_active': 'order_finance',
        'page_heading': 'Orders finance',
        'month_metrics': month_metrics,
        'sort_columns': [
            {'key': 'invoice', 'label': 'Invoice', 'num': False},
            {'key': 'date', 'label': 'Date', 'num': False},
            {'key': 'status', 'label': 'Status', 'num': False},
            {'key': 'customer', 'label': 'Customer', 'num': False},
            {'key': 'revenue', 'label': 'Revenue', 'num': True},
            {'key': 'vat', 'label': 'VAT', 'num': True},
            {'key': 'net', 'label': 'Net Revenue', 'num': True},
            {'key': 'tax', 'label': 'TAX', 'num': True},
            {'key': 'acquiring', 'label': 'Acquiring', 'num': True},
            {'key': 'net_proceeds', 'label': 'Net Proceeds', 'num': True},
            {'key': 'cogs', 'label': 'COGS', 'num': True},
            {'key': 'gross_profit', 'label': 'Gross Profit', 'num': True},
            {'key': 'profit', 'label': 'Profit', 'num': True},
            {'key': 'margin', 'label': 'Margin', 'num': True},
            {'key': 'roi', 'label': 'ROI', 'num': True},
        ],
        'ai_insights_url': reverse('administration:staff_sales_orders_insights'),
        'ai_insight_kind': 'orders',
        'csv_export_url': reverse('administration:staff_order_finance_csv'),
    }
    return render(request, 'shop/staff/order_finance.html', ctx)
