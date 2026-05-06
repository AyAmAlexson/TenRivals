"""SEO helpers: readable catalog URLs, page titles, meta descriptions."""

from __future__ import annotations

from decimal import Decimal
from urllib.parse import quote, urlencode

from django.utils.text import slugify

from .site_locale import shop_reverse

from .models import CourtSurface, Gender, ProductType

# URL segment → internal type filter (matches ?type= for stock/preorder)
TYPE_SLUG_TO_CODE: dict[str, str] = {
    'rackets': ProductType.RACKET,
    'mens-shoes': ProductType.MENS_SHOES,
    'womens-shoes': ProductType.WOMENS_SHOES,
    'junior-shoes': ProductType.JUNIOR_SHOES,
    'mens-apparel': ProductType.MENS_APPAREL,
    'womens-apparel': ProductType.WOMENS_APPAREL,
    'junior-apparel': ProductType.JUNIOR_APPAREL,
    'balls': ProductType.BALLS,
    'strings': ProductType.STRINGS,
    'bags': ProductType.BAGS,
    'grips': ProductType.GRIPS,
    'dampeners': ProductType.DAMPENERS,
    'accessories': ProductType.ACCESSORIES,
    'accessories-equipment': 'ACC_GEAR',
}

CODE_TO_TYPE_SLUG: dict[str, str] = {v: k for k, v in TYPE_SLUG_TO_CODE.items()}

SHOE_TYPES = frozenset(
    {
        ProductType.MENS_SHOES,
        ProductType.WOMENS_SHOES,
        ProductType.JUNIOR_SHOES,
    }
)

# Third URL segment → ?surf= value
SURFACE_SLUG_TO_SURF: dict[str, str] = {
    'clay': 'clay',
    'hard': 'hard',
    'all-court': 'allcourt',
    'grass': 'grass',
    'padel': 'padel',
}


def surf_query_to_surface_slug(surf: str) -> str | None:
    for slug, val in SURFACE_SLUG_TO_SURF.items():
        if val == surf:
            return slug
    return None

SITE_NAME = 'Tenrivals'
CITY_COUNTRY = 'Tbilisi, Georgia'


def type_slug_to_code(slug: str | None) -> str | None:
    if not slug:
        return None
    return TYPE_SLUG_TO_CODE.get((slug or '').strip().lower())


def type_code_to_slug(code: str | None) -> str | None:
    if not code:
        return None
    return CODE_TO_TYPE_SLUG.get(code)


def brand_to_slug(brand: str) -> str:
    return slugify(brand) or 'brand'


def early_brand_lists_for_catalog(browse_mode: str, type_code: str | None) -> tuple[list[str], list[str]]:
    """Shoe brands list, racket brands list (one may be empty) for path slug matching."""
    from .catalog_utils import distinct_brands_for_type, stock_catalog_storefront_queryset
    from .models import Product

    if not type_code:
        return [], []
    if type_code in SHOE_TYPES:
        if browse_mode == 'stock':
            shoe_brands = distinct_brands_for_type(
                stock_catalog_storefront_queryset(), type_code
            )
        else:
            shoe_brands = list(
                Product.objects.filter(is_active=True, type=type_code)
                .exclude(brand__isnull=True)
                .exclude(brand__exact='')
                .values_list('brand', flat=True)
                .distinct()
                .order_by('brand')
            )
        return list(shoe_brands), []
    if type_code == ProductType.RACKET:
        if browse_mode == 'stock':
            racket_brands = distinct_brands_for_type(
                stock_catalog_storefront_queryset(), ProductType.RACKET
            )
        else:
            racket_brands = list(
                Product.objects.filter(is_active=True, type=ProductType.RACKET)
                .exclude(brand__isnull=True)
                .exclude(brand__exact='')
                .values_list('brand', flat=True)
                .distinct()
                .order_by('brand')
            )
        return [], list(racket_brands)
    return [], []


def match_brand_from_slug(slug: str | None, candidates: list[str]) -> str | None:
    if not slug or not candidates:
        return None
    want = (slug or '').lower().replace('-', '')
    for name in candidates:
        if not name:
            continue
        n = slugify(name).lower().replace('-', '')
        if n == want or slugify(name).lower() == slug.lower():
            return name
    return None


