"""Connector for tennis-point-de (Shopify)."""

from buying.connectors.base import NormalizedProductQuery, OfferData, SearchCandidate
from buying.connectors.exceptions import ParsingError
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector
from buying.connectors.shopify import (
    BROWSER_UA,
    fetch_shopify_product,
    offer_from_shopify_product,
    search_shopify_multi,
)


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'tennis-point-de'
    parser_version = 'phase3-4'
    base_url = 'https://www.tennis-point.de/'
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
        phrases = list(query.search_phrases or ['tennis'])
        found = search_shopify_multi(self._client(), base_url=self.base_url, phrases=phrases)
        return found or super().search(query)

    def get_product_details(self, candidate: SearchCandidate, query: NormalizedProductQuery) -> OfferData:
        client = self._client()
        product = fetch_shopify_product(client, product_url=candidate.url)
        if not product:
            return super().get_product_details(candidate, query)
        try:
            offer = offer_from_shopify_product(
                product,
                page_url=candidate.url.split('?')[0],
                default_currency=self.default_currency,
                default_tax_mode=self.default_tax_mode,
                default_destination_country=self.default_destination_country,
                parser_version=self.parser_version,
                wanted_grip=query.grip_size or query.size or '',
            )
        except ValueError as exc:
            raise ParsingError(str(exc)) from exc
        return self.apply_configured_destination(offer)
