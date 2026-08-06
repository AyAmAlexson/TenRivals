"""JSON-LD / schema.org Product helpers."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from bs4 import BeautifulSoup


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_decimal(value) -> Decimal | None:
    if value in (None, ''):
        return None
    if isinstance(value, dict) and 'value' in value:
        value = value['value']
    text = re.sub(r'[^\d.,]', '', str(value))
    if not text:
        return None
    if ',' in text and '.' in text:
        if text.rfind(',') > text.rfind('.'):
            text = text.replace('.', '').replace(',', '.')
        else:
            text = text.replace(',', '')
    elif ',' in text:
        parts = text.split(',')
        text = text.replace(',', '.') if len(parts[-1]) <= 2 else text.replace(',', '')
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def extract_json_ld_blocks(html: str) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    blocks: list[dict] = []
    for tag in soup.find_all('script', type=lambda t: t and 'ld+json' in t.lower()):
        raw = tag.string or tag.get_text() or ''
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in _as_list(data):
            if isinstance(item, dict):
                if item.get('@graph'):
                    blocks.extend(x for x in item['@graph'] if isinstance(x, dict))
                else:
                    blocks.append(item)
    return blocks


def find_products(blocks: list[dict]) -> list[dict]:
    products = []
    for block in blocks:
        types = _as_list(block.get('@type'))
        types_l = {str(t).lower() for t in types}
        if 'product' in types_l:
            products.append(block)
    return products


def offer_from_product(product: dict) -> dict[str, Any]:
    """Normalize schema.org Product + Offer into a flat dict."""
    offers = _as_list(product.get('offers') or product.get('offer'))
    offer = next((o for o in offers if isinstance(o, dict)), {}) if offers else {}
    price = (
        _parse_decimal(offer.get('price'))
        or _parse_decimal(offer.get('lowPrice'))
        or _parse_decimal(product.get('price'))
    )
    currency = (
        offer.get('priceCurrency')
        or product.get('priceCurrency')
        or ''
    )
    availability = str(offer.get('availability') or product.get('availability') or '')
    stock = 'unknown'
    avail_l = availability.lower()
    if 'instock' in avail_l or avail_l.endswith('/instock'):
        stock = 'in_stock'
    elif 'outofstock' in avail_l or 'soldout' in avail_l:
        stock = 'out_of_stock'
    elif 'preorder' in avail_l:
        stock = 'preorder'

    brand = product.get('brand')
    if isinstance(brand, dict):
        brand = brand.get('name') or ''
    sku = str(product.get('sku') or offer.get('sku') or '')
    mpn = str(product.get('mpn') or '')
    gtin = str(product.get('gtin13') or product.get('gtin') or product.get('ean') or '')
    upc = str(product.get('gtin12') or product.get('upc') or '')

    return {
        'title': str(product.get('name') or ''),
        'description': str(product.get('description') or ''),
        'brand': str(brand or ''),
        'sku': sku,
        'mpn': mpn,
        'ean': gtin,
        'upc': upc,
        'price': price,
        'currency': str(currency or '').upper(),
        'stock_status': stock,
        'url': str(product.get('url') or offer.get('url') or ''),
        'raw': product,
    }


def parse_product_json_ld(html: str) -> dict[str, Any] | None:
    products = find_products(extract_json_ld_blocks(html))
    if not products:
        return None
    return offer_from_product(products[0])
