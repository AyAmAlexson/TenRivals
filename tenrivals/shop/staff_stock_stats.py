"""
Staff-only analytics for the in-stock catalog (ProductListing STOCK + per-variant JSON).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from .catalog_utils import annotate_stock_listing_quantity, stock_catalog_base_queryset
from .models import CourtSurface, Gender, Product, ProductListingChannel, ProductType, Racket, Shoe
from .size_inventory import normalize_sizes_to_qty_map, us_shoe_size_labels

# Adult shoe sizes: show 🔴 for zero stock (attention). Below = show plain 0.
ADULT_US_SIZE_MIN = Decimal('5')


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
    """Active products in stock channel with listing qty annotation."""
    qs = stock_catalog_base_queryset()
    qs = annotate_stock_listing_quantity(qs)
    qs = qs.filter(stock_listing_qty__gt=0)
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
    for p in products:
        q = _stock_qty(p)
        total_value += _unit_price_stock(p) * q

    # Category breakdown (by Product.category)
    cat_units: dict[str, int] = defaultdict(int)
    cat_value: dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
    for p in products:
        name = p.category.name if p.category else 'Uncategorized'
        q = _stock_qty(p)
        u = _unit_price_stock(p)
        cat_units[name] += q
        cat_value[name] += u * q

    total_units = sum(cat_units.values()) or 1
    total_val = sum(cat_value.values(), Decimal('0')) or Decimal('1')

    category_rows = []
    for name in sorted(cat_units.keys(), key=lambda k: (-cat_units[k], k)):
        u = cat_units[name]
        v = cat_value[name]
        category_rows.append(
            {
                'name': name,
                'units': u,
                'units_pct': round(100 * u / total_units, 1),
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

    shoe_rows_out = []
    for lab in us_labels:
        rest = lab.replace('US', '', 1).strip()
        try:
            us_num = Decimal(rest)
        except Exception:
            us_num = Decimal('0')
        is_adult_row = us_num >= ADULT_US_SIZE_MIN
        cells = []
        for c in shoe_cols:
            v = shoe_matrix[c][lab]
            cells.append(
                {
                    'value': v,
                    'display': v if v > 0 else ('🔴' if is_adult_row else '0'),
                }
            )
        shoe_rows_out.append({'label': lab, 'cells': cells, 'is_adult': is_adult_row})

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
        'adult_us_size_min': ADULT_US_SIZE_MIN,
        'category_rows': category_rows,
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
