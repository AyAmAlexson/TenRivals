"""Connector for saburi-sports."""

from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'saburi-sports'
    parser_version = 'phase3-1'
    base_url = 'https://saburisports.com/'
    search_path_template = '/search?q={query}'
    default_currency = 'USD'
    default_tax_mode = 'sales_tax_at_checkout'
    default_destination_country = 'US'
