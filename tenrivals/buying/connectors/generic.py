"""Shared HTML/JSON-LD connector helpers for store implementations."""

from __future__ import annotations

import re
import time
from decimal import Decimal
from urllib.parse import quote_plus, urljoin

from buying.connectors.base import (
    ConnectorCapabilities,
    HealthCheckResult,
    NormalizedProductQuery,
    OfferData,
    PurchaseContext,
    SearchCandidate,
    SupplierConnector,
)
from buying.connectors.exceptions import ParsingError, ProductNotFound
from buying.connectors.http import ConnectorHttpClient
from buying.connectors.jsonld import parse_product_json_ld
from bs4 import BeautifulSoup


class HtmlJsonLdConnector(SupplierConnector):
    """Baseline connector: health on base_url, search via query URL, details via JSON-LD."""

    base_url: str = ''
    search_path_template: str = '/search?q={query}'
    default_currency: str = 'EUR'
    default_tax_mode: str = 'unknown'
    default_destination_country: str = ''
    capabilities = ConnectorCapabilities(
        search=True,
        product_details=True,
        variant_availability=False,
        destination_selection=False,
        tax_detection=False,
        promotion_detection=False,
        cart_simulation=False,
        shipping_calculation=False,
        authenticated_pricing=False,
    )

    def _client(self) -> ConnectorHttpClient:
        if self.http is not None:
            return self.http
        return ConnectorHttpClient()

    def health_check(self) -> HealthCheckResult:
        url = self.base_url.rstrip('/') + '/'
        client = self._client()
        started = time.monotonic()
        try:
            response = client.get(url)
            elapsed = getattr(response, '_buying_elapsed_ms', int((time.monotonic() - started) * 1000))
            status = 'available' if response.status_code < 400 else 'failed'
            return HealthCheckResult(
                status=status,
                response_time_ms=elapsed,
                http_status=response.status_code,
                checked_url=url,
                error_type=None if status == 'available' else 'http_error',
                error_message='' if status == 'available' else f'HTTP {response.status_code}',
                metadata={'parser_version': self.parser_version},
            )
        except Exception as exc:
            return HealthCheckResult(
                status='failed',
                response_time_ms=int((time.monotonic() - started) * 1000),
                checked_url=url,
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
            )

    def search_url(self, phrase: str) -> str:
        return urljoin(
            self.base_url.rstrip('/') + '/',
            self.search_path_template.format(query=quote_plus(phrase)).lstrip('/'),
        )

    def search(self, query: NormalizedProductQuery) -> list[SearchCandidate]:
        client = self._client()
        phrase = (query.search_phrases or ['tennis'])[0]
        url = self.search_url(phrase)
        response = client.get(url)
        response.raise_for_status()
        return self.parse_search_html(response.text, page_url=str(response.url), query=query)

    def parse_search_html(
        self,
        html: str,
        *,
        page_url: str,
        query: NormalizedProductQuery,
    ) -> list[SearchCandidate]:
        """Override in store modules for site-specific search cards."""
        soup = BeautifulSoup(html, 'html.parser')
        candidates: list[SearchCandidate] = []
        seen = set()
        for a in soup.select('a[href]'):
            href = a.get('href') or ''
            title = a.get_text(' ', strip=True)
            if not title or len(title) < 8:
                continue
            full = urljoin(page_url, href)
            if full in seen or full.rstrip('/') == self.base_url.rstrip('/'):
                continue
            # Prefer product-like paths (storefronts vary widely)
            path = full.lower()
            product_tokens = (
                '/product', '/products/', '/p/', '/p?', '/item', '/racquet', '/shoe',
                'descpage', '/search/',  # Direct Tennis PDP lives under /Search/slug
            )
            if not any(tok in path for tok in product_tokens):
                # Smashinn / TradeInn: .../123456/p  (no trailing slash)
                if not re.search(r'/\d+/p(?:$|[?#])', path):
                    # Extreme / PrestaShop: /{cat}/{id}-{slug}.html
                    if not re.search(r'/\d{3,}-[a-z0-9-]+\.html', path):
                        continue
            seen.add(full)
            candidates.append(SearchCandidate(title=title[:300], url=full))
            if len(candidates) >= 15:
                break
        return candidates

    def get_product_details(
        self,
        candidate: SearchCandidate,
        query: NormalizedProductQuery,
    ) -> OfferData:
        client = self._client()
        response = client.get(candidate.url)
        response.raise_for_status()
        return self.parse_product_html(
            response.text,
            page_url=str(response.url),
            candidate=candidate,
            query=query,
        )

    def parse_product_html(
        self,
        html: str,
        *,
        page_url: str,
        candidate: SearchCandidate,
        query: NormalizedProductQuery,
    ) -> OfferData:
        parsed = parse_product_json_ld(html)
        if not parsed or parsed.get('price') is None:
            # Fallback: try og/meta price patterns
            soup = BeautifulSoup(html, 'html.parser')
            title = ''
            h1 = soup.find('h1')
            if h1:
                title = h1.get_text(strip=True)
            if not title:
                raise ProductNotFound(f'No product data at {page_url}')
            raise ParsingError(f'Could not extract price from {page_url}')

        price = parsed['price']
        assert isinstance(price, Decimal)
        currency = parsed.get('currency') or self.default_currency
        original = None
        ctx = self.purchase_context
        dest = (ctx.destination_country if ctx else None) or self.default_destination_country or None
        dest_confirmed = bool(dest) and bool(getattr(self.capabilities, 'destination_selection', False))
        # If connector does not implement destination selection, mark as configured default
        source = 'configured_default' if dest else ''
        if dest and not self.capabilities.destination_selection:
            dest_confirmed = True
            source = 'configured_default'

        offer = OfferData(
            title=parsed.get('title') or candidate.title or 'Product',
            product_url=page_url,
            original_price=original,
            displayed_price=price,
            effective_price=price,
            currency=currency,
            displayed_price_tax_mode=self.default_tax_mode,
            local_tax_source='unknown',
            public_price=price,
            selected_destination_country=dest,
            selected_destination_postal_code=ctx.destination_postal_code if ctx else None,
            destination_selection_source=source,
            destination_selection_confirmed=dest_confirmed,
            stock_status=parsed.get('stock_status') or 'unknown',
            supplier_sku=parsed.get('sku') or candidate.supplier_sku or '',
            manufacturer_code=parsed.get('mpn') or candidate.manufacturer_code or '',
            ean=parsed.get('ean') or '',
            upc=parsed.get('upc') or '',
            parser_version=self.parser_version,
            raw_payload={'json_ld': parsed.get('raw'), 'page_text': ''},
            content_type='text/html',
            purchase_context_status='confirmed' if dest_confirmed else 'purchase_context_unconfirmed',
        )
        # Capture page text + grip/size options for identity / variant verification
        soup = BeautifulSoup(html, 'html.parser')
        page_text = ' '.join(soup.stripped_strings)[:20_000]
        offer.raw_payload['page_text'] = page_text
        variants = []
        for opt in soup.select('select option, [data-value], .swatch, .variant-option'):
            label = opt.get_text(strip=True) or opt.get('data-value') or opt.get('value') or ''
            if label and label.lower() not in ('select', 'choose', '-', ''):
                variants.append({'label': label})
        offer.available_variants = variants
        if query.size or query.grip_size or query.color:
            if not variants:
                offer.requested_variant_available = None
                offer.warnings.append('Variant availability not verified on product page')
        return self.build_default_context_warnings(offer)

    def apply_configured_destination(self, offer: OfferData) -> OfferData:
        ctx: PurchaseContext | None = self.purchase_context
        if not offer.selected_destination_country and ctx:
            offer.selected_destination_country = ctx.destination_country
            offer.selected_destination_postal_code = ctx.destination_postal_code
            offer.destination_selection_source = 'configured_default'
            offer.destination_selection_confirmed = True
        return self.build_default_context_warnings(offer)
