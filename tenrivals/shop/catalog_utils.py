"""Shared catalog queries for shop (stock/preorder lists and nav)."""

from django.db import models
from django.db.models import OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .models import Product, ProductListing, ProductListingChannel


def filter_products_by_listing_channel(qs, channel: str):
    """Once any listing rows exist for a channel, public lists only show those products."""
    if ProductListing.objects.filter(channel=channel).exists():
        return qs.filter(listings__channel=channel).distinct()
    return qs


def order_products_by_effective_price(qs):
    return qs.annotate(sort_price=Coalesce('actual_price', 'initial_price')).order_by(
        'sort_price', 'id'
    )


def annotate_stock_listing_quantity(qs):
    """Per-product quantity on STOCK channel (0 if no row)."""
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


def stock_catalog_base_queryset():
    return filter_products_by_listing_channel(
        Product.objects.filter(is_active=True),
        ProductListingChannel.STOCK,
    )


def top_stock_brands_by_listing_quantity(limit: int = 7) -> list[str]:
    """Brands with the highest total STOCK listing quantity (only qty > 0)."""
    qs = annotate_stock_listing_quantity(stock_catalog_base_queryset())
    qs = qs.exclude(brand__isnull=True).exclude(brand__exact='').filter(
        stock_listing_qty__gt=0
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