def _type_label(code: str | None) -> str:
    if not code:
        return 'tennis equipment'
    labels = dict(ProductType.choices)
    if code == 'ACC_GEAR':
        return 'tennis accessories & equipment'
    return (labels.get(code) or code).lower()


def _surface_label(surf: str) -> str:
    m = {
        'clay': 'clay court',
        'hard': 'hard court',
        'allcourt': 'all-court',
        'grass': 'grass',
        'padel': 'padel',
    }
    return m.get(surf, surf)


def build_stock_catalog_path(
    type_slug: str | None,
    brand_slug: str | None = None,
    surface_slug: str | None = None,
    *,
    site_locale: str | None = None,
) -> str:
    if not type_slug:
        return shop_reverse('shop:stock', site_locale=site_locale)
    tc = type_slug_to_code(type_slug)
    if not tc:
        return shop_reverse('shop:stock', site_locale=site_locale)
    if tc in SHOE_TYPES and brand_slug and surface_slug:
        return shop_reverse(
            'shop:stock_catalog_shoe',
            site_locale=site_locale,
            type_slug=type_slug,
            brand_slug=brand_slug,
            surface_slug=surface_slug,
        )
    if brand_slug:
        return shop_reverse(
            'shop:stock_catalog_brand',
            site_locale=site_locale,
            type_slug=type_slug,
            brand_slug=brand_slug,
        )
    return shop_reverse(
        'shop:stock_catalog_type',
        site_locale=site_locale,
        type_slug=type_slug,
    )


def build_preorder_catalog_path(
    type_slug: str | None,
    brand_slug: str | None = None,
    surface_slug: str | None = None,
    *,
    site_locale: str | None = None,
) -> str:
    if not type_slug:
        return shop_reverse('shop:preorder', site_locale=site_locale)
    tc = type_slug_to_code(type_slug)
    if not tc:
        return shop_reverse('shop:preorder', site_locale=site_locale)
    if tc in SHOE_TYPES and brand_slug and surface_slug:
        return shop_reverse(
            'shop:preorder_catalog_shoe',
            site_locale=site_locale,
            type_slug=type_slug,
            brand_slug=brand_slug,
            surface_slug=surface_slug,
        )
    if brand_slug:
        return shop_reverse(
            'shop:preorder_catalog_brand',
            site_locale=site_locale,
            type_slug=type_slug,
            brand_slug=brand_slug,
        )
    return shop_reverse(
        'shop:preorder_catalog_type',
        site_locale=site_locale,
        type_slug=type_slug,
    )


def catalog_canonical_path(
    browse_mode: str,
    *,
    type_slug: str | None,
    brand_slug: str | None,
    surface_slug: str | None,
    site_locale: str | None = None,
) -> str:
    if browse_mode == 'preorder':
        return build_preorder_catalog_path(
            type_slug, brand_slug, surface_slug, site_locale=site_locale
        )
    return build_stock_catalog_path(
        type_slug, brand_slug, surface_slug, site_locale=site_locale
    )


