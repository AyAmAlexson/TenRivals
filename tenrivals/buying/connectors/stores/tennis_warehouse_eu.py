"""Tennis Warehouse Europe connector — HTML search + product page (JSON-LD / legacy markup)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from buying.connectors.base import (
    ConnectorCapabilities,
    NormalizedProductQuery,
    OfferData,
    SearchCandidate,
)
from buying.connectors.exceptions import ParsingError, ProductNotFound
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.jsonld import parse_product_json_ld
from buying.connectors.registry import register_connector


@register_connector
class TennisWarehouseEuropeConnector(HtmlJsonLdConnector):
    code = 'tennis-warehouse-eu'
    parser_version = 'phase3-1'
    base_url = 'https://www.tenniswarehouse-europe.com/'
    search_path_template = '/cgi-bin/search?searchtext={query}'
    default_currency = 'EUR'
    default_tax_mode = 'vat_included'
    default_destination_country = 'DE'
    capabilities = ConnectorCapabilities(
        search=True,
        product_details=True,
        variant_availability=True,
        destination_selection=False,
        tax_detection=True,
        promotion_detection=True,
        cart_simulation=False,
        shipping_calculation=False,
        authenticated_pricing=False,
    )

    def parse_search_html(self, html, *, page_url, query: NormalizedProductQuery):
        soup = BeautifulSoup(html, 'html.parser')
        candidates: list[SearchCandidate] = []
        seen = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            if 'descpage' not in href.lower():
                continue
            full = urljoin(page_url, href)
            if full in seen:
                continue
            title = a.get_text(' ', strip=True) or ''
            if len(title) < 4:
                parent = a.find_parent(['div', 'li', 'article'])
                if parent:
                    title = parent.get_text(' ', strip=True)[:300]
            seen.add(full)
            candidates.append(SearchCandidate(title=title[:300] or 'Product', url=full))
            if len(candidates) >= 20:
                break
        return candidates

    def parse_product_html(self, html, *, page_url, candidate, query):
        parsed = parse_product_json_ld(html)
        soup = BeautifulSoup(html, 'html.parser')
        title = (parsed or {}).get('title') or ''
        if not title:
            h1 = soup.find('h1')
            title = h1.get_text(strip=True) if h1 else candidate.title
        if not title:
            raise ProductNotFound(f'No title at {page_url}')

        price = (parsed or {}).get('price')
        original = None
        if price is None:
            price_el = soup.select_one('.price, .prod_price, [itemprop=price], .our_price')
            if price_el:
                raw = price_el.get('content') or price_el.get_text()
                try:
                    text = ''.join(c for c in str(raw) if c.isdigit() or c in '.,')
                    if ',' in text and '.' in text:
                        text = text.replace('.', '').replace(',', '.')
                    elif ',' in text:
                        text = text.replace(',', '.')
                    price = Decimal(text)
                except (InvalidOperation, ValueError):
                    price = None
        if price is None:
            raise ParsingError(f'No price at {page_url}')

        # Sale / strikethrough original
        was = soup.select_one('.was_price, .old_price, del, s')
        if was:
            try:
                text = ''.join(c for c in was.get_text() if c.isdigit() or c in '.,')
                text = text.replace(',', '.')
                original = Decimal(text)
            except (InvalidOperation, ValueError):
                original = None

        discount_amount = (original - price) if original and original > price else None
        discount_percent = None
        if discount_amount and original:
            discount_percent = (discount_amount / original * Decimal('100')).quantize(Decimal('0.01'))

        stock = (parsed or {}).get('stock_status') or 'unknown'
        offer = OfferData(
            title=title[:300],
            product_url=page_url,
            original_price=original,
            displayed_price=price,
            effective_price=price,
            currency=(parsed or {}).get('currency') or self.default_currency,
            displayed_price_tax_mode=self.default_tax_mode,
            local_tax_source='configured_rule',
            product_discount_amount=discount_amount,
            product_discount_percent=discount_percent,
            public_price=price,
            selected_destination_country=self.default_destination_country,
            destination_selection_source='configured_default',
            destination_selection_confirmed=True,
            stock_status=stock,
            supplier_sku=(parsed or {}).get('sku') or '',
            manufacturer_code=(parsed or {}).get('mpn') or '',
            ean=(parsed or {}).get('ean') or '',
            upc=(parsed or {}).get('upc') or '',
            parser_version=self.parser_version,
            raw_payload={'json_ld': (parsed or {}).get('raw')},
            free_shipping_status='threshold_unknown',
        )
        # Grip / size options
        variants = []
        for opt in soup.select('select option, .size_option, .grip_option'):
            label = opt.get_text(strip=True)
            if label and label.lower() not in ('select', 'choose', '-'):
                variants.append({'label': label})
        offer.available_variants = variants
        if query.grip_size or query.size:
            wanted = (query.grip_size or query.size or '').lower()
            if variants and wanted:
                offer.requested_variant_available = any(
                    wanted in (v.get('label') or '').lower() for v in variants
                )
            else:
                offer.requested_variant_available = None
                offer.warnings.append('Variant availability not fully verified')
        return self.apply_configured_destination(offer)
