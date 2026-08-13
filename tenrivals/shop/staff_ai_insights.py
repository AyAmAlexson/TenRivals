"""Staff AI brief packs: snapshot + prompt download for off-site analysis.

On-site LLM generation is disabled. Staff export a Markdown file (prompt + live
data + expected JSON schema) and run it in an external model.
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import SalesOrder, SalesOrderLine
from .staff_analytics import (
    _EXCLUDED_STATUSES,
    build_sales_analytics,
)
from .staff_stock_stats import build_stock_stats, stock_products_queryset, _stock_qty, _unit_price_stock

ANALYTICS_PROMPT_VERSION = 'exec-analytics-v1'
STOCK_PROMPT_VERSION = 'exec-stock-v2'
KIND_ANALYTICS = 'analytics'
KIND_STOCK = 'stock'

_SEVERITY = ('high', 'medium', 'low')
_HORIZON = ('this_week', 'this_month', '90_days')
_PRIORITY = ('P0', 'P1', 'P2')


class InsightError(Exception):
    """Staff-visible failure while generating a board brief."""


def _staff_ok(user) -> bool:
    return bool(user.is_authenticated and user.is_superuser)


def _n(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


def earliest_order_date() -> date | None:
    return (
        SalesOrder.objects.exclude(status__in=_EXCLUDED_STATUSES)
        .order_by('order_date')
        .values_list('order_date', flat=True)
        .first()
    )


def _compact_analytics(label: str, start: date, end: date, ctx: dict) -> dict:
    totals = ctx['totals']
    revenue = totals.get('revenue') or Decimal('0')
    customers = []
    for c in ctx.get('top_customers') or []:
        share = None
        if revenue:
            share = float((c['revenue'] / revenue * 100).quantize(Decimal('0.01')))
        customers.append(
            {
                'name': c['label'],
                'orders': c['orders'],
                'revenue': _n(c['revenue']),
                'profit': _n(c['profit']),
                'share_pct': share,
                'last_order': _n(c['last_order']),
            }
        )
    monthly = []
    for r in ctx.get('bucket_rows') or []:
        if not r.get('orders'):
            continue
        monthly.append(
            {
                'period': r['label'],
                'orders': r['orders'],
                'revenue': _n(r['revenue']),
                'profit': _n(r['profit']),
                'roi_pct': _n(r.get('roi_pct')),
                'margin_pct': _n(r.get('margin_pct')),
                'aov': _n(r.get('aov')),
                'coverage_pct': _n(r.get('coverage_pct')),
            }
        )
    if len(monthly) > 24:
        monthly = monthly[-24:]
    stock_today = (ctx.get('stock_value_chart') or {}).get('today') or {}
    return {
        'label': label,
        'start': start.isoformat(),
        'end': end.isoformat(),
        'orders': totals.get('orders', 0),
        'revenue': _n(totals.get('revenue')),
        'vat': _n(totals.get('vat')),
        'net': _n(totals.get('net')),
        'cogs': _n(totals.get('cogs')),
        'income': _n(totals.get('income')),
        'tax': _n(totals.get('tax')),
        'acquiring': _n(totals.get('acquiring')),
        'profit': _n(totals.get('profit')),
        'margin_pct': _n(totals.get('margin_pct')),
        'roi_pct': _n(totals.get('roi_pct')),
        'coverage_pct': _n(totals.get('coverage_pct')),
        'aov': _n(totals.get('aov')),
        'items': totals.get('items', 0),
        'avg_items_per_order': _n(ctx.get('avg_items_per_order')),
        'avg_profit_per_order': _n(ctx.get('avg_profit_per_order')),
        'unique_customers': ctx.get('unique_customers', 0),
        'repeat_customers': ctx.get('repeat_customers', 0),
        'card_share_pct': _n(totals.get('card_share_pct')),
        'card_revenue': _n(totals.get('card_revenue')),
        'unknown_cost_revenue': _n(ctx.get('unknown_cost_gross')),
        'services_delivery_revenue': _n(ctx.get('services_delivery_gross')),
        'best_period': (
            {
                'label': ctx['best_bucket']['label'],
                'profit': _n(ctx['best_bucket']['profit']),
                'revenue': _n(ctx['best_bucket']['revenue']),
                'orders': ctx['best_bucket']['orders'],
            }
            if ctx.get('best_bucket')
            else None
        ),
        'best_weekday': (
            {
                'label': ctx['best_weekday']['label'],
                'revenue': _n(ctx['best_weekday']['revenue']),
                'orders': ctx['best_weekday']['orders'],
                'share_pct': _n(ctx['best_weekday'].get('share_pct')),
            }
            if ctx.get('best_weekday')
            else None
        ),
        'channels': [
            {
                'label': r['label'],
                'qty': r['qty'],
                'revenue': _n(r['revenue']),
                'income': _n(r['income']),
                'share_pct': _n(r.get('share_pct')),
                'roi_pct': _n(r.get('roi_pct')),
                'coverage_pct': _n(r.get('coverage_pct')),
            }
            for r in (ctx.get('channel_breakdown') or [])
        ],
        'categories': [
            {
                'label': r['label'],
                'qty': r['qty'],
                'revenue': _n(r['revenue']),
                'income': _n(r['income']),
                'share_pct': _n(r.get('share_pct')),
                'roi_pct': _n(r.get('roi_pct')),
                'coverage_pct': _n(r.get('coverage_pct')),
            }
            for r in (ctx.get('type_breakdown') or [])[:14]
        ],
        'top_products': [
            {
                'label': r['label'],
                'qty': r['qty'],
                'revenue': _n(r['revenue']),
                'income': _n(r['income']),
                'profit': _n(r['profit']),
            }
            for r in (ctx.get('top_products') or [])[:12]
        ],
        'top_customers': customers[:8],
        'weekdays': [
            {
                'label': r['label'],
                'orders': r['orders'],
                'revenue': _n(r['revenue']),
                'profit': _n(r['profit']),
                'aov': _n(r.get('aov')),
                'share_pct': _n(r.get('share_pct')),
            }
            for r in (ctx.get('weekday_rows') or [])
        ],
        'monthly_trend': monthly,
        'stock_on_hand': {
            'shelf_value_gel': _n(stock_today.get('shelf_value_gel')),
            'landed_value_gel': _n(stock_today.get('landed_value_gel')),
            'units': stock_today.get('units'),
        }
        if stock_today
        else None,
    }


def build_analytics_insight_payload(*, today: date | None = None) -> dict:
    today = today or date.today()
    earliest = earliest_order_date()
    if not earliest:
        return {
            'as_of': today.isoformat(),
            'business': _business_context(),
            'note': 'No completed sales orders yet.',
            'periods': [],
        }

    year_start = date(today.year, 1, 1)
    month_start = date(today.year, today.month, 1)
    ly_last_day = calendar.monthrange(today.year - 1, today.month)[1]
    ly_mtd_day = min(today.day, ly_last_day)
    ly_mtd_start = date(today.year - 1, today.month, 1)
    ly_mtd_end = date(today.year - 1, today.month, ly_mtd_day)
    ly_full_end = date(today.year - 1, today.month, ly_last_day)

    specs = [
        ('this_month', month_start, today),
        ('this_year', max(year_start, earliest), today),
        ('all_time', earliest, today),
    ]
    if ly_mtd_end >= earliest:
        specs.append(('same_month_last_year_mtd', max(ly_mtd_start, earliest), min(ly_mtd_end, today)))
        if ly_full_end >= earliest and today.day >= ly_last_day:
            specs.append(('same_month_last_year_full', max(ly_mtd_start, earliest), min(ly_full_end, today)))
        elif ly_full_end >= earliest and ly_full_end != ly_mtd_end:
            specs.append(('same_month_last_year_full_available', max(ly_mtd_start, earliest), min(ly_full_end, today)))

    periods = []
    for label, start, end in specs:
        if start > end:
            continue
        ctx = build_sales_analytics(start, end, 'month')
        periods.append(_compact_analytics(label, start, end, ctx))

    return {
        'as_of': today.isoformat(),
        'business': _business_context(),
        'definitions': {
            'revenue': 'VAT-inclusive gross ₾',
            'vat': '18% included',
            'tax': '1% turnover tax on gross',
            'acquiring': '2% of gross on card/POS payments',
            'cogs': 'known landed costs + service/delivery contractor costs',
            'income': '(Revenue − VAT) − COGS',
            'profit': 'Income − TAX − Acquiring',
            'roi': 'Profit / COGS',
            'coverage': 'share of product revenue with a known landed cost',
            'cancelled_refunded': 'excluded',
        },
        'periods': periods,
        'comparison_notes': [
            'this_month is calendar MTD (1st → as_of).',
            'same_month_last_year_mtd is like-for-like day count last year.',
            'same_month_last_year_full is the full calendar month last year when data exists.',
            'all_time starts at the first completed order.',
        ],
    }


def _business_context() -> dict:
    return {
        'brand': 'Tenrivals',
        'model': 'Curated tennis specialty retail, Georgia (Tbilisi), prices in GEL',
        'courts': 'Local play is heavily clay; hard/all-court also matters; juniors growing',
        'channels': 'In-stock warehouse + preorder catalog + staff sales orders',
        'tools_available': [
            'Buying module (supplier search, landed-cost scenarios, FX/NBG)',
            'Stock receive (batches + average landed cost)',
            'Product create / edit + AI listing fill',
            'Preorder catalog',
            'Promo codes',
            'Customer list / outreach',
            'Sales orders (stock reservation, free-text legacy lines)',
        ],
        'constraints': [
            'Working capital is limited — do not recommend buying everything that sells',
            'Unknown landed costs make profit look better/worse than reality — flag coverage',
            'Do not invent SKUs, quantities, or prices that are not in the data',
            'Seasonality: spring–autumn outdoor tennis; August is peak summer',
        ],
    }


def _sales_velocity(today: date) -> dict:
    d30 = today - timedelta(days=29)
    d90 = today - timedelta(days=89)
    lines = (
        SalesOrderLine.objects.filter(order__order_date__gte=d90, order__order_date__lte=today)
        .exclude(order__status__in=_EXCLUDED_STATUSES)
        .select_related('product', 'order')
    )
    by_product_30: dict[int, dict] = defaultdict(lambda: {'qty': 0, 'revenue': Decimal('0'), 'label': ''})
    by_product_90: dict[int, dict] = defaultdict(lambda: {'qty': 0, 'revenue': Decimal('0'), 'label': ''})
    by_cat_30: dict[str, dict] = defaultdict(lambda: {'qty': 0, 'revenue': Decimal('0')})
    by_cat_90: dict[str, dict] = defaultdict(lambda: {'qty': 0, 'revenue': Decimal('0')})
    channel_30 = defaultdict(lambda: {'qty': 0, 'revenue': Decimal('0')})

    for line in lines:
        qty = int(line.quantity or 0)
        rev = line.line_gross or Decimal('0')
        cat = line.analytics_category_label()
        ch = line.sale_channel or SalesOrderLine.SaleChannel.STOCK
        on_30 = line.order.order_date >= d30
        by_cat_90[cat]['qty'] += qty
        by_cat_90[cat]['revenue'] += rev
        if on_30:
            by_cat_30[cat]['qty'] += qty
            by_cat_30[cat]['revenue'] += rev
            channel_30[ch]['qty'] += qty
            channel_30[ch]['revenue'] += rev
        pid = line.product_id
        if not pid:
            continue
        label = line.display_title()
        by_product_90[pid]['qty'] += qty
        by_product_90[pid]['revenue'] += rev
        by_product_90[pid]['label'] = label
        if on_30:
            by_product_30[pid]['qty'] += qty
            by_product_30[pid]['revenue'] += rev
            by_product_30[pid]['label'] = label

    return {
        'by_product_30': by_product_30,
        'by_product_90': by_product_90,
        'by_cat_30': by_cat_30,
        'by_cat_90': by_cat_90,
        'channel_30': channel_30,
        'd30': d30,
        'd90': d90,
    }


def _weeks_cover(on_hand: int, sold_30: int) -> float | None:
    if sold_30 <= 0:
        return None
    return round(on_hand * 7 / sold_30, 1)


def _short(text, n: int = 72) -> str:
    s = re.sub(r'\s+', ' ', (text or '').strip())
    return s if len(s) <= n else s[: n - 1].rstrip() + '…'


def _sku_brief(row: dict) -> dict:
    brand = _short(row.get('brand') or '', 28)
    name = _short(row.get('name') or '', 64)
    label = f'{brand} {name}'.strip()
    return {
        'sku': label,
        'type': row['type'],
        'qty': row['qty'],
        'shelf': row['shelf_value'],
        'landed': row['landed_unit'],
        's30': row['sold_30d'],
        's90': row['sold_90d'],
        'woc': row['weeks_cover'],
    }


def _group_shoe_gaps(gaps: list[dict], *, limit_groups: int = 10) -> list[dict]:
    by_col: dict[str, list[str]] = defaultdict(list)
    for gap in gaps:
        col = str(gap.get('column') or '').strip() or 'Unknown'
        size = str(gap.get('size') or '').strip()
        if size and size not in by_col[col]:
            by_col[col].append(size)
    out = []
    for col, sizes in sorted(by_col.items(), key=lambda x: (-len(x[1]), x[0])):
        out.append({'column': col, 'hot_zero_sizes': sizes[:18], 'count': len(sizes)})
        if len(out) >= limit_groups:
            break
    return out


def _group_racket_holes(holes: list[dict], *, limit_groups: int = 10) -> list[dict]:
    by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
    for hole in holes:
        tier = str(hole.get('tier') or '').strip()
        weight = str(hole.get('weight') or '').strip()
        grip = str(hole.get('grip') or '').strip()
        if grip and grip not in by_key[(tier, weight)]:
            by_key[(tier, weight)].append(grip)
    out = []
    for (tier, weight), grips in sorted(by_key.items(), key=lambda x: (-len(x[1]), x[0][0], x[0][1])):
        out.append(
            {
                'tier': tier,
                'weight': weight,
                'empty_grips': sorted(grips),
                'count': len(grips),
            }
        )
        if len(out) >= limit_groups:
            break
    return out


def build_stock_insight_payload(*, today: date | None = None) -> dict:
    today = today or date.today()
    stats = build_stock_stats()
    vel = _sales_velocity(today)
    products = list(stock_products_queryset())

    cat_on_hand: dict[str, dict] = defaultdict(
        lambda: {'units': 0, 'shelf_value': Decimal('0'), 'landed_value': Decimal('0'), 'skus': 0}
    )
    sku_rows = []
    landed_known_units = 0
    landed_value = Decimal('0')
    for p in products:
        qty = _stock_qty(p)
        unit = _unit_price_stock(p)
        shelf = unit * qty
        landed_unit = p.landed_cost_gel
        landed = (landed_unit * qty) if landed_unit is not None else None
        cat = p.get_type_display()
        cat_on_hand[cat]['units'] += qty
        cat_on_hand[cat]['shelf_value'] += shelf
        cat_on_hand[cat]['skus'] += 1
        if landed is not None:
            cat_on_hand[cat]['landed_value'] += landed
            landed_value += landed
            landed_known_units += qty
        sold30 = vel['by_product_30'].get(p.pk, {}).get('qty', 0)
        sold90 = vel['by_product_90'].get(p.pk, {}).get('qty', 0)
        sku_rows.append(
            {
                'id': p.pk,
                'brand': (p.brand or '').strip(),
                'name': p.name,
                'type': cat,
                'qty': qty,
                'unit_price': _n(unit),
                'shelf_value': _n(shelf),
                'landed_unit': _n(landed_unit),
                'sold_30d': sold30,
                'sold_90d': sold90,
                'weeks_cover': _weeks_cover(qty, sold30),
                'created': p.created_at.date().isoformat() if p.created_at else None,
            }
        )

    sku_rows.sort(key=lambda r: (-(r['shelf_value'] or 0), r['name']))
    by_value = sku_rows[:40]
    fast = [r for r in sku_rows if r['sold_30d'] and (r['weeks_cover'] is not None) and r['weeks_cover'] <= 4]
    fast.sort(key=lambda r: (r['weeks_cover'] or 0, -r['sold_30d']))
    dead = [r for r in sku_rows if r['sold_90d'] == 0]
    dead.sort(key=lambda r: -(r['shelf_value'] or 0))
    excess = [
        r
        for r in sku_rows
        if r['sold_30d'] > 0 and r['weeks_cover'] is not None and r['weeks_cover'] >= 16
    ]
    excess.sort(key=lambda r: -(r['weeks_cover'] or 0))

    categories = []
    for name, row in sorted(cat_on_hand.items(), key=lambda x: -x[1]['units']):
        sold30 = vel['by_cat_30'].get(name, {}).get('qty', 0)
        sold90 = vel['by_cat_90'].get(name, {}).get('qty', 0)
        rev30 = vel['by_cat_30'].get(name, {}).get('revenue', Decimal('0'))
        categories.append(
            {
                'type': name,
                'skus': row['skus'],
                'units_on_hand': row['units'],
                'shelf_value': _n(row['shelf_value']),
                'landed_value': _n(row['landed_value']),
                'sold_30d': sold30,
                'sold_90d': sold90,
                'revenue_30d': _n(rev30),
                'weeks_cover': _weeks_cover(row['units'], sold30),
            }
        )

    shoe_gaps = []
    for row in stats.get('shoe_rows') or []:
        for i, cell in enumerate(row.get('cells') or []):
            disp = str(cell.get('display') or '')
            if disp == '🔴':
                col = (stats.get('shoe_col_labels') or [''])[i] if i < len(stats.get('shoe_col_labels') or []) else ''
                shoe_gaps.append({'size': row['label'], 'column': col})

    def _racket_holes(table, tier: str):
        holes = []
        labels = stats.get('racket_weight_col_labels') or []
        for row in table or []:
            for i, cell in enumerate(row.get('cells') or []):
                if not cell.get('value'):
                    wlab = labels[i] if i < len(labels) else ''
                    holes.append({'tier': tier, 'grip': f"L{row.get('grip')}", 'weight': wlab})
        return holes

    racket_holes = _racket_holes(stats.get('racket_table_low'), '≤600₾') + _racket_holes(
        stats.get('racket_table_high'), '601+₾'
    )

    analytics_ytd = None
    earliest = earliest_order_date()
    if earliest:
        ytd = build_sales_analytics(max(date(today.year, 1, 1), earliest), today, 'month')
        analytics_ytd = {
            'orders': ytd['totals']['orders'],
            'revenue': _n(ytd['totals']['revenue']),
            'profit': _n(ytd['totals']['profit']),
            'roi_pct': _n(ytd['totals'].get('roi_pct')),
            'coverage_pct': _n(ytd['totals'].get('coverage_pct')),
            'channels': [
                {
                    'label': r['label'],
                    'qty': r['qty'],
                    'revenue': _n(r['revenue']),
                    'share_pct': _n(r.get('share_pct')),
                }
                for r in (ytd.get('channel_breakdown') or [])
            ],
            'categories': [
                {
                    'label': r['label'],
                    'qty': r['qty'],
                    'revenue': _n(r['revenue']),
                    'share_pct': _n(r.get('share_pct')),
                    'roi_pct': _n(r.get('roi_pct')),
                }
                for r in (ytd.get('type_breakdown') or [])[:12]
            ],
            'top_products': [
                {'label': _short(r['label'], 56), 'qty': r['qty'], 'revenue': _n(r['revenue']), 'profit': _n(r['profit'])}
                for r in (ytd.get('top_products') or [])[:8]
            ],
        }
        mtd = build_sales_analytics(date(today.year, today.month, 1), today, 'month')
        analytics_mtd = {
            'orders': mtd['totals']['orders'],
            'revenue': _n(mtd['totals']['revenue']),
            'profit': _n(mtd['totals']['profit']),
            'categories': [
                {'label': r['label'], 'qty': r['qty'], 'revenue': _n(r['revenue'])}
                for r in (mtd.get('type_breakdown') or [])[:8]
            ],
        }
    else:
        analytics_mtd = None

    return {
        'as_of': today.isoformat(),
        'business': _business_context(),
        'stock_kpis': {
            'skus': stats.get('positions'),
            'units': stats.get('total_units'),
            'shelf_value': _n(stats.get('total_value')),
            'landed_value_known': _n(landed_value),
            'units_with_known_landed_cost': landed_known_units,
        },
        'category_mix': categories,
        'brand_capital_top': [
            {'brand': b, 'shelf_value': v}
            for b, v in zip(
                (stats.get('charts') or {}).get('brandsTop', {}).get('labels') or [],
                (stats.get('charts') or {}).get('brandsTop', {}).get('values') or [],
            )
        ],
        'price_buckets': [
            {'bucket': lab, 'units': val}
            for lab, val in zip(
                (stats.get('charts') or {}).get('priceBuckets', {}).get('labels') or [],
                (stats.get('charts') or {}).get('priceBuckets', {}).get('values') or [],
            )
        ],
        'shoe_hot_size_gaps': _group_shoe_gaps(shoe_gaps),
        'racket_empty_cells': _group_racket_holes(racket_holes),
        'skus_by_shelf_value': [_sku_brief(r) for r in by_value[:18]],
        'fast_movers_low_cover': [_sku_brief(r) for r in fast[:12]],
        'dead_stock_no_sales_90d': [_sku_brief(r) for r in dead[:12]],
        'excess_high_cover': [_sku_brief(r) for r in excess[:12]],
        'sales_30d_channels': [
            {'channel': k, 'qty': v['qty'], 'revenue': _n(v['revenue'])}
            for k, v in vel['channel_30'].items()
        ],
        'sales_ytd': analytics_ytd,
        'sales_mtd': analytics_mtd,
        'windows': {
            'sold_30d_from': vel['d30'].isoformat(),
            'sold_90d_from': vel['d90'].isoformat(),
        },
        'cover_rules': {
            'fast_if_weeks_cover_lte': 4,
            'excess_if_weeks_cover_gte': 16,
            'dead_if_no_sales_days': 90,
            'weeks_cover_formula': 'on_hand * 7 / units_sold_last_30d; null if no 30d sales',
        },
    }


def _item_schema(properties: dict, required: list[str]) -> dict:
    return {
        'type': 'object',
        'additionalProperties': False,
        'properties': properties,
        'required': required,
    }


def _str_arr(item_props: dict, item_required: list[str]) -> dict:
    return {'type': 'array', 'items': _item_schema(item_props, item_required)}


ANALYTICS_JSON_SCHEMA = {
    'name': 'staff_analytics_insights',
    'strict': True,
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'headline': {'type': 'string'},
            'executive_summary': {'type': 'string'},
            'scorecard_narrative': {'type': 'string'},
            'period_takeaways': _str_arr(
                {'period': {'type': 'string'}, 'takeaway': {'type': 'string'}},
                ['period', 'takeaway'],
            ),
            'what_is_working': _str_arr(
                {'title': {'type': 'string'}, 'detail': {'type': 'string'}},
                ['title', 'detail'],
            ),
            'attention': _str_arr(
                {
                    'title': {'type': 'string'},
                    'severity': {'type': 'string', 'enum': list(_SEVERITY)},
                    'detail': {'type': 'string'},
                    'why_it_matters': {'type': 'string'},
                },
                ['title', 'severity', 'detail', 'why_it_matters'],
            ),
            'recommendations': _str_arr(
                {
                    'action': {'type': 'string'},
                    'horizon': {'type': 'string', 'enum': list(_HORIZON)},
                    'how': {'type': 'string'},
                    'tools': {'type': 'string'},
                    'expected_impact': {'type': 'string'},
                },
                ['action', 'horizon', 'how', 'tools', 'expected_impact'],
            ),
            'risks': _str_arr(
                {'risk': {'type': 'string'}, 'mitigation': {'type': 'string'}},
                ['risk', 'mitigation'],
            ),
            'data_quality': _str_arr(
                {'issue': {'type': 'string'}, 'fix': {'type': 'string'}},
                ['issue', 'fix'],
            ),
            'kpis_to_watch': _str_arr(
                {
                    'kpi': {'type': 'string'},
                    'reading': {'type': 'string'},
                    'watch_for': {'type': 'string'},
                },
                ['kpi', 'reading', 'watch_for'],
            ),
        },
        'required': [
            'headline',
            'executive_summary',
            'scorecard_narrative',
            'period_takeaways',
            'what_is_working',
            'attention',
            'recommendations',
            'risks',
            'data_quality',
            'kpis_to_watch',
        ],
    },
}

STOCK_JSON_SCHEMA = {
    'name': 'staff_stock_insights',
    'strict': True,
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'headline': {'type': 'string'},
            'executive_summary': {'type': 'string'},
            'inventory_health': {'type': 'string'},
            'what_sells': _str_arr(
                {'title': {'type': 'string'}, 'detail': {'type': 'string'}},
                ['title', 'detail'],
            ),
            'what_lags': _str_arr(
                {'title': {'type': 'string'}, 'detail': {'type': 'string'}},
                ['title', 'detail'],
            ),
            'gaps': _str_arr(
                {
                    'area': {'type': 'string'},
                    'detail': {'type': 'string'},
                    'priority': {'type': 'string', 'enum': list(_PRIORITY)},
                },
                ['area', 'detail', 'priority'],
            ),
            'excess': _str_arr(
                {
                    'area': {'type': 'string'},
                    'detail': {'type': 'string'},
                    'action': {'type': 'string'},
                },
                ['area', 'detail', 'action'],
            ),
            'size_and_spec_alerts': _str_arr(
                {'alert': {'type': 'string'}, 'action': {'type': 'string'}},
                ['alert', 'action'],
            ),
            'buy_plan_thesis': {'type': 'string'},
            'buy_plan': _str_arr(
                {
                    'priority': {'type': 'string', 'enum': list(_PRIORITY)},
                    'category': {'type': 'string'},
                    'what': {'type': 'string'},
                    'qty_hint': {'type': 'string'},
                    'why': {'type': 'string'},
                },
                ['priority', 'category', 'what', 'qty_hint', 'why'],
            ),
            'do_not_buy': _str_arr(
                {'item': {'type': 'string'}, 'why': {'type': 'string'}},
                ['item', 'why'],
            ),
            'operations': _str_arr(
                {'action': {'type': 'string'}, 'how': {'type': 'string'}},
                ['action', 'how'],
            ),
            'data_quality': _str_arr(
                {'issue': {'type': 'string'}, 'fix': {'type': 'string'}},
                ['issue', 'fix'],
            ),
        },
        'required': [
            'headline',
            'executive_summary',
            'inventory_health',
            'what_sells',
            'what_lags',
            'gaps',
            'excess',
            'size_and_spec_alerts',
            'buy_plan_thesis',
            'buy_plan',
            'do_not_buy',
            'operations',
            'data_quality',
        ],
    },
}


ANALYTICS_SYSTEM_PROMPT = """You are the COO of Tenrivals, a curated tennis specialty retailer in Tbilisi, Georgia.