def catalog_seo_texts(
    browse_mode: str,
    *,
    type_code: str | None,
    racket_brand: str,
    shoe_brand: str,
    surface_active: str,
    gender_filter: str,
    product_count: int,
) -> dict[str, str]:
    """Unique title, H1, meta description for catalog listing."""
    mode_phrase = (
        'Preorder from the EU & USA to Georgia'
        if browse_mode == 'preorder'
        else 'Buy in Tbilisi with free city delivery'
    )
    mode_short = 'Preorder from EU/USA' if browse_mode == 'preorder' else 'In stock in Tbilisi'

    type_label = _type_label(type_code)
    brand_parts: list[str] = []
    if type_code == ProductType.RACKET and racket_brand and racket_brand != 'all':
        brand_parts.append(racket_brand)
    if type_code in SHOE_TYPES and shoe_brand and shoe_brand != 'all':
        brand_parts.append(shoe_brand)
    brand_txt = ', '.join(brand_parts) if brand_parts else ''

    surf_txt = ''
    if type_code in SHOE_TYPES and surface_active and surface_active != 'all':
        surf_txt = _surface_label(surface_active)

    gender_txt = ''
    if type_code in SHOE_TYPES and gender_filter == 'm':
        gender_txt = "men's "
    elif type_code in SHOE_TYPES and gender_filter == 'w':
        gender_txt = "women's "

    head_subject = f'{gender_txt}{type_label}'.strip()
    if brand_txt:
        head_subject = f'{head_subject} · {brand_txt}'
    if surf_txt:
        head_subject = f'{head_subject} · {surf_txt}'

    title = f'{head_subject} | {SITE_NAME} — {mode_short}'
    if len(title) > 72:
        title = f'{head_subject} | {SITE_NAME}'

    h1 = head_subject or 'Tennis equipment'
    if browse_mode == 'stock':
        h1 = f'{h1} · shop in {CITY_COUNTRY}'
    else:
        h1 = f'{h1} · preorder to {CITY_COUNTRY}'

    desc_bits = [
        f'{SITE_NAME} online tennis store in {CITY_COUNTRY}.',
        mode_phrase + '.',
    ]
    if brand_txt:
        desc_bits.append(f'Brands: {brand_txt}.')
    if surf_txt:
        desc_bits.append(f'Surface: {surf_txt}.')
    desc_bits.append(
        'Wilson, HEAD, Babolat, Prince & more — rackets, shoes, strings, and apparel.'
        if browse_mode == 'stock'
        else 'Rackets, shoes, strings, bags — indicative prices until you confirm your order.'
    )
    desc = ' '.join(desc_bits)
    if len(desc) > 320:
        desc = desc[:317] + '…'

    return {
        'seo_page_title': title,
        'seo_page_h1': h1,
        'seo_meta_description': desc,
    }


def append_query(url: str, params: dict) -> str:
    if not params:
        return url
    q = urlencode({k: v for k, v in params.items() if v is not None and v != ''})
    return f'{url}?{q}' if q else url


def append_cbrand_query(url: str, catalog_brand: str | None) -> str:
    cb = (catalog_brand or '').strip()
    if not cb:
        return url
    b = quote(cb, safe='')
    join = '&' if '?' in url else '?'
    return f'{url}{join}cbrand={b}'


def shoe_surface_tab_href(
    browse_mode: str,
    *,
    type_slug: str | None,
    type_code: str | None,
    shoe_brand: str,
    surface: str,
    catalog_brand: str,
    site_locale: str | None = None,
) -> str:
    """Relative URL for a shoe surface tab (SEO path when type + brand + surface allow)."""
    shoe_brand = shoe_brand or 'all'
    surface = surface or 'all'
    if not type_code or type_code not in SHOE_TYPES:
        base = shop_reverse(
            'shop:preorder' if browse_mode == 'preorder' else 'shop:stock',
            site_locale=site_locale,
        )
        params: dict[str, str] = {}
        if type_code:
            params['type'] = type_code
        if surface != 'all':
            params['surf'] = surface
        if shoe_brand != 'all':
            params['sbrand'] = shoe_brand
        return append_cbrand_query(append_query(base, params), catalog_brand)

    if not type_slug:
        base = shop_reverse(
            'shop:preorder' if browse_mode == 'preorder' else 'shop:stock',
            site_locale=site_locale,
        )
        params = {'type': type_code}
        if surface != 'all':
            params['surf'] = surface
        if shoe_brand != 'all':
            params['sbrand'] = shoe_brand
        return append_cbrand_query(append_query(base, params), catalog_brand)

    stock = browse_mode == 'stock'
    ts = type_slug
    sb = shoe_brand if shoe_brand != 'all' else None
    path_surface = surf_query_to_surface_slug(surface) if surface != 'all' else None

    if stock:
        if sb and path_surface:
            u = shop_reverse(
                'shop:stock_catalog_shoe',
                site_locale=site_locale,
                type_slug=ts,
                brand_slug=brand_to_slug(sb),
                surface_slug=path_surface,
            )
        elif sb:
            u = shop_reverse(
                'shop:stock_catalog_brand',
                site_locale=site_locale,
                type_slug=ts,
                brand_slug=brand_to_slug(sb),
            )
            if surface != 'all':
                u = append_query(u, {'surf': surface})
        else:
            u = shop_reverse(
                'shop:stock_catalog_type', site_locale=site_locale, type_slug=ts
            )
            extra: dict[str, str] = {}
            if surface != 'all':
                extra['surf'] = surface
            if shoe_brand != 'all':
                extra['sbrand'] = shoe_brand
            u = append_query(u, extra)
    else:
        if sb and path_surface:
            u = shop_reverse(
                'shop:preorder_catalog_shoe',
                site_locale=site_locale,
                type_slug=ts,
                brand_slug=brand_to_slug(sb),
                surface_slug=path_surface,
            )
        elif sb:
            u = shop_reverse(
                'shop:preorder_catalog_brand',
                site_locale=site_locale,
                type_slug=ts,
                brand_slug=brand_to_slug(sb),
            )
            if surface != 'all':
                u = append_query(u, {'surf': surface})
        else:
            u = shop_reverse(
                'shop:preorder_catalog_type',
                site_locale=site_locale,
                type_slug=ts,
            )
            extra = {}
            if surface != 'all':
                extra['surf'] = surface
            if shoe_brand != 'all':
                extra['sbrand'] = shoe_brand
            u = append_query(u, extra)
    return append_cbrand_query(u, catalog_brand)


