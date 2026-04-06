"""Shared catalog queries for shop (stock/preorder lists and nav)."""

from django.db import models
from django.db.models import OuterRef, Subquery, Value
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


def distinct_brands_for_type(stock_qs, type_code: str):
    return list(
        stock_qs.filter(type=type_code)
        .exclude(brand__isnull=True)
        .exclude(brand__exact='')
        .values_list('brand', flat=True)
        .distinct()
        .order_by('brand')
    )
