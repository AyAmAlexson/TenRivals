"""Connector for tennis-nuts."""

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from buying.connectors.base import SearchCandidate
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'tennis-nuts'
    parser_version = 'phase3-2'
    base_url = 'https://www.tennisnuts.com/'
    # Site uses Magento-style catalog search under /shop/ (plain /search?q= → 404)
    search_path_template = '/shop/catalogsearch/result/?q={query}'
    default_currency = 'GBP'
    default_tax_mode = 'vat_included'
    default_destination_country = 'GB'

    def parse_search_html(self, html, *, page_url, query):
        soup = BeautifulSoup(html, 'html.parser')
        candidates: list[SearchCandidate] = []
        seen = set()
        for a in soup.select('a.product-item-link, a.product-image-link, .product-item a[href]'):
            href = a.get('href') or ''
            title = a.get_text(' ', strip=True) or a.get('title') or ''
            if not href:
                continue
            full = urljoin(page_url, href)
            if full in seen:
                continue
            if '.html' not in full.lower() and '/shop/' not in full.lower():
                continue
            if not title or len(title) < 4:
                continue
            seen.add(full)
            candidates.append(SearchCandidate(title=title[:300], url=full))
            if len(candidates) >= 15:
                break
        if candidates:
            return candidates
        return super().parse_search_html(html, page_url=page_url, query=query)
