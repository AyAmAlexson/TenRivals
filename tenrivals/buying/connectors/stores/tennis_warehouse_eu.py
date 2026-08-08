"""Tennis Warehouse Europe connector — HTML search + product page (JSON-LD / legacy markup)."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from buying.connectors.base import (
    ConnectorCapabilities,
    NormalizedProductQuery,
    OfferData,
    SearchCandidate,
)
from buying.connectors.exceptions import ParsingError, ProductNotFound
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.http import ConnectorHttpClient
from buying.connectors.jsonld import parse_product_json_ld
from buying.connectors.registry import register_connector
from buying.connectors.shopify import BROWSER_UA


@register_connector
class TennisWarehouseEuropeConnector(HtmlJsonLdConnector):
    code = 'tennis-warehouse-eu'
    parser_version = 'phase3-2'
    base_url = 'https://www.tenniswarehouse-europe.com/'
    # /cgi-bin/search returns HTTP 406; the live storefront form posts here.
    search_path_template = '/search-tennis.html?searchtext={query}'
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

    def _client(self) -> ConnectorHttpClient:
        if self.http is not None:
            return self.http
        return ConnectorHttpClient(
            user_agent=BROWSER_UA,
            timeout=40,
            headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-GB,en;q=0.9',
            },
        )

    def search(self, query: NormalizedProductQuery) -> list[SearchCandidate]:
        client = self._client()
        # Warm cookies — some TW endpoints are picky without a homepage hit.
        try:
            client.get(self.base_url)
        except Exception:
            pass
        phrases = list(query.search_phrases or ['tennis'])
        seen: set[str] = set()
        candidates: list[SearchCandidate] = []
        for phrase in phrases:
            url = self.search_url(phrase)
            response = client.get(
                url,
                headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-GB,en;q=0.9',
                    'Referer': self.base_url,
                },
            )
            response.raise_for_status()
            for cand in self.parse_search_html(
                response.text, page_url=str(response.url), query=query
            ):
                key = cand.url.split('?')[0].rstrip('/')
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(cand)
                if len(candidates) >= 20:
                    return candidates
        return candidates

    def parse_search_html(self, html, *, page_url, query: NormalizedProductQuery):
        soup = BeautifulSoup(html, 'html.parser')
        candidates: list[SearchCandidate] = []
        seen = set()
        for a in soup.find_all('a', href=True):
            href = (a['href'] or '').replace('\r', '').strip()
            if 'descpage' not in href.lower():
                continue
            if '#cust_reviews' in href.lower() or href.lower().endswith('#cust_reviews'):
                continue
            full = urljoin(page_url, href).split('#')[0]
            if full in seen:
                continue
            title = a.get_text(' ', strip=True) or ''
            title = re.sub(r'\s+', ' ', title).strip()
            if len(title) < 8 or title.lower() in ('5.0 1 review', 'review', 'reviews'):
                # Empty anchors are common — recover title from URL slug.
                title = self._title_from_twe_url(full) or title
            if len(title) < 4:
                parent = a.find_parent(['div', 'li', 'article', 'td'])
                if parent:
                    title = parent.get_text(' ', strip=True)[:300]
            title = re.sub(r'\s+', ' ', title).strip()
            if len(title) < 4:
                continue
            seen.add(full)
            candidates.append(SearchCandidate(title=title[:300] or 'Product', url=full))
            if len(candidates) >= 20:
                break
        return candidates

    @staticmethod
    def _title_from_twe_url(url: str) -> str:
        path = urlparse_path(url)
        # /Wilson_Blade_100_v10_Racket/descpageRCWILSON-WB1001-EN.html
        parts = [p for p in path.split('/') if p]
        if not parts:
            return ''
        slug = parts[0]
        if slug.lower().startswith('descpage'):
            return ''
        return unquote(slug).replace('_', ' ').strip()

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
            price_el = soup.select_one(
                '.price, .prod_price, [itemprop=price], .our_price, meta[itemprop=price]'
            )
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
            raw_payload={
                'json_ld': (parsed or {}).get('raw'),
                'page_text': ' '.join(soup.stripped_strings)[:20_000],
            },
            free_shipping_status='threshold_unknown',
        )
        # Grip sizes appear as "Grip Size: (3)" style items on TW Europe PDPs
        variants = []
        for m in re.finditer(
            r'Grip\s*Size\s*</strong>\s*:\s*<span[^>]*>\(?\s*([0-5])\s*\)?</span>',
            html,
            re.I,
        ):
            variants.append({'label': f'L{m.group(1)}'})
        if not variants:
            for opt in soup.select('select option, .size_option, .grip_option, .styleitem'):
                label = opt.get_text(strip=True)
                if re.fullmatch(r'\(?[0-5]\)?', label or ''):
                    variants.append({'label': f'L{label.strip("()")}'})
                elif label and label.lower() not in ('select', 'choose', '-', 'tennis', 'pickleball'):
                    if re.search(r'\bL[0-5]\b', label, re.I) or re.fullmatch(r'[0-5]', label):
                        variants.append({'label': label})
        # De-dupe
        seen_labels = set()
        uniq = []
        for v in variants:
            lab = v['label']
            if lab in seen_labels:
                continue
            seen_labels.add(lab)
            uniq.append(v)
        offer.available_variants = uniq
        if query.grip_size or query.size:
            from buying.services.enrichment import normalize_grip_size
            wanted = normalize_grip_size(query.grip_size or query.size or '')
            if uniq and wanted:
                available = {normalize_grip_size(v.get('label') or '') for v in uniq}
                offer.requested_variant_available = wanted in available
            else:
                offer.requested_variant_available = None
                offer.warnings.append('Variant availability not verified on product page')
        return self.apply_configured_destination(offer)


def urlparse_path(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).path or ''
