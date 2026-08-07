"""Connector for extreme-tennis (PrestaShop)."""

from urllib.parse import quote_plus, urljoin

from buying.connectors.base import NormalizedProductQuery
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'extreme-tennis'
    parser_version = 'phase3-3'
    base_url = 'https://www.extreme-tennis.eu/'
    search_path_template = '/recherche?controller=search&s={query}'
    default_currency = 'EUR'
    default_tax_mode = 'vat_included'
    default_destination_country = 'DE'

    def search(self, query: NormalizedProductQuery):
        client = self._client()
        phrase = (query.search_phrases or ['tennis'])[0]
        for path in (
            f'recherche?controller=search&s={quote_plus(phrase)}',
            f'search?controller=search&s={quote_plus(phrase)}',
            f'search?q={quote_plus(phrase)}',
        ):
            url = urljoin(self.base_url, path)
            try:
                response = client.get(
                    url,
                    headers={
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'en-GB,en;q=0.9',
                    },
                )
                if response.status_code >= 400:
                    continue
                found = self.parse_search_html(
                    response.text, page_url=str(response.url), query=query
                )
                if found:
                    return found
            except Exception:
                continue
        return []
