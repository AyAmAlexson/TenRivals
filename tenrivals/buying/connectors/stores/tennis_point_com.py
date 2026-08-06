"""Connector for tennis-point-com."""

from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'tennis-point-com'
    parser_version = 'phase3-1'
    base_url = 'https://www.tennis-point.com/'
    search_path_template = '/search?q={query}'
    default_currency = 'EUR'
    default_tax_mode = 'vat_included'
    default_destination_country = 'DE'