Write a board brief for the owner / executive team. Audience: operators who already see the charts. They need judgment, not a recap of every number.

VOICE
• Direct, specific, commercially literate. No hype, no “exciting opportunity” filler.
• English. Short sentences. Name the category, channel, weekday, or customer pattern when it matters.
• Never invent SKUs, quantities, prices, or suppliers that are not in the JSON.
• If coverage (landed-cost completeness) is weak, treat profit/ROI as directional and say so.
• Georgia tennis: clay is the default outdoor surface in Tbilisi; summer (esp. August) is peak outdoor play; juniors are a growth slice.

WHAT A GOOD BRIEF DOES
1. Headline — one sentence on the state of the P&L (growth vs profit quality vs cash).
2. Executive summary — 2–4 paragraphs: what happened this month vs last year same days, vs YTD, vs all-time run-rate. Call out whether we are ahead/behind on profit pace, AOV, mix (stock vs preorder), and cost coverage.
3. Scorecard narrative — interpret margin, ROI, TAX+acquiring, card share. Is the issue volume, mix, cost, or payment mix?
4. Period takeaways — one sharp takeaway per period present in the JSON (this_month, this_year, all_time, last-year comps).
5. What is working — 3–6 concrete strengths (category, weekday, channel, customers, products).
6. Attention — 3–8 issues with severity. Concentration (top customers), unknown COGS, weak categories, preorder leakage, low repeat rate, AOV drift, seasonal miss.
7. Recommendations — 4–8 actions. Each must say HOW and which internal tool: Buying module, Stock receive, Product create, Preorder catalog, Promo codes, Customer outreach, Sales orders, price edit. Horizon: this_week / this_month / 90_days. Prefer cash-aware moves (price, chase COGS, targeted reorder) over “buy more of everything”.
8. Risks — 2–5 commercial/operational risks + mitigation.
9. Data quality — missing landed costs, mis-tagged preorder vs stock, free-text legacy lines, etc.
10. KPIs to watch until next brief — 4–8 leading indicators with the current reading from the data.

