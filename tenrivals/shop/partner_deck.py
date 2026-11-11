"""Public partner briefing. Counts and a scale-free trend only.

Revenue, profit, ROI, and margins are never sent to the page. Revenue is a
set of bar rectangles and ROI is a polyline. Both are normalized on their own,
so the shape is visible and the amounts are not.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import qrcode
import qrcode.image.svg
from django.core.cache import cache
from django.db.models import Count, Sum
from django.shortcuts import render

from .catalog_utils import FEATURED_STOCK_BRANDS
from .models import (
    Product,
    ProductListing,
    ProductListingChannel,
    ProductType,
    SalesOrder,
    SalesOrderLine,
)
from .seo_catalog import absolute_public_shop_url
from .site_locale import DEFAULT_SHOP_SITE_LOCALE, shop_reverse
from .staff_analytics import _EXCLUDED_STATUSES, build_sales_analytics

logger = logging.getLogger(__name__)

# The briefing stops at October 2026. Later months stay off the chart.
CHART_END = date(2026, 10, 31)
CACHE_KEY = 'partner_deck_public_v3'
CACHE_SECONDS = 60 * 60 * 6

_CHART_W = 320
_CHART_H = 140
_CHART_PAD = 12


def partner_deck(request):
    facts = partner_public_facts()
    page_path = shop_reverse('shop:partners', site_locale=DEFAULT_SHOP_SITE_LOCALE)
    page_url = absolute_public_shop_url(request, page_path)
    return render(
        request,
        'shop/partners.html',
        {
            'facts': facts,
            'brands': FEATURED_STOCK_BRANDS,
            'page_url': page_url,
            'qr_svg': _qr_svg(page_url),
            'seo_canonical_url': page_url,
        },
    )


def partner_public_facts() -> dict:
    cached = cache.get(CACHE_KEY)
    if isinstance(cached, dict):
        return cached
    facts = _compute_public_facts()
    cache.set(CACHE_KEY, facts, CACHE_SECONDS)
    return facts


def _compute_public_facts() -> dict:
    stock_ids = ProductListing.objects.filter(
        channel=ProductListingChannel.STOCK,
        quantity__gt=0,
    ).values('product_id')
    stock_products = Product.objects.filter(is_active=True, pk__in=stock_ids)
    units = (
        ProductListing.objects.filter(
            channel=ProductListingChannel.STOCK,
            product__is_active=True,
        ).aggregate(total=Sum('quantity'))['total']
        or 0
    )
    brand_names = {
        (name or '').strip()
        for name in stock_products.values_list('brand', flat=True)
        if (name or '').strip()
    }
    categories = {
        code
        for code in stock_products.values_list('type', flat=True)
        if (code or '').strip()
    }
    orders = SalesOrder.objects.exclude(status__in=_EXCLUDED_STATUSES)
    clients = orders.values('customer_id').distinct().count()
    repeat_clients = (
        orders.values('customer_id').annotate(n=Count('id')).filter(n__gt=1).count()
    )
    first_on = (
        orders.order_by('order_date').values_list('order_date', flat=True).first()
    )

    revenue_bars: list[dict] = []
    roi_points = ''
    growth_pct = None
    months_active = 0
    since_year = None
    if first_on and first_on <= CHART_END:
        since_year = first_on.year
        months_active = (
            (CHART_END.year - first_on.year) * 12
            + (CHART_END.month - first_on.month)
            + 1
        )
        revenue_bars, roi_points, growth_pct = _trend(first_on, CHART_END)

    return {
        'clients': clients,
        'orders': orders.count(),
        'repeat_clients': repeat_clients,
        'units_in_stock': int(units),
        'skus': stock_products.count(),
        'brands': len(brand_names),
        'categories': len(categories),
        'since_year': since_year,
        'months_active': months_active,
        'growth_pct': growth_pct,
        'revenue_bars': revenue_bars,
        'roi_points': roi_points,
        'category_mix': _category_mix(),
    }


def _trend(start: date, end: date) -> tuple[list[dict], str, int | None]:
    """Bar and line geometry plus a compound monthly growth percent.

    Raw revenue and ROI never leave this function. Revenue bars are scaled
    from zero to the peak. The ROI line is scaled on its own min and max.
    """
    try:
        chart = build_sales_analytics(start, end, 'month')['chart']
    except Exception:
        logger.exception('Partner deck trend failed')
        return [], '', None
    revenue = [float(v or 0) for v in chart.get('revenue') or []]
    roi = _carry(list(chart.get('roi') or []))
    growth = _compound_monthly_growth_pct(revenue)
    if len(revenue) < 2:
        return [], '', growth
    return _bars(revenue), _polyline(roi), growth


def _category_mix() -> list[dict]:
    """Share of sales by category. Percents only, never the amounts."""
    rows = (
        SalesOrderLine.objects.filter(order__order_date__lte=CHART_END)
        .exclude(order__status__in=_EXCLUDED_STATUSES)
        .values('product_type')
        .annotate(money=Sum('line_gross'))
        .order_by('-money', 'product_type')
    )
    grouped: dict[str, Decimal] = {}
    labels = dict(ProductType.choices)
    for row in rows:
        money = row['money'] or Decimal('0')
        if money <= 0:
            continue
        code = row['product_type'] or ''
        label = labels.get(code, 'Other')
        grouped[label] = grouped.get(label, Decimal('0')) + money
    total = sum(grouped.values(), Decimal('0'))
    if total <= 0:
        return []
    ranked = sorted(grouped.items(), key=lambda item: (-item[1], item[0]))
    top = ranked[:5]
    items = [
        {'label': label, 'share': int(round(100 * money / total))}
        for label, money in top
    ]
    rest = total - sum((money for _, money in top), Decimal('0'))
    if rest > 0:
        other = int(round(100 * rest / total))
        if items and items[-1]['label'] == 'Other':
            items[-1]['share'] += other
        else:
            items.append({'label': 'Other', 'share': other})
    return items


def _carry(values: list) -> list[float]:
    """Fill gaps so a missing month does not drop the ROI line to zero."""
    filled: list[float | None] = []
    last = None
    for value in values:
        if value is None:
            filled.append(last)
        else:
            last = float(value)
            filled.append(last)
    first = next((v for v in filled if v is not None), None)
    if first is None:
        return []
    return [first if v is None else v for v in filled]


def _compound_monthly_growth_pct(revenue: list[float]) -> int | None:
    positive = [i for i, value in enumerate(revenue) if value > 0]
    if len(positive) < 2:
        return None
    start_i, end_i = positive[0], positive[-1]
    steps = end_i - start_i
    base = revenue[start_i]
    if steps <= 0 or base <= 0:
        return None
    rate = (revenue[end_i] / base) ** (1 / steps) - 1
    return int(round(rate * 100))


def _bars(values: list[float]) -> list[dict]:
    """Column geometry scaled from zero to the peak. No raw amounts."""
    peak = max(values) if values else 0
    if peak <= 0:
        return []
    n = len(values)
    inner_w = _CHART_W - 2 * _CHART_PAD
    slot = inner_w / n
    bar_w = slot * 0.62
    baseline = _CHART_H - _CHART_PAD
    usable = _CHART_H - 2 * _CHART_PAD
    bars = []
    for i, value in enumerate(values):
        height = usable * (max(value, 0) / peak)
        x = _CHART_PAD + slot * i + (slot - bar_w) / 2
        bars.append(
            {
                'x': f'{x:.1f}',
                'y': f'{baseline - height:.1f}',
                'w': f'{bar_w:.1f}',
                'h': f'{height:.1f}',
            }
        )
    return bars


def _polyline(values: list[float]) -> str:
    if len(values) < 2:
        return ''
    lo = min(values)
    hi = max(values)
    span = hi - lo
    n = len(values)
    coords = []
    for i, value in enumerate(values):
        x = _CHART_PAD + (_CHART_W - 2 * _CHART_PAD) * (i / (n - 1))
        if span == 0:
            y = _CHART_H / 2
        else:
            y = _CHART_PAD + (_CHART_H - 2 * _CHART_PAD) * (1 - (value - lo) / span)
        coords.append(f'{x:.1f},{y:.1f}')
    return ' '.join(coords)


def _qr_svg(url: str) -> str:
    image = qrcode.make(
        url,
        image_factory=qrcode.image.svg.SvgPathImage,
        border=1,
        box_size=8,
    )
    raw = image.to_string()
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8')
    # Drop the XML declaration so the markup can sit inside the page.
    if raw.lstrip().startswith('<?xml'):
        raw = raw.split('?>', 1)[1].strip()
    return raw
