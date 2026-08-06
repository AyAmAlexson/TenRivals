"""Lightweight Shopify storefront helpers (products.json / search)."""

from __future__ import annotations

from decimal import Decimal
from urllib.parse import urljoin

from buying.connectors.base import SearchCandidate


def parse_products_json(payload: dict | list, *, base_url: str) -> list[SearchCandidate]:
    products = payload.get('products') if isinstance(payload, dict) else payload
    if not isinstance(products, list):
        return []
    results: list[SearchCandidate] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        handle = product.get('handle') or ''
        title = str(product.get('title') or '')
        variants = product.get('variants') or []
        price = None
        sku = ''
        currency = None
        if variants and isinstance(variants[0], dict):
            try:
                price = Decimal(str(variants[0].get('price')))
            except Exception:
                price = None
            sku = str(variants[0].get('sku') or '')
        path = f'/products/{handle}' if handle else ''
        url = urljoin(base_url.rstrip('/') + '/', path.lstrip('/'))
        results.append(
            SearchCandidate(
                title=title,
                url=url,
                price_preview=price,
                currency=currency,
                supplier_sku=sku or None,
                raw_data={'shopify': True, 'handle': handle, 'id': product.get('id')},
            )
        )
    return results