Do not mention the source JSON. Do not tell them to “look at the dashboard”. They are already on it.
"""

STOCK_SYSTEM_PROMPT = """You are the COO of Tenrivals, a curated tennis specialty retailer in Tbilisi, Georgia.

Write a stock & replenishment board brief. Audience: owner who decides what to buy next month and what to stop sitting on.

VOICE
• Direct, specific, inventory-literate. No generic “optimize assortment”.
• English. Use weeks of cover, sell-through, hot size gaps, and capital tied up by brand/type.
• Never invent SKUs or qtys. If a matrix cell is empty or 🔴, that is a real gap only if sales also suggest demand — say when it is a gap vs when it is an unused size.
• Clay is the default Tbilisi outdoor surface; men’s/women’s hot US size bands matter more than fringe sizes.
• Working capital is scarce. A buy plan must include a do-not-buy list.
• Keep the brief tight so it can finish quickly: summary ≤3 short paragraphs; lists 3–6 items; buy_plan ≤8 rows; do_not_buy ≤5.

WHAT A GOOD BRIEF DOES
1. Headline — inventory health in one line (lean / bloated / gappy / balanced) plus cash implication.
2. Executive summary — 2–4 paragraphs: what is selling vs what we hold, stock vs preorder leakage, whether we are over-invested in slow categories.
3. Inventory health — interpret shelf value vs landed value, cover by category, dead stock share.
4. What sells / what lags — name types, brands, price bands, specific SKUs from the lists.
5. Gaps — P0/P1/P2. Hot shoe sizes 🔴 in selling ranges, racket grip×weight holes that match sales, missing categories with YTD demand and little stock.
6. Excess — high weeks-of-cover or 90d dead stock. Action: hold, discount, stop reorder, or transfer to preorder-only.
7. Size & spec alerts — shoes (US × surface) and rackets (grip × weight × price tier).
8. Buy plan thesis — one paragraph on next 30 days: seasonal timing, budget discipline, clay vs AC, juniors.
9. Buy plan — 4–8 lines, P0 first. Each line: category, what (model family / spec, not fake SKUs unless listed), qty hint (pairs/units/range), why (cover, gap, sales). Prefer completing size runs on winners over adding random new models.
10. Do not buy — 3–5 items/buckets to avoid this month.
11. Operations — receive stock, backfill landed costs, retag preorder vs stock, price markdowns, chase open preorders.
12. Data quality — SKUs without landed cost, unusable size JSON, free-text sales that hide true sell-through.

