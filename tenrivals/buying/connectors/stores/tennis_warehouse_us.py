"""Connector for tennis-warehouse-us — anti-bot blocked for automated search."""

from buying.connectors.base import NormalizedProductQuery
from buying.connectors.exceptions import CapabilityNotSupported
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'tennis-warehouse-us'
    parser_version = 'phase3-2'
    base_url = 'https://www.tennis-warehouse.com/'
    search_path_template = '/search?searchtext={query}'
    default_currency = 'USD'
    default_tax_mode = 'sales_tax_at_checkout'
    default_destination_country = 'US'

    def search(self, query: NormalizedProductQuery):
        raise CapabilityNotSupported(
            'Tennis Warehouse US blocks automated access (HTTP 403). '
            'Use manual offer entry until a reliable method exists.'
        )
