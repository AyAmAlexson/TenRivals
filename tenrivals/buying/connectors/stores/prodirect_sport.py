"""Connector for prodirect-sport (Shopify storefront)."""

from buying.connectors.base import NormalizedProductQuery
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector
from buying.connectors.shopify import search_shopify


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'prodirect-sport'
    parser_version = 'phase3-3'
    base_url = 'https://www.prodirectsport.com/'
    search_path_template = '/search?q={query}'
    default_currency = 'GBP'
    default_tax_mode = 'vat_included'
    default_destination_country = 'GB'

    def search(self, query: NormalizedProductQuery):
        phrase = (query.search_phrases or ['tennis'])[0]
        found = search_shopify(self._client(), base_url=self.base_url, phrase=phrase)
        return found or super().search(query)
