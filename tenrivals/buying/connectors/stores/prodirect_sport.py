"""Connector for prodirect-sport."""

from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'prodirect-sport'
    parser_version = 'phase3-1'
    base_url = 'https://www.prodirectsport.com/'
    search_path_template = '/search?q={query}'
    default_currency = 'GBP'
    default_tax_mode = 'vat_included'
    default_destination_country = 'GB'