def shoe_brand_tab_href(
    browse_mode: str,
    *,
    type_slug: str | None,
    type_code: str | None,
    shoe_brand: str,
    surface: str,
    catalog_brand: str,
    site_locale: str | None = None,
) -> str:
    """Relative URL for a shoe brand tab."""
    shoe_brand = shoe_brand or 'all'
    surface = surface or 'all'
    if not type_code or type_code not in SHOE_TYPES:
        base = shop_reverse(
            'shop:preorder' if browse_mode == 'preorder' else 'shop:stock',
            site_locale=site_locale,
        )
        params: dict[str, str] = {}
        if type_code:
            params['type'] = type_code
        if surface != 'all':
            params['surf'] = surface
        if shoe_brand != 'all':
            params['sbrand'] = shoe_brand
        return append_cbrand_query(append_query(base, params), catalog_brand)

    if not type_slug:
        base = shop_reverse(
            'shop:preorder' if browse_mode == 'preorder' else 'shop:stock',
            site_locale=site_locale,
        )
        params = {'type': type_code, 'surf': surface}
        if shoe_brand != 'all':
            params['sbrand'] = shoe_brand
        return append_cbrand_query(append_query(base, params), catalog_brand)

    stock = browse_mode == 'stock'
    ts = type_slug
    sb = shoe_brand if shoe_brand != 'all' else None
    path_surface = surf_query_to_surface_slug(surface) if surface != 'all' else None

    if stock:
        if sb and path_surface:
            u = shop_reverse(
                'shop:stock_catalog_shoe',
                site_locale=site_locale,
                type_slug=ts,
                brand_slug=brand_to_slug(sb),
                surface_slug=path_surface,
            )
        elif sb:
            u = shop_reverse(
                'shop:stock_catalog_brand',
                site_locale=site_locale,
                type_slug=ts,
                brand_slug=brand_to_slug(sb),
            )
            if surface != 'all':
                u = append_query(u, {'surf': surface})
        else:
            u = shop_reverse(
                'shop:stock_catalog_type', site_locale=site_locale, type_slug=ts
            )
            if surface != 'all':
                u = append_query(u, {'surf': surface})
    else:
        if sb and path_surface:
            u = shop_reverse(
                'shop:preorder_catalog_shoe',
                site_locale=site_locale,
                type_slug=ts,
                brand_slug=brand_to_slug(sb),
                surface_slug=path_surface,
            )
        elif sb:
            u = shop_reverse(
                'shop:preorder_catalog_brand',
                site_locale=site_locale,
                type_slug=ts,
                brand_slug=brand_to_slug(sb),
            )
            if surface != 'all':
                u = append_query(u, {'surf': surface})
        else:
            u = shop_reverse(
                'shop:preorder_catalog_type',
                site_locale=site_locale,
                type_slug=ts,
            )
            if surface != 'all':
                u = append_query(u, {'surf': surface})
    return append_cbrand_query(u, catalog_brand)


