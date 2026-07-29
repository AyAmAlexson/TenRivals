"""Staff-only sales analytics dashboard (landed-cost economics).

Metric definitions (agreed with the owner):
    Revenue   = gross_total (VAT-inclusive ₾)
    VAT       = included 18%  -> gross − gross / 1.18 (stored vat_total)
    TAX       = 1% turnover tax on gross
    Acquiring = 2% of gross for card payments (payment_method heuristic)
    COGS      = Σ qty × landed_cost_gel over lines with a known cost
              + Σ service contractor costs + delivery_cost_gel
    Income    = (Revenue − VAT) − COGS
    Profit    = Income − TAX − Acquiring
    Margin    = Profit / Revenue
    ROI       = Profit / COGS

Cancelled / refunded orders are excluded everywhere. Lines without a landed
cost contribute to Revenue but not to COGS — the dashboard reports "cost
coverage" so profit figures can be read with the right confidence.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Prefetch
from django.shortcuts import render

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


def _zero_metrics() -> dict:
    return {
        'orders': 0,
        'revenue': Decimal('0.00'),
        'vat': Decimal('0.00'),
        'tax': Decimal('0.00'),
        'acquiring': Decimal('0.00'),
        'cogs': Decimal('0.00'),
        'income': Decimal('0.00'),
        'profit': Decimal('0.00'),
        'covered_gross': Decimal('0.00'),
        'product_gross': Decimal('0.00'),
        'card_revenue': Decimal('0.00'),
        'items': 0,
    }


def _finalize_metrics(m: dict) -> dict:
    """Derived ratios; call after summing raw values."""
    revenue = m['revenue']
    cogs = m['cogs']
    profit = m['profit']
    m['net'] = _q2(revenue - m['vat'])
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
    net = gross - vat
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
    card = is_card_payment(order.payment_method)
    acquiring = _q2(gross * ACQUIRING_RATE) if card else Decimal('0.00')
    income = net - cogs
    profit = income - tax - acquiring
    return {
        'orders': 1,
        'revenue': gross,
        'vat': vat,
        'tax': tax,
        'acquiring': acquiring,
        'cogs': _q2(cogs),
        'income': _q2(income),
        'profit': _q2(profit),
        'covered_gross': covered_gross,
        'product_gross': product_gross,
        'card_revenue': gross if card else Decimal('0.00'),
        'items': items,
    }


def _add_metrics(acc: dict, m: dict) -> None:
    for k, v in m.items():
        acc[k] += v


def _parse_date(raw: str, default: date) -> date:
    try:
        return date.fromisoformat((raw or '').strip())
    except (TypeError, ValueError):
        return default


_WEEKDAY_LABELS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


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
            )
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
            'income': Decimal('0.00'),
            'covered_gross': Decimal('0.00'),
            'lines': 0,
        }
        for key in _CHANNEL_ORDER
    }
    customer_rows: dict[int, dict] = {}
    services_delivery_gross = Decimal('0.00')

    for o in orders:
        m = compute_order_economics(o)
        _add_metrics(totals, m)
        b = _bucket_start(o.order_date, granularity)
        if b not in buckets:
            buckets[b] = _zero_metrics()
        _add_metrics(buckets[b], m)
        _add_metrics(weekdays[o.order_date.weekday()], m)
        services_delivery_gross += m['revenue'] - m['product_gross']

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
                crow_ch['income'] += line_net - line_cogs
                crow_ch['covered_gross'] += line_gross

            if line.product_id:
                type_label = line.product.get_type_display()
            else:
                type_label = 'Custom / legacy'
            trow = type_rows.setdefault(
                type_label,
                {
                    'label': type_label,
                    'qty': 0,
                    'revenue': Decimal('0.00'),
                    'cogs': Decimal('0.00'),
                    'income': Decimal('0.00'),
                    'covered_gross': Decimal('0.00'),
                },
            )
            trow['qty'] += int(line.quantity or 0)
            trow['revenue'] += line_gross
            if line_cogs is not None:
                trow['cogs'] += line_cogs
                trow['income'] += line_net - line_cogs
                trow['covered_gross'] += line_gross

            if line_cogs is not None:
                plabel = line.display_title()
                line_income = line_net - line_cogs
                line_tax = _q2(line_gross * TURNOVER_TAX_RATE)
                line_acq = (
                    _q2(line_gross * ACQUIRING_RATE)
                    if is_card_payment(o.payment_method)
                    else Decimal('0.00')
                )
                line_profit = _q2(line_income - line_tax - line_acq)
                prow = product_rows.setdefault(
                    plabel,
                    {
                        'label': plabel,
                        'qty': 0,
                        'revenue': Decimal('0.00'),
                        'income': Decimal('0.00'),
                        'profit': Decimal('0.00'),
                    },
                )
                prow['qty'] += int(line.quantity or 0)
                prow['revenue'] += line_gross
                prow['income'] += line_income
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
        # Category ROI/margin use line-level income (before order-level tax
        # and acquiring, which cannot be attributed to a single line).
        trow['roi_pct'] = (
            (trow['income'] / trow['cogs'] * 100).quantize(_Q2)
            if trow['cogs']
            else None
        )
        trow['margin_pct'] = (
            (trow['income'] / trow['revenue'] * 100).quantize(_Q2)
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
            (crow['income'] / crow['cogs'] * 100).quantize(_Q2)
            if crow['cogs']
            else None
        )
        crow['margin_pct'] = (
            (crow['income'] / crow['revenue'] * 100).quantize(_Q2)
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
        'income': [float(r['income']) for r in bucket_rows],
        'profit': [float(r['profit']) for r in bucket_rows],
        'cogs': [float(r['cogs']) for r in bucket_rows],
        'orders': [r['orders'] for r in bucket_rows],
        'margin': [
            float(r['margin_pct']) if r['margin_pct'] is not None else None
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
        'channel_income': [float(r['income']) for r in channel_breakdown],
        'channel_cogs': [float(r['cogs']) for r in channel_breakdown],
        'channel_qty': [r['qty'] for r in channel_breakdown],
        'channel_margin': [
            float(r['margin_pct']) if r['margin_pct'] is not None else None
            for r in channel_breakdown
        ],
    }

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
    ctx.update(
        {
            'start': start,
            'end': end,
            'granularity': granularity,
            'tax_rate_pct': TURNOVER_TAX_RATE * 100,
            'acquiring_rate_pct': ACQUIRING_RATE * 100,
            'staff_nav_active': 'sales_analytics',
            'page_heading': 'Sales analytics',
        }
    )
    return render(request, 'shop/staff/sales_analytics.html', ctx)
