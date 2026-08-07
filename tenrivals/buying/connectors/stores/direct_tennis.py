"""Connector for direct-tennis."""

from buying.connectors.base import NormalizedProductQuery
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'direct-tennis'
    parser_version = 'phase3-3'
    base_url = 'https://www.directtennis.co.uk/'
    # Site uses /Search/{query} for both search results and some PDP slugs
    search_path_template = '/Search/{query}'
    default_currency = 'GBP'
    default_tax_mode = 'vat_included'
    default_destination_country = 'GB'

    def search(self, query: NormalizedProductQuery):
        # Prefer unencoded spaces as path segments the site expects in /Search/...
        from urllib.parse import quote
        client = self._client()
        phrase = (query.search_phrases or ['tennis'])[0]
        path = f"/Search/{quote(phrase, safe='')}"
        from urllib.parse import urljoin
        url = urljoin(self.base_url, path.lstrip('/'))
        response = client.get(url)
        response.raise_for_status()
        found = self.parse_search_html(response.text, page_url=str(response.url), query=query)
        if found:
            return found
        # Fallback: classic query-string search
        response = client.get(urljoin(self.base_url, f'search?q={quote(phrase)}'))
        response.raise_for_status()
        return self.parse_search_html(response.text, page_url=str(response.url), query=query)
