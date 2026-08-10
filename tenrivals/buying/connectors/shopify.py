"""Lightweight Shopify storefront helpers (products.json / search / PDP .js)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse

from buying.connectors.base import OfferData, SearchCandidate


BROWSER_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
)


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
            headers={'Accept': 'application/json', 'User-Agent': BROWSER_UA},
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
        response = client.get(
            products_url,
            params={'limit': 250},
            headers={'Accept': 'application/json', 'User-Agent': BROWSER_UA},
        )
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


def search_shopify_multi(client, *, base_url: str, phrases: list[str]) -> list[SearchCandidate]:
    """Try several phrases; de-dupe by URL."""
    seen: set[str] = set()
    out: list[SearchCandidate] = []
    for phrase in phrases:
        phrase = (phrase or '').strip()
        if not phrase:
            continue
        for cand in search_shopify(client, base_url=base_url, phrase=phrase):
            key = (cand.url or '').split('?')[0].rstrip('/')
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(cand)
            if len(out) >= 20:
                return out
    return out


_DE_HANDLE_REPLACEMENTS = (
    ('turnierschlaeger', 'tour-racket'),
    ('testschlaeger', 'test-racket'),
    ('unbesaitet', 'unstrung'),
    ('schlaegertasche', 'racket-bag'),
    ('schlaeger', 'racket'),
)


def localize_shopify_handle(handle: str) -> list[str]:
    """Candidate handles when moving a DE Tennis-Point slug onto .com."""
    handle = (handle or '').strip().strip('/')
    if not handle:
        return []
    variants = [handle]
    translated = handle
    for src, dst in _DE_HANDLE_REPLACEMENTS:
        if src in translated:
            translated = translated.replace(src, dst)
    if translated != handle:
        variants.append(translated)
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for item in variants:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def remap_candidates_to_store(
    client,
    candidates: list[SearchCandidate],
    *,
    target_base_url: str,
) -> list[SearchCandidate]:
    """Map sibling-store product URLs onto target host (handle localization + 301)."""
    target = target_base_url.rstrip('/') + '/'
    target_host = urlparse(target).netloc
    remapped: list[SearchCandidate] = []
    for cand in candidates:
        path = urlparse(cand.url).path
        if '/products/' not in path:
            continue
        handle = path.split('/products/')[-1].strip('/').split('/')[0]
        final_url = ''
        for candidate_handle in localize_shopify_handle(handle):
            probe = urljoin(target, f'products/{candidate_handle}')
            # Prefer cheap .js existence check with localized handle
            try:
                js = client.get(
                    probe.rstrip('/') + '.js',
                    headers={'Accept': 'application/json', 'User-Agent': BROWSER_UA},
                )
                if js.status_code < 400 and js.content:
                    final_url = probe
                    break
            except Exception:
                pass
            try:
                response = client.get(
                    probe,
                    headers={'User-Agent': BROWSER_UA, 'Accept': 'text/html'},
                )
                if response.status_code >= 400:
                    continue
                final_url = str(response.url).split('?')[0]
                if urlparse(final_url).netloc != target_host:
                    continue
                if '/products/' not in final_url:
                    continue
                break
            except Exception:
                continue
        if not final_url:
            continue
        remapped.append(
            SearchCandidate(
                title=cand.title,
                url=final_url,
                price_preview=cand.price_preview,
                currency=cand.currency,
                supplier_sku=cand.supplier_sku,
                manufacturer_code=cand.manufacturer_code,
                raw_data={
                    **(cand.raw_data or {}),
                    'remapped_from': cand.url,
                    'handle': final_url.rstrip('/').split('/')[-1],
                },
            )
        )
    return remapped


def fetch_shopify_product(client, *, product_url: str) -> dict | None:
    """Fetch /products/<handle>.js or .json for variant options."""
    path = urlparse(product_url).path.rstrip('/')
    if '/products/' not in path:
        return None
    handle = path.split('/products/')[-1].split('/')[0]
    if not handle:
        return None
    base = f"{urlparse(product_url).scheme}://{urlparse(product_url).netloc}"
    for suffix in ('.js', '.json'):
        try:
            url = urljoin(base + '/', f'products/{handle}{suffix}')
            response = client.get(
                url,
                headers={'Accept': 'application/json', 'User-Agent': BROWSER_UA},
            )
            if response.status_code >= 400:
                continue
            data = response.json()
            if isinstance(data, dict) and (data.get('variants') or data.get('title')):
                return data
            if isinstance(data, dict) and isinstance(data.get('product'), dict):
                return data['product']
        except Exception:
            continue
    return None


def _money_from_shopify(raw) -> Decimal | None:
    if raw is None or raw == '':
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    # Shopify .js prices are usually cents as int (22495 → 224.95)
    if value == value.to_integral_value() and value >= 1000:
        return (value / Decimal('100')).quantize(Decimal('0.01'))
    return value


def shopify_variant_labels(product: dict) -> list[dict]:
    labels: list[dict] = []
    for variant in product.get('variants') or []:
        if not isinstance(variant, dict):
            continue
        title = str(variant.get('title') or '')
        options = [
            str(variant.get(k) or '')
            for k in ('option1', 'option2', 'option3')
            if variant.get(k)
        ]
        label = title or ' / '.join(options)
        if label and label.lower() not in ('default title', 'default'):
                money = _money_from_shopify(variant.get('price'))
                labels.append({
                    'label': label,
                    'sku': str(variant.get('sku') or ''),
                    'available': bool(variant.get('available', True)),
                    # String — SupplierOffer.available_variants is JSONField
                    'price': format(money, 'f') if money is not None else None,
                })
    return labels


def offer_from_shopify_product(
    product: dict,
    *,
    page_url: str,
    default_currency: str,
    default_tax_mode: str,
    default_destination_country: str,
    parser_version: str,
    wanted_grip: str = '',
) -> OfferData:
    """Build OfferData from Shopify product .js payload."""
    import re

    from buying.services.enrichment import normalize_grip_size

    title = str(product.get('title') or 'Product')[:300]
    variants = [v for v in (product.get('variants') or []) if isinstance(v, dict)]
    labels = shopify_variant_labels(product)
    wanted = normalize_grip_size(wanted_grip or '')

    def _variant_grips(variant: dict) -> set[str]:
        grips: set[str] = set()
        blob = ' '.join(
            str(variant.get(k) or '')
            for k in ('title', 'option1', 'option2', 'option3', 'public_title')
        )
        normalized = normalize_grip_size(blob)
        if normalized.startswith('L') and len(normalized) == 2:
            grips.add(normalized)
        for piece in re.findall(r'\b([0-5])\b', blob):
            grips.add(f'L{piece}')
        return grips

    chosen = None
    if wanted and variants:
        for variant in variants:
            if wanted in _variant_grips(variant):
                chosen = variant
                if variant.get('available'):
                    break
    if chosen is None and variants:
        chosen = next((v for v in variants if v.get('available')), variants[0])

    price = _money_from_shopify((chosen or {}).get('price')) if chosen else None
    if price is None and variants:
        price = _money_from_shopify(variants[0].get('price'))
    if price is None:
        raise ValueError(f'No Shopify price for {page_url}')

    compare = _money_from_shopify((chosen or {}).get('compare_at_price')) if chosen else None
    original = compare if compare and compare > price else None
    available = bool((chosen or {}).get('available', True)) if chosen else None
    stock = 'in_stock' if available else ('out_of_stock' if available is False else 'unknown')

    description = str(product.get('description') or '')
    page_text = re.sub(r'<[^>]+>', ' ', description)
    page_text = ' '.join(page_text.split())[:20_000]
    page_text = f'{title} {page_text}'.strip()

    variant_available = None
    if wanted and variants:
        matching = [v for v in variants if wanted in _variant_grips(v)]
        if matching:
            variant_available = any(bool(v.get('available')) for v in matching)
        else:
            # Listed grip options on product but none match
            all_grips: set[str] = set()
            for v in variants:
                all_grips |= _variant_grips(v)
            if all_grips:
                variant_available = False

    offer = OfferData(
        title=title,
        product_url=page_url,
        original_price=original,
        displayed_price=price,
        effective_price=price,
        currency=default_currency,
        displayed_price_tax_mode=default_tax_mode,
        local_tax_source='configured_rule',
        public_price=price,
        selected_destination_country=default_destination_country or None,
        destination_selection_source='configured_default' if default_destination_country else '',
        destination_selection_confirmed=bool(default_destination_country),
        stock_status=stock,
        supplier_sku=str((chosen or {}).get('sku') or product.get('id') or ''),
        manufacturer_code='',
        ean=str((chosen or {}).get('barcode') or ''),
        parser_version=parser_version,
        raw_payload={
            'shopify': True,
            'handle': product.get('handle'),
            'page_text': page_text,
            'variant_id': (chosen or {}).get('id'),
        },
        content_type='application/json',
        purchase_context_status='confirmed' if default_destination_country else 'purchase_context_unconfirmed',
        available_variants=labels,
        requested_variant_available=variant_available,
        free_shipping_status='threshold_unknown',
    )
    if wanted and variant_available is None and labels:
        offer.warnings.append('Variant availability not verified on product page')
    return offer
