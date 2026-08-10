"""Connector for tennis-point-com / Tennis-Point International (Shopify).

Global suggest.json is often empty / noisy for EU SKUs that exist on
tennis-point.de. Prefer DE catalog search and remap handles onto .com
(Shopify 301 localizes German slugs). Fall back to DE product .js when the
English URL 404s so twin-store assortments still produce an offer.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from buying.connectors.base import NormalizedProductQuery, OfferData, SearchCandidate
from buying.connectors.exceptions import ParsingError, ProductNotFound
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector
from buying.connectors.shopify import (
    BROWSER_UA,
    fetch_shopify_product,
    offer_from_shopify_product,
    remap_candidates_to_store,
    search_shopify_multi,
)

SIBLING_DE = 'https://www.tennis-point.de/'


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'tennis-point-com'
    parser_version = 'phase3-5'
    base_url = 'https://www.tennis-point.com/'
    search_path_template = '/search?q={query}'
    default_currency = 'EUR'
    default_tax_mode = 'vat_included'
    default_destination_country = 'DE'

    def _client(self):
        if self.http is not None:
            return self.http
        from buying.connectors.http import ConnectorHttpClient
        return ConnectorHttpClient(user_agent=BROWSER_UA)

    def search(self, query: NormalizedProductQuery):
        client = self._client()
        phrases = list(query.search_phrases or ['tennis'])
        # Twin store: DE predictive search is the reliable catalog index.
        # COM suggest is often empty; COM products.json can return a single
        # wrong hit and previously short-circuited the DE remap path.
        de_hits = search_shopify_multi(client, base_url=SIBLING_DE, phrases=phrases)
        remapped = remap_candidates_to_store(client, de_hits, target_base_url=self.base_url)
        if remapped:
            return remapped

        # Keep DE URLs as last-resort candidates tagged for details fallback
        if de_hits:
            out: list[SearchCandidate] = []
            for cand in de_hits:
                raw = dict(cand.raw_data or {})
                raw['sibling_de_url'] = cand.url
                raw['fetch_details_from_de'] = True
                out.append(
                    SearchCandidate(
                        title=cand.title,
                        url=cand.url,  # temporary; details will rewrite to COM when possible
                        price_preview=cand.price_preview,
                        currency=cand.currency,
                        supplier_sku=cand.supplier_sku,
                        manufacturer_code=cand.manufacturer_code,
                        raw_data=raw,
                    )
                )
            return out

        found = search_shopify_multi(client, base_url=self.base_url, phrases=phrases)
        return found or super().search(query)

    def get_product_details(self, candidate: SearchCandidate, query: NormalizedProductQuery) -> OfferData:
        client = self._client()
        raw = candidate.raw_data if isinstance(candidate.raw_data, dict) else {}
        page_url = str(candidate.url).split('?')[0]
        product = fetch_shopify_product(client, product_url=page_url)

        # Remap left a COM URL that 404s, or search fell back to a DE URL —
        # load the DE twin SKU and keep/repair the COM product URL.
        if not product:
            de_url = (
                raw.get('remapped_from')
                or raw.get('sibling_de_url')
                or ''
            )
            if de_url and 'tennis-point.de' in de_url:
                product = fetch_shopify_product(client, product_url=de_url)
                if product:
                    # Prefer localized COM handle when remap succeeded earlier
                    if 'tennis-point.com' in page_url and '/products/' in page_url:
                        pass
                    else:
                        page_url = self._com_url_from_de(de_url, product) or page_url
                    offer = self._offer_from_product(product, page_url, query)
                    offer.warnings = list(offer.warnings) + [
                        'Product details loaded from Tennis-Point DE twin catalog'
                    ]
                    return offer

        if not product:
            # Avoid HTML fallback 404 noise — raise a clean connector error
            raise ProductNotFound(f'No Shopify product JSON at {page_url}')

        return self._offer_from_product(product, page_url, query)

    def _offer_from_product(
        self, product: dict, page_url: str, query: NormalizedProductQuery
    ) -> OfferData:
        try:
            offer = offer_from_shopify_product(
                product,
                page_url=page_url,
                default_currency=self.default_currency,
                default_tax_mode=self.default_tax_mode,
                default_destination_country=self.default_destination_country,
                parser_version=self.parser_version,
                wanted_grip=query.grip_size or query.size or '',
            )
        except ValueError as exc:
            raise ParsingError(str(exc)) from exc
        return self.apply_configured_destination(offer)

    def _com_url_from_de(self, de_url: str, product: dict) -> str:
        from buying.connectors.shopify import localize_shopify_handle

        path = urlparse(de_url).path
        if '/products/' not in path:
            return ''
        handle = path.split('/products/')[-1].strip('/').split('/')[0]
        for candidate in localize_shopify_handle(handle):
            probe = urljoin(self.base_url, f'products/{candidate}')
            try:
                response = self._client().get(
                    probe.rstrip('/') + '.js',
                    headers={'Accept': 'application/json', 'User-Agent': BROWSER_UA},
                )
                if response.status_code < 400 and response.content:
                    return probe
            except Exception:
                continue
        # Keep DE URL rather than inventing a dead COM slug
        return de_url
