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
                supplier_sku=sku or None,
                raw_data={'shopify': True, 'handle': handle, 'id': product.get('id')},
            )
        )
    return results


def search_shopify(client, *, base_url: str, phrase: str) -> list[SearchCandidate]:
    """Try suggest.json then filter products.json; empty list if both fail."""
    suggest_url = urljoin(base_url.rstrip('/') + '/', 'search/suggest.json')
    try:
        response = client.get(
            suggest_url,
            params={
                'q': phrase,
                'resources[type]': 'product',
                'resources[limit]': 20,
            },
        )
        if response.status_code < 400:
            data = response.json()
            resources = (data.get('resources') or {}).get('results') or {}
            products = resources.get('products') or []
            out: list[SearchCandidate] = []
            for product in products:
                handle = product.get('handle') or ''
                title = str(product.get('title') or '')
                url = product.get('url') or (
                    urljoin(base_url.rstrip('/') + '/', f'products/{handle}') if handle else ''
                )
                if not url:
                    continue
                if url.startswith('/'):
                    url = urljoin(base_url.rstrip('/') + '/', url.lstrip('/'))
                price = None
                try:
                    if product.get('price'):
                        price = Decimal(str(product['price'])) / Decimal('100')
                except Exception:
                    price = None
                out.append(
                    SearchCandidate(
                        title=title[:300] or 'Product',
                        url=url,
                        price_preview=price,
                        raw_data={'shopify_suggest': True, 'handle': handle},
                    )
                )
            if out:
                return out
    except Exception:
        pass

    try:
        products_url = urljoin(base_url.rstrip('/') + '/', 'products.json')
        response = client.get(products_url, params={'limit': 250})
        if response.status_code >= 400:
            return []
        all_products = parse_products_json(response.json(), base_url=base_url)
        tokens = [t for t in phrase.lower().split() if len(t) > 2]
        filtered = [
            c for c in all_products
            if all(t in (c.title or '').lower() for t in tokens)
        ]
        return filtered[:20] or []
    except Exception:
        return []
