"""Connector for tennis-point-de."""

from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'tennis-point-de'
    parser_version = 'phase3-1'
    base_url = 'https://www.tennis-point.de/'
    search_path_template = '/search?q={query}'
    default_currency = 'EUR'
    default_tax_mode = 'vat_included'
    default_destination_country = 'DE'
