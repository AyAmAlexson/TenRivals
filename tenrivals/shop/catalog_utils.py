"""Shared catalog queries for shop (stock/preorder lists and nav)."""

import re
from typing import NotRequired, TypedDict

from django.db import models
from django.db.models import Case, IntegerField, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce

from .models import Product, ProductListing, ProductListingChannel


class FeaturedStockBrand(TypedDict):
    """Home marquee / brands page: logo asset and exact Product.brand for ?cbrand=."""

    stock_brand: str
    logo: str
    label: str
    # Brands page: CSS transform scale inside the shared tile frame so marks look equally “heavy”.
    logo_optical_scale: NotRequired[float]


# Paths are under static/ (use with {% static %}).
# stock_brand must match Product.brand exactly for the stock catalog filter.
FEATURED_STOCK_BRANDS: list[FeaturedStockBrand] = [
    {
        'stock_brand': 'Toroline',
        'logo': 'assets/img/brand-logos/toroline.png',
        'label': 'Toroline',
        # Wide neon mark + solid fill reads heavy vs monochrome wordmarks
        'logo_optical_scale': 0.9,
    },
    {
        'stock_brand': 'HEAD',
        'logo': 'assets/img/brand-logos/head.svg',
        'label': 'HEAD',
        'logo_optical_scale': 1.06,
    },
    {
        'stock_brand': 'Yonex',
        'logo': 'assets/img/brand-logos/yonex.svg',
        'label': 'Yonex',
        'logo_optical_scale': 1.0,
    },
    {
        'stock_brand': 'Wilson',
        'logo': 'assets/img/brand-logos/wilson.svg',
        'label': 'Wilson',
        # Horizontal script SVG: height-limited row shrinks it — nudge up vs HEAD/Yonex
        'logo_optical_scale': 1.06,
    },
    {
        'stock_brand': 'Asics',
        'logo': 'assets/img/brand-logos/asics.svg',
        'label': 'Asics',
        'logo_optical_scale': 0.92,
    },
    {
        'stock_brand': 'Prince',
        'logo': 'assets/img/brand-logos/prince.svg',
        'label': 'Prince',
        # Tight wordmark viewBox — closer to neutral than old padded Illustrator export
        'logo_optical_scale': 1.02,
    },
    {
        'stock_brand': 'Tourna',
        'logo': 'assets/img/brand-logos/tourna.png',
        'label': 'Tourna',
        # Solid badge reads heavier than the line wordmarks beside it
        'logo_optical_scale': 0.92,
    },
]


def filter_products_by_listing_channel(qs, channel: str):
    """Restrict to products that have at least one ``ProductListing`` row for ``channel``.

    Quantity is not checked here. For the **In stock** storefront grid, use
    ``stock_catalog_storefront_queryset()`` or annotate ``stock_listing_qty`` then
    ``filter(Q(stock_listing_qty__gt=0) | Q(in_stock=True))`` so on-hand units and
    merchandised “In stock” vitrine rows (STOCK qty 0, checkbox on) both appear; use
    ``annotate_stock_shelf_priority`` + ``order_by('-_shelf_first', ...)`` so shelf qty > 0
    sort before vitrine.

    **Preorder** vitrine uses ``PREORDER`` with ``quantity == 0`` (separate tiles).
    """
    if ProductListing.objects.filter(channel=channel).exists():
        return qs.filter(listings__channel=channel).distinct()
    return qs


def order_products_by_effective_price(qs):
    return qs.annotate(sort_price=Coalesce('actual_price', 'initial_price')).order_by(
        '-sort_price', 'id'
    )


def annotate_stock_listing_quantity(qs):
    """Per-product quantity on the STOCK channel — physical units in Tbilisi (0 if no row)."""
    stock_sq = ProductListing.objects.filter(
        product_id=OuterRef('pk'),
        channel=ProductListingChannel.STOCK,
    ).values('quantity')[:1]
    return qs.annotate(
        stock_listing_qty=Coalesce(
            Subquery(stock_sq, output_field=models.PositiveIntegerField()),
            Value(0),
        )
    )


