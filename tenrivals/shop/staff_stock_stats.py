"""
Staff-only analytics for the in-stock catalog (ProductListing STOCK + per-variant JSON).
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .catalog_utils import stock_catalog_in_stock_queryset
from .models import (
    Accessory,
    Apparel,
    Bag,
    Balls,
    CourtSurface,
    Gender,
    Product,
    ProductType,
    Racket,
    Shoe,
    String,
)
from .sales_order_stock import APPAREL_TYPES, SHOE_TYPES, _string_effective_variant_map
from .size_inventory import normalize_sizes_to_qty_map, us_shoe_size_labels

# Hot adult ranges: highlight 0 with 🔴 only in these US size windows.
MEN_HOT_MIN = Decimal('8.5')
MEN_HOT_MAX = Decimal('12')
WOMEN_HOT_MIN = Decimal('7.5')
WOMEN_HOT_MAX = Decimal('11')


def _unit_price_stock(p: Product) -> Decimal:
    """Lower of actual vs initial when both exist; else initial (or actual if only one)."""
    init = p.initial_price
    act = p.actual_price
    if act is not None:
        return min(act, init)
    return init


def _stock_qty(p: Product) -> int:
    return int(getattr(p, 'stock_listing_qty', 0) or 0)


def stock_products_queryset():
    """Active products in stock channel with on-hand qty > 0 (same as storefront)."""
    qs = stock_catalog_in_stock_queryset()
    return qs.select_related(
        'category',
        'shoe',
        'racket',
        'apparel',
        'string',
        'bag',
        'balls',
        'accessory',
    ).order_by('id')


def build_stock_stats() -> dict[str, Any]:
    products = list(stock_products_queryset())
    positions = len(products)

    total_value = Decimal('0')
    landed_value = Decimal('0')
    units_with_known_cost = 0
    for p in products:
        q = _stock_qty(p)
        total_value += _unit_price_stock(p) * q
        lc = p.landed_cost_gel
        if lc is not None and lc != 0:
            landed_value += lc * q
            units_with_known_cost += q

    # Product-type breakdown
    type_units_table: dict[str, int] = defaultdict(int)
    type_value_table: dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
    for p in products:
        name = p.get_type_display()
        q = _stock_qty(p)
        u = _unit_price_stock(p)
        type_units_table[name] += q
        type_value_table[name] += u * q

    total_units = sum(type_units_table.values())
    total_val = sum(type_value_table.values(), Decimal('0')) or Decimal('1')
    units_pct_base = total_units or 1
    coverage_pct = None
    if total_units:
        coverage_pct = round(100 * units_with_known_cost / total_units, 1)

    type_rows = []
    for name in sorted(type_units_table.keys(), key=lambda k: (-type_units_table[k], k)):
        u = type_units_table[name]
        v = type_value_table[name]
        type_rows.append(
            {
                'name': name,
                'units': u,
                'units_pct': round(100 * u / units_pct_base, 1),
                'value': v,
                'value_pct': round(float(100 * v / total_val), 1),
            }
        )

    shoe_cols = ['men_ac', 'men_clay', 'women_ac', 'women_clay']
    shoe_col_labels = ["Men's AC", "Men's Clay", "Women's AC", "Women's Clay"]
    us_labels = us_shoe_size_labels()
    shoe_matrix = {c: {lab: 0 for lab in us_labels} for c in shoe_cols}

    def _is_clay_surface(code: str) -> bool:
        return code == CourtSurface.CLAY

    def _is_ac_surface(code: str) -> bool:
        return code in (
            CourtSurface.ALL_COURT,
            CourtSurface.HARD,
            CourtSurface.GRASS,
            CourtSurface.PADEL,
        )

    for p in products:
        if p.type not in (ProductType.MENS_SHOES, ProductType.WOMENS_SHOES):
            continue
        try:
            shoe: Shoe = p.shoe
        except Shoe.DoesNotExist:
            continue
        qty_map = normalize_sizes_to_qty_map(shoe.sizes, fallback_total=_stock_qty(p))
        surf = shoe.surface or CourtSurface.ALL_COURT
        g = shoe.gender or Gender.UNISEX
        is_unisex = g == Gender.UNISEX

        men_ac = is_unisex or p.type == ProductType.MENS_SHOES
        men_clay = men_ac
        women_ac = is_unisex or p.type == ProductType.WOMENS_SHOES
        women_clay = women_ac

        for size_label, n in qty_map.items():
            n = int(n or 0)
            if n == 0:
                continue
            if size_label not in shoe_matrix['men_ac']:
                continue
            if _is_clay_surface(surf):
                if men_clay:
                    shoe_matrix['men_clay'][size_label] += n
                if women_clay:
                    shoe_matrix['women_clay'][size_label] += n
            elif _is_ac_surface(surf):
                if men_ac:
                    shoe_matrix['men_ac'][size_label] += n
                if women_ac:
                    shoe_matrix['women_ac'][size_label] += n
            else:
                # Unknown surface — count as AC for visibility
                if men_ac:
                    shoe_matrix['men_ac'][size_label] += n
                if women_ac:
                    shoe_matrix['women_ac'][size_label] += n

    def _hot_zero_for_col(col_key: str, us_num: Decimal) -> bool:
        if col_key.startswith('men_'):
            return MEN_HOT_MIN <= us_num <= MEN_HOT_MAX
        if col_key.startswith('women_'):
            return WOMEN_HOT_MIN <= us_num <= WOMEN_HOT_MAX
        return False

    shoe_rows_out = []
    for lab in us_labels:
        rest = lab.replace('US', '', 1).strip()
        try:
            us_num = Decimal(rest)
        except Exception:
            us_num = Decimal('0')
        cells = []
        for c in shoe_cols:
            v = shoe_matrix[c][lab]
            cells.append(
                {
                    'value': v,
                    'display': v if v > 0 else ('🔴' if _hot_zero_for_col(c, us_num) else '0'),
                }
            )
        shoe_rows_out.append({'label': lab, 'cells': cells})

    # Racket matrices: grip L1–L5 × weight buckets; split by unit price ≤600 vs >600
    grip_row_keys = [f'L{i}' for i in range(1, 6)]
    weight_cols = [
        ('le279', lambda w: w is None or w <= 279),
        ('280_299', lambda w: w is not None and 280 <= w <= 299),
        ('300', lambda w: w == 300),
        ('301_310', lambda w: w is not None and 301 <= w <= 310),
        ('311p', lambda w: w is not None and w >= 311),
    ]
    weight_col_labels = ['≤279g', '280–299g', '300g', '301–310g', '311g+']

    def _weight_bucket(w: int | None) -> str | None:
        for key, pred in weight_cols:
            if pred(w):
                return key
        return None

    racket_low = defaultdict(lambda: defaultdict(int))
    racket_high = defaultdict(lambda: defaultdict(int))

    for p in products:
        if p.type != ProductType.RACKET:
            continue
        try:
            rk: Racket = p.racket
        except Racket.DoesNotExist:
            continue
        unit = _unit_price_stock(p)
        tier = 'low' if unit <= Decimal('600') else 'high'
        target = racket_low if tier == 'low' else racket_high
        qty_map = normalize_sizes_to_qty_map(rk.grip_sizes, fallback_total=_stock_qty(p))
        w = rk.weight_grams
        b = _weight_bucket(w)
        if b is None:
            b = 'le279'
        for gk, n in qty_map.items():
            n = int(n or 0)
            if n == 0:
                continue
            gk = str(gk).strip().upper()
            if gk not in grip_row_keys:
                continue
            target[gk][b] += n

    def _racket_table(mat: dict) -> list[dict]:
        rows = []
        for gk in grip_row_keys:
            display_grip = gk.replace('L', '', 1)
            cells = []
            for key, _ in weight_cols:
                v = mat[gk][key]
                cells.append({'value': v, 'display': str(v) if v else '—'})
            rows.append({'grip': display_grip, 'cells': cells})
        return rows

    racket_table_low = _racket_table(racket_low)
    racket_table_high = _racket_table(racket_high)

    # Charts (JSON-serializable)
    brand_value: dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
    type_units: dict[str, int] = defaultdict(int)
    price_bucket_qty: dict[str, int] = defaultdict(int)
    created_month: dict[str, int] = defaultdict(int)

    def _price_bucket_label(price: Decimal) -> str:
        p = float(price)
        if p < 100:
            return '<100 ₾'
        if p < 200:
            return '100–199 ₾'
        if p < 400:
            return '200–399 ₾'
        if p < 600:
            return '400–599 ₾'
        if p < 900:
            return '600–899 ₾'
        return '900+ ₾'

    for p in products:
        q = _stock_qty(p)
        u = _unit_price_stock(p)
        bname = (p.brand or 'No brand').strip() or 'No brand'
        brand_value[bname] += u * q
        type_units[p.get_type_display()] += q
        price_bucket_qty[_price_bucket_label(u)] += q
        if p.created_at:
            key = p.created_at.strftime('%Y-%m')
            created_month[key] += q

    month_keys = sorted(created_month.keys())
    top_brands = sorted(brand_value.keys(), key=lambda k: float(brand_value[k]), reverse=True)[:12]
    if not top_brands:
        top_brands = ['—']
        brand_value['—'] = Decimal('0')
    charts = {
        'brandsTop': {
            'labels': top_brands,
            'values': [float(brand_value[k]) for k in top_brands],
        },
        'typesPie': {
            'labels': [k for k, _ in sorted(type_units.items(), key=lambda x: -x[1])],
            'values': [v for _, v in sorted(type_units.items(), key=lambda x: -x[1])],
        },
        'priceBuckets': {
            'labels': [
                '<100 ₾',
                '100–199 ₾',
                '200–399 ₾',
                '400–599 ₾',
                '600–899 ₾',
                '900+ ₾',
            ],
            'values': [
                price_bucket_qty.get('<100 ₾', 0),
                price_bucket_qty.get('100–199 ₾', 0),
                price_bucket_qty.get('200–399 ₾', 0),
                price_bucket_qty.get('400–599 ₾', 0),
                price_bucket_qty.get('600–899 ₾', 0),
                price_bucket_qty.get('900+ ₾', 0),
            ],
        },
        'arrivalMonths': {
            'labels': month_keys[-18:] or ['—'],
            'values': [created_month[k] for k in month_keys[-18:]] or [0],
        },
    }
    if not charts['typesPie']['labels']:
        charts['typesPie'] = {'labels': ['NO STOCK'], 'values': [0]}

    return {
        'positions': positions,
        'total_units': total_units,
        'total_value': total_value,
        'landed_value': landed_value,
        'units_with_known_cost': units_with_known_cost,
        'coverage_pct': coverage_pct,
        'type_rows': type_rows,
        'shoe_col_labels': shoe_col_labels,
        'shoe_rows': shoe_rows_out,
        'racket_weight_col_labels': weight_col_labels,
        'racket_table_low': racket_table_low,
        'racket_table_high': racket_table_high,
        'charts': charts,
        'chart_captions': [
            'Stock value (₾) by brand — where capital is tied up',
            'Units on hand by product type — category mix',
            'Units by retail price bucket — pricing / margin mix',
            'Units added to catalog by month (created_at) — intake rhythm',
        ],
    }


def _related(product: Product, attr: str, model):
    try:
        return getattr(product, attr)
    except model.DoesNotExist:
        return None


def _variant_qty_pairs(product: Product) -> list[tuple[str, int]]:
    """In-stock size/grip/gauge rows, or one unlabeled row for simple SKUs."""
    listing_total = _stock_qty(product)
    qty_map: dict[str, int] = {}
    if product.type == ProductType.RACKET:
        rk = _related(product, 'racket', Racket)
        if rk is not None:
            qty_map = normalize_sizes_to_qty_map(rk.grip_sizes, fallback_total=listing_total)
    elif product.type in SHOE_TYPES:
        sh = _related(product, 'shoe', Shoe)
        if sh is not None:
            qty_map = normalize_sizes_to_qty_map(sh.sizes, fallback_total=listing_total)
    elif product.type in APPAREL_TYPES:
        ap = _related(product, 'apparel', Apparel)
        if ap is not None:
            qty_map = normalize_sizes_to_qty_map(ap.sizes, fallback_total=listing_total)
    elif product.type == ProductType.STRINGS:
        st = _related(product, 'string', String)
        if st is not None:
            qty_map = _string_effective_variant_map(st, listing_total)
    rows = [(label, qty) for label, qty in sorted(qty_map.items()) if int(qty or 0) > 0]
    if rows:
        return rows
    if listing_total > 0:
        return [('', listing_total)]
    return []


def _choice_display(obj, field: str) -> str:
    fn = getattr(obj, f'get_{field}_display', None)
    if callable(fn):
        return str(fn() or '')
    return str(getattr(obj, field, '') or '')


def _type_spec_fields(product: Product) -> dict[str, Any]:
    specs = {
        'gender': '',
        'surface': '',
        'width': '',
        'material': '',
        'weight_grams': '',
        'head_size_sq_in': '',
        'length_in': '',
        'balance_mm': '',
        'swingweight': '',
        'string_pattern': '',
        'is_strung': '',
        'gauge_mm': '',
        'length_m': '',
        'capacity_rackets': '',
        'balls_per_can': '',
        'specs': '',
    }
    rk = _related(product, 'racket', Racket)
    sh = _related(product, 'shoe', Shoe)
    ap = _related(product, 'apparel', Apparel)
    st = _related(product, 'string', String)
    bg = _related(product, 'bag', Bag)
    bl = _related(product, 'balls', Balls)
    ac = _related(product, 'accessory', Accessory)
    sub = rk or sh or ap or st or bg or bl or ac
    if sub is not None:
        fn = getattr(sub, 'invoice_specs_slash', None)
        if callable(fn):
            specs['specs'] = fn()
    if rk is not None:
        specs.update(
            {
                'weight_grams': rk.weight_grams or '',
                'head_size_sq_in': rk.head_size_sq_in or '',
                'length_in': rk.length_in if rk.length_in is not None else '',
                'balance_mm': rk.balance_mm or '',
                'swingweight': rk.swingweight or '',
                'string_pattern': (rk.string_pattern or '').strip(),
                'is_strung': 'yes' if rk.is_strung else 'no',
            }
        )
    if sh is not None:
        specs.update(
            {
                'gender': _choice_display(sh, 'gender'),
                'surface': _choice_display(sh, 'surface'),
                'width': (sh.width or '').strip(),
            }
        )
    if ap is not None:
        specs.update(
            {
                'gender': _choice_display(ap, 'gender'),
                'material': (ap.material or '').strip(),
            }
        )
    if st is not None:
        specs.update(
            {
                'gauge_mm': st.gauge_mm if st.gauge_mm is not None else '',
                'material': (st.material or '').strip(),
                'length_m': st.length_m or '',
            }
        )
    if bg is not None:
        specs['capacity_rackets'] = bg.capacity_rackets or ''
    if bl is not None:
        specs.update(
            {
                'balls_per_can': bl.balls_per_can or '',
                'surface': _choice_display(bl, 'surface'),
            }
        )
    return specs


def _csv_cell(value) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    if isinstance(value, Decimal):
        return format(value, 'f')
    return value


def _dt(value) -> str:
    if not value:
        return ''
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.strftime('%Y-%m-%d %H:%M:%S')


STOCK_CSV_COLUMNS = (
    ('category', 'category'),
    ('brand', 'brand'),
    ('name', 'name'),
    ('variant', 'variant'),
    ('color', 'color'),
    ('specs', 'specs'),
    ('gender', 'gender'),
    ('surface', 'surface'),
    ('width', 'width'),
    ('material', 'material'),
    ('weight_grams', 'weight_grams'),
    ('head_size_sq_in', 'head_size_sq_in'),
    ('length_in', 'length_in'),
    ('balance_mm', 'balance_mm'),
    ('swingweight', 'swingweight'),
    ('string_pattern', 'string_pattern'),
    ('is_strung', 'is_strung'),
    ('gauge_mm', 'gauge_mm'),
    ('length_m', 'length_m'),
    ('capacity_rackets', 'capacity_rackets'),
    ('balls_per_can', 'balls_per_can'),
    ('qty', 'qty'),
    ('landed_cost', 'landed_cost'),
    ('shelf_price', 'shelf_price'),
    ('discounted_price', 'discounted_price'),
    ('sku', 'sku'),
    ('catalog_category', 'catalog_category'),
    ('short_description', 'short_description'),
    ('description', 'description'),
    ('attributes', 'attributes'),
    ('featured', 'featured'),
    ('is_active', 'is_active'),
    ('in_stock', 'in_stock'),
    ('product_id', 'product_id'),
    ('created_at', 'created_at'),
    ('updated_at', 'updated_at'),
)


def build_stock_csv(*, today: date | None = None) -> tuple[str, str]:
    """One CSV row per in-stock variant (size / grip / gauge) or SKU."""
    today = today or date.today()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([header for _, header in STOCK_CSV_COLUMNS])
    products = list(stock_products_queryset())
    rows = []
    for product in products:
        type_fields = _type_spec_fields(product)
        attrs = product.attributes if isinstance(product.attributes, dict) else {}
        attr_json = json.dumps(attrs, ensure_ascii=False) if attrs else ''
        catalog = ''
        if product.category_id:
            catalog = (product.category.name or '').strip()
        base = {
            'category': product.get_type_display(),
            'brand': (product.brand or '').strip(),
            'name': (product.name or '').strip(),
            'color': (product.color or '').strip(),
            'landed_cost': product.landed_cost_gel,
            'shelf_price': product.initial_price,
            'discounted_price': product.actual_price,
            'sku': (product.sku or '').strip(),
            'catalog_category': catalog,
            'short_description': (product.short_description or '').strip(),
            'description': (product.description or '').strip(),
            'attributes': attr_json,
            'featured': product.featured_product,
            'is_active': product.is_active,
            'in_stock': product.in_stock,
            'product_id': product.pk,
            'created_at': _dt(product.created_at),
            'updated_at': _dt(product.updated_at),
            **type_fields,
        }
        for variant, qty in _variant_qty_pairs(product):
            row = dict(base)
            row['variant'] = variant
            row['qty'] = qty
            rows.append(row)
    rows.sort(
        key=lambda r: (
            str(r.get('category') or ''),
            str(r.get('brand') or '').lower(),
            str(r.get('name') or '').lower(),
            str(r.get('variant') or ''),
        )
    )
    for row in rows:
        writer.writerow([_csv_cell(row.get(key)) for key, _ in STOCK_CSV_COLUMNS])
    filename = f'tenrivals-stock-{today.isoformat()}.csv'
    return filename, buf.getvalue()


def _staff_ok(user):
    return bool(user.is_authenticated and user.is_superuser)


@login_required
@user_passes_test(_staff_ok)
@require_http_methods(['GET'])
def staff_stock_csv(request):
    """Download every in-stock SKU/variant as CSV."""
    filename, body = build_stock_csv()
    response = HttpResponse('\ufeff' + body, content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
