"""Connector for ole-tennis (Shopify)."""

from buying.connectors.base import NormalizedProductQuery
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector
from buying.connectors.shopify import search_shopify


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'ole-tennis'
    parser_version = 'phase3-2'
    base_url = 'https://oletennis.com/'
    search_path_template = '/search?q={query}'
    default_currency = 'USD'
    default_tax_mode = 'sales_tax_at_checkout'
    default_destination_country = 'US'

    def search(self, query: NormalizedProductQuery):
        phrase = (query.search_phrases or ['tennis'])[0]
        found = search_shopify(self._client(), base_url=self.base_url, phrase=phrase)
        return found or super().search(query)