def pdp_seo_title(product, *, pdp_mode: str, stock_qty: int) -> str:
    brand = (product.brand or '').strip()
    name = (product.name or '').strip()
    kind = product.get_type_display()
    if pdp_mode == 'stock' and stock_qty > 0:
        tail = f'buy in Tbilisi, Georgia | {SITE_NAME}'
    elif pdp_mode == 'stock':
        tail = f'order in Tbilisi | {SITE_NAME}'
    else:
        tail = f'preorder from EU to Georgia | {SITE_NAME}'
    core = f'{kind} {brand} {name}'.strip()
    return f'{core} — {tail}'[:70]


def pdp_meta_description(product, *, pdp_mode: str, stock_qty: int) -> str:
    brand = (product.brand or '').strip()
    bits = [
        f'{SITE_NAME} — tennis shop in Tbilisi, Georgia.',
        f'{product.get_type_display()} {brand} {product.name}.'.strip(),
    ]
    if product.short_description:
        bits.append(str(product.short_description)[:120])
    if pdp_mode == 'stock' and stock_qty > 0:
        bits.append('In stock with free delivery in Tbilisi; shipping across Georgia.')
    elif pdp_mode == 'stock':
        bits.append('Contact us for availability, sizes, and pickup in Tbilisi.')
    else:
        bits.append('Preorder from Europe/USA with official customs clearance into Georgia.')
    return ' '.join(bits)[:320]


def pdp_product_json_ld(
    request,
    product,
    *,
    pdp_mode: str,
    stock_qty: int,
    price_value: Decimal | None,
) -> dict:
    """Schema.org Product (dict for json_script or |safe JSON)."""
    images = []
    for n in ('image_1', 'image_2', 'image_3', 'image_4', 'image_5'):
        f = getattr(product, n, None)
        if f:
            try:
                images.append(request.build_absolute_uri(f.url))
            except Exception:
                pass
    avail = (
        'https://schema.org/InStock'
        if pdp_mode == 'stock' and stock_qty > 0
        else 'https://schema.org/OutOfStock'
    )
    price = None
    if price_value is not None:
        try:
            price = str(price_value.quantize(Decimal('0.01')))
        except Exception:
            price = str(price_value)
    brand = (product.brand or '').strip()
    data: dict = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        'name': product.name,
        'description': (product.short_description or product.description or '')[:5000],
        'sku': product.sku or str(product.pk),
        'brand': {'@type': 'Brand', 'name': brand} if brand else None,
        'image': images[:8] if images else None,
        'offers': {
            '@type': 'Offer',
            'url': request.build_absolute_uri(
                shop_reverse(
                    'shop:product_detail',
                    product.pk,
                    site_locale=getattr(request, 'site_locale', None),
                )
            ),
            'priceCurrency': 'GEL',
            'availability': avail,
            'seller': {'@type': 'Organization', 'name': SITE_NAME},
        },
    }
    if price:
        data['offers']['price'] = price
    data = {k: v for k, v in data.items() if v is not None}
    if data.get('offers'):
        data['offers'] = {k: v for k, v in data['offers'].items() if v is not None}
    return data


def site_organization_json_ld(request) -> dict:
    from django.conf import settings

    configured_root = (getattr(settings, 'WEBSITE_URL', '') or '').rstrip('/')
    store_path = shop_reverse('shop:index', site_locale=getattr(request, 'site_locale', None))
    if configured_root:
        store_url = f'{configured_root}{store_path}'
    else:
        store_url = request.build_absolute_uri(store_path)

    return {
        '@context': 'https://schema.org',
        '@type': 'Store',
        'name': f'{SITE_NAME} Tennis Shop',
        'description': f'Online tennis equipment store in Tbilisi, Georgia. In-stock catalog, preorder from EU/USA, free delivery in Tbilisi.',
        'url': store_url,
        'telephone': '+995591288967',
        'email': 'andy.rivals@tenrivals.com',
        'areaServed': {'@type': 'City', 'name': 'Tbilisi', 'containedInPlace': {'@type': 'Country', 'name': 'Georgia'}},
        'sameAs': [
            'https://www.instagram.com/tennis_rivals_shop/',
            'https://t.me/tennis_rivals_shop_tbilisi',
            'https://wa.me/995591288967',
        ],
    }
