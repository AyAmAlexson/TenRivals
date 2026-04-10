"""Helpers for staff retail sales orders (VAT 18% inclusive, invoice numbers, stock)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction

from .models import (
    Product,
    ProductListing,
    ProductListingChannel,
    SalesInvoiceYearSequence,
)


VAT_GROSS_DIVISOR = Decimal('1.18')


def gross_split_vat_net(gross: Decimal) -> tuple[Decimal, Decimal]:
    """Gross is VAT-inclusive at 18%. Returns (net, vat_amount)."""
    g = gross.quantize(Decimal('0.01'))
    net = (g / VAT_GROSS_DIVISOR).quantize(Decimal('0.01'))
    vat = (g - net).quantize(Decimal('0.01'))
    return net, vat


def line_amounts(
    quantity: int,
    unit_price_gross: Decimal,
    discount_percent: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """Returns (line_gross, line_vat, line_net)."""
    base = Decimal(quantity) * unit_price_gross
    disc = max(Decimal('0'), min(Decimal('100'), discount_percent))
    line_gross = (base * (Decimal('1') - disc / Decimal('100'))).quantize(Decimal('0.01'))
    net, vat = gross_split_vat_net(line_gross)
    return line_gross, vat, net


def product_unit_gross_price(product: Product) -> Decimal:
    if product.actual_price is not None:
        return min(product.actual_price, product.initial_price)
    return product.initial_price


def stock_listing_quantity(product_id: int) -> int:
    row = ProductListing.objects.filter(
        product_id=product_id,
        channel=ProductListingChannel.STOCK,
    ).first()
    return int(row.quantity) if row else 0


def stock_products_for_select():
    """Active products in stock channel with on-hand qty > 0 (same rules as storefront)."""
    from .catalog_utils import annotate_stock_listing_quantity, stock_catalog_base_queryset

    qs = stock_catalog_base_queryset()
    qs = annotate_stock_listing_quantity(qs)
    return (
        qs.filter(stock_listing_qty__gt=0)
        .order_by('brand', 'name')
        .select_related('shoe', 'racket', 'apparel')
    )


def allocate_invoice_number(order_year: int) -> str:
    with transaction.atomic():
        row, _ = SalesInvoiceYearSequence.objects.select_for_update().get_or_create(
            year=order_year,
            defaults={'last_seq': 38},
        )
        row.last_seq += 1
        row.save(update_fields=['last_seq'])
        return f'{order_year}-{row.last_seq:06d}'


def parse_services_payload(raw: Any) -> list[dict[str, Any]]:
    """Normalize services from JSON / form into [{'name': str, 'gross': Decimal}, ...]."""
    if not raw:
        return []
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = (item.get('name') or '').strip()
        if not name:
            continue
        try:
            g = Decimal(str(item.get('gross', '0') or '0')).quantize(Decimal('0.01'))
        except Exception:
            g = Decimal('0')
        if g <= 0:
            continue
        out.append({'name': name, 'gross': g})
    return out


def compute_order_totals(
    line_gross_values: list[Decimal],
    services_gross: list[Decimal],
    delivery_gross: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    items_gross = sum(line_gross_values, Decimal('0'))
    svc_gross = sum(services_gross, Decimal('0'))
    gross = (items_gross + svc_gross + delivery_gross).quantize(Decimal('0.01'))
    net, vat = gross_split_vat_net(gross)
    return gross, vat, net