def annotate_preorder_listing_quantity(qs):
    """Per-product quantity on the PREORDER channel (0 if no row; 0 = vitrine / coming soon)."""
    preorder_sq = ProductListing.objects.filter(
        product_id=OuterRef('pk'),
        channel=ProductListingChannel.PREORDER,
    ).values('quantity')[:1]
    return qs.annotate(
        preorder_listing_qty=Coalesce(
            Subquery(preorder_sq, output_field=models.PositiveIntegerField()),
            Value(0),
        )
    )


def stock_catalog_base_queryset():
    return filter_products_by_listing_channel(
        Product.objects.filter(is_active=True),
        ProductListingChannel.STOCK,
    )


def stock_catalog_in_stock_queryset():
    """Strict shelf inventory: STOCK channel listing with quantity > 0 (orders, staff stats)."""
    return annotate_stock_listing_quantity(stock_catalog_base_queryset()).filter(
        stock_listing_qty__gt=0
    )


def stock_catalog_storefront_queryset():
    """In stock grid: on-hand (STOCK qty > 0) OR merchandised vitrine (``in_stock`` and STOCK qty 0)."""
    qs = annotate_stock_listing_quantity(stock_catalog_base_queryset())
    return qs.filter(Q(stock_listing_qty__gt=0) | Q(in_stock=True))


def annotate_stock_shelf_priority(qs):
    """1 = has on-hand STOCK qty; 0 = vitrine (qty 0). Order ``-_shelf_first`` to list shelf first."""
    return qs.annotate(
        _shelf_first=Case(
            When(stock_listing_qty__gt=0, then=1),
            default=0,
            output_field=IntegerField(),
        )
    )


def top_stock_brands_by_listing_quantity(limit: int = 7) -> list[str]:
    """Brands with the highest total STOCK listing quantity (only qty > 0)."""
    qs = stock_catalog_in_stock_queryset().exclude(brand__isnull=True).exclude(
        brand__exact=''
    )
    rows = (
        qs.values('brand')
        .annotate(total_qty=Sum('stock_listing_qty'))
        .order_by('-total_qty', 'brand')[:limit]
    )
    return [r['brand'] for r in rows]


def distinct_brands_for_type(stock_qs, type_code: str):
    return list(
        stock_qs.filter(type=type_code)
        .exclude(brand__isnull=True)
        .exclude(brand__exact='')
        .values_list('brand', flat=True)
        .distinct()
        .order_by('brand')
    )


def brand_logo_lookup() -> dict[str, FeaturedStockBrand]:
    """Case-insensitive map of Product.brand -> marquee logo row."""
    return {row['stock_brand'].casefold(): row for row in FEATURED_STOCK_BRANDS}


def order_brands_for_logo_strip(names: list[str]) -> list[str]:
    """Known logo brands first (marquee order), then the rest A–Z."""
    by_fold: dict[str, str] = {}
    for name in names:
        cleaned = (name or '').strip()
        if cleaned:
            by_fold.setdefault(cleaned.casefold(), cleaned)
    ordered: list[str] = []
    used: set[str] = set()
    for row in FEATURED_STOCK_BRANDS:
        key = row['stock_brand'].casefold()
        if key in by_fold and key not in used:
            ordered.append(by_fold[key])
            used.add(key)
    rest = sorted(
        (name for key, name in by_fold.items() if key not in used),
        key=str.casefold,
    )
    return ordered + rest


def distinct_brand_names(qs) -> list[str]:
    names = qs.exclude(brand__isnull=True).exclude(brand__exact='').values_list(
        'brand', flat=True
    )
    return order_brands_for_logo_strip(list(names.distinct()))


_SHOE_SIZE_NUMBER = re.compile(r'(\d+(?:[.,]\d+)?)')


def shoe_size_sort_key(label: str) -> tuple:
    match = _SHOE_SIZE_NUMBER.search(label or '')
    if match:
        return (0, float(match.group(1).replace(',', '.')), label.casefold())
    return (1, 0.0, (label or '').casefold())


def positive_shoe_size_labels(raw_sizes) -> list[str]:
    """In-stock size labels from a Shoe.sizes JSON list or {label: qty} dict."""
    found: set[str] = set()
    for raw in raw_sizes:
        if isinstance(raw, dict):
            for key, qty in raw.items():
                label = str(key).strip()
                if label and int(qty or 0) > 0:
                    found.add(label)
        elif isinstance(raw, list):
            for key in raw:
                label = str(key).strip()
                if label:
                    found.add(label)
    return sorted(found, key=shoe_size_sort_key)