Tools you may recommend: Buying module, Stock receive, Product create/edit, Preorder catalog, Promo codes, Sales orders, Customer outreach.
"""


def _clean_str(v) -> str:
    if v is None:
        return ''
    return _scrub(str(v).strip())


def _scrub(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()


def _clean_list(items, fields: tuple[str, ...], *, enums: dict | None = None) -> list[dict]:
    out = []
    enums = enums or {}
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        row = {}
        empty = True
        for key in fields:
            val = _clean_str(item.get(key))
            if key in enums:
                allowed = enums[key]
                val = val if val in allowed else allowed[0]
            row[key] = val
            if val and key not in enums:
                empty = False
            elif val and key in enums and any(_clean_str(item.get(f)) for f in fields if f not in enums):
                empty = False
        if not empty:
            out.append(row)
    return out


def validate_analytics_report(data: object) -> dict:
    if not isinstance(data, dict):
        raise InsightError('AI response was not a JSON object.')
    report = {
        'headline': _clean_str(data.get('headline')),
        'executive_summary': _clean_str(data.get('executive_summary')),
        'scorecard_narrative': _clean_str(data.get('scorecard_narrative')),
        'period_takeaways': _clean_list(data.get('period_takeaways'), ('period', 'takeaway')),
        'what_is_working': _clean_list(data.get('what_is_working'), ('title', 'detail')),
        'attention': _clean_list(
            data.get('attention'),
            ('title', 'severity', 'detail', 'why_it_matters'),
            enums={'severity': _SEVERITY},
        ),
        'recommendations': _clean_list(
            data.get('recommendations'),
            ('action', 'horizon', 'how', 'tools', 'expected_impact'),
            enums={'horizon': _HORIZON},
        ),
        'risks': _clean_list(data.get('risks'), ('risk', 'mitigation')),
        'data_quality': _clean_list(data.get('data_quality'), ('issue', 'fix')),
        'kpis_to_watch': _clean_list(data.get('kpis_to_watch'), ('kpi', 'reading', 'watch_for')),
    }
    if not report['headline'] and not report['executive_summary']:
        raise InsightError('AI returned an empty brief.')
    return report


def validate_stock_report(data: object) -> dict:
    if not isinstance(data, dict):
        raise InsightError('AI response was not a JSON object.')
    report = {
        'headline': _clean_str(data.get('headline')),
        'executive_summary': _clean_str(data.get('executive_summary')),
        'inventory_health': _clean_str(data.get('inventory_health')),
        'what_sells': _clean_list(data.get('what_sells'), ('title', 'detail')),
        'what_lags': _clean_list(data.get('what_lags'), ('title', 'detail')),
        'gaps': _clean_list(
            data.get('gaps'),
            ('area', 'detail', 'priority'),
            enums={'priority': _PRIORITY},
        ),
        'excess': _clean_list(data.get('excess'), ('area', 'detail', 'action')),
        'size_and_spec_alerts': _clean_list(data.get('size_and_spec_alerts'), ('alert', 'action')),
        'buy_plan_thesis': _clean_str(data.get('buy_plan_thesis')),
        'buy_plan': _clean_list(
            data.get('buy_plan'),
            ('priority', 'category', 'what', 'qty_hint', 'why'),
            enums={'priority': _PRIORITY},
        ),
        'do_not_buy': _clean_list(data.get('do_not_buy'), ('item', 'why')),
        'operations': _clean_list(data.get('operations'), ('action', 'how')),
        'data_quality': _clean_list(data.get('data_quality'), ('issue', 'fix')),
    }
    if not report['headline'] and not report['executive_summary']:
        raise InsightError('AI returned an empty brief.')
    return report


def insight_kind_spec(kind: str, *, today: date | None = None) -> dict:
    """Prompt + live snapshot + output schema for one brief kind."""
    today = today or date.today()
    if kind == KIND_ANALYTICS:
        return {
            'kind': KIND_ANALYTICS,
            'title': 'Sales analytics board brief',
            'slug': 'analytics',
            'prompt_version': ANALYTICS_PROMPT_VERSION,
            'system_prompt': ANALYTICS_SYSTEM_PROMPT.strip(),
            'task': (
                'Generate the sales analytics board brief from this Tenrivals snapshot. '
                'Compare this month, YTD, all-time, and last-year same month when present.'
            ),
            'output_schema': ANALYTICS_JSON_SCHEMA,
            'data': build_analytics_insight_payload(today=today),
        }
    if kind == KIND_STOCK:
        return {
            'kind': KIND_STOCK,
            'title': 'Stock & replenishment board brief',
            'slug': 'stock',
            'prompt_version': STOCK_PROMPT_VERSION,
            'system_prompt': STOCK_SYSTEM_PROMPT.strip(),
            'task': (
                'Generate the stock & 30-day buy-plan board brief from this Tenrivals snapshot. '
                'Use on-hand stock, size/grip matrices, and sales velocity together.'
            ),
            'output_schema': STOCK_JSON_SCHEMA,
            'data': build_stock_insight_payload(today=today),
        }
    raise InsightError('Unknown insight type.')


def build_insight_export_markdown(kind: str, *, today: date | None = None) -> tuple[str, str]:
    """Return (filename, markdown body) for off-site AI analysis."""
    spec = insight_kind_spec(kind, today=today)
    as_of = timezone.localtime().strftime('%Y-%m-%d %H:%M %Z')
    data_json = json.dumps(spec['data'], ensure_ascii=False, indent=2, default=str)
    schema_json = json.dumps(spec['output_schema'], ensure_ascii=False, indent=2)
    body = (
        f"# Tenrivals — {spec['title']}\n\n"
        f"- Exported: {as_of}\n"
        f"- Kind: `{spec['kind']}`\n"
        f"- Prompt version: `{spec['prompt_version']}`\n"
        f"- Site: tenrivals.com (staff analytics / stock stats)\n\n"
        'Paste this entire file into an AI chat. Analysis is **not** run inside the Tenrivals admin.\n\n'
        '---\n\n'
        '## System prompt\n\n'
        f"{spec['system_prompt']}\n\n"
        '---\n\n'
        '## Task\n\n'
        f"{spec['task']}\n\n"
        'Return a single JSON object that matches the schema below. '
        'Do not invent SKUs, quantities, prices, or suppliers that are absent from the data snapshot.\n\n'
        '---\n\n'
        '## Expected output JSON schema\n\n'
        '```json\n'
        f'{schema_json}\n'
        '```\n\n'
        '---\n\n'
        '## Data snapshot\n\n'
        '```json\n'
        f'{data_json}\n'
        '```\n'
    )
    day = (today or date.today()).isoformat()
    filename = f"tenrivals-{spec['slug']}-ai-brief-{day}.md"
    return filename, body


@login_required
@user_passes_test(_staff_ok)
@require_http_methods(['GET'])
def staff_ai_insights(request, kind: str):
    """Download prompt + live snapshot as a Markdown file (no on-site LLM)."""
    try:
        filename, body = build_insight_export_markdown(kind)
    except InsightError:
        return HttpResponse('Unknown insight type.', status=404, content_type='text/plain; charset=utf-8')
    response = HttpResponse(body.encode('utf-8'), content_type='text/markdown; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

