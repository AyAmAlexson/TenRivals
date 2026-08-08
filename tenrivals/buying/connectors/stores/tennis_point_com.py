"""Connector for tennis-point-com / Tennis-Point International (Shopify).

Global suggest.json is often empty for EU SKUs. Fall back to DE catalog search
and remap product handles onto .com (Shopify 301 localizes German handles).
"""

from buying.connectors.base import NormalizedProductQuery, OfferData, SearchCandidate
from buying.connectors.exceptions import ParsingError
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
    parser_version = 'phase3-4'
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
        found = search_shopify_multi(client, base_url=self.base_url, phrases=phrases)
        if found:
            return found
        # International predictive search is frequently empty for the same SKUs
        # that exist on tennis-point.de — remap DE hits onto .com handles.
        de_hits = search_shopify_multi(client, base_url=SIBLING_DE, phrases=phrases)
        remapped = remap_candidates_to_store(client, de_hits, target_base_url=self.base_url)
        return remapped or super().search(query)

    def get_product_details(self, candidate: SearchCandidate, query: NormalizedProductQuery) -> OfferData:
        client = self._client()
        product = fetch_shopify_product(client, product_url=candidate.url)
        if not product:
            return super().get_product_details(candidate, query)
        try:
            offer = offer_from_shopify_product(
                product,
                page_url=str(candidate.url).split('?')[0],
                default_currency=self.default_currency,
                default_tax_mode=self.default_tax_mode,
                default_destination_country=self.default_destination_country,
                parser_version=self.parser_version,
                wanted_grip=query.grip_size or query.size or '',
            )
        except ValueError as exc:
            raise ParsingError(str(exc)) from exc
        return self.apply_configured_destination(offer)
