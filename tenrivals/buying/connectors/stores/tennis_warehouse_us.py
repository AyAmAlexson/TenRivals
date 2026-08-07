"""Connector for tennis-warehouse-us.

Attempts browser-like HTML search. Many datacenter IPs still get HTTP 403 —
manual offer entry remains the fallback when anti-bot blocks the request.
"""

from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from buying.connectors.base import NormalizedProductQuery, SearchCandidate
from buying.connectors.exceptions import CaptchaDetected
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector

BROWSER_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
)


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'tennis-warehouse-us'
    parser_version = 'phase3-3'
    base_url = 'https://www.tennis-warehouse.com/'
    search_path_template = '/search/Search.aspx?searchtext={query}'
    default_currency = 'USD'
    default_tax_mode = 'sales_tax_at_checkout'
    default_destination_country = 'US'

    def search(self, query: NormalizedProductQuery):
        client = self._client()
        phrase = (query.search_phrases or ['tennis'])[0]
        headers = {
            'User-Agent': BROWSER_UA,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': self.base_url,
        }
        for path in (
            f'search/Search.aspx?searchtext={quote_plus(phrase)}',
            f'search?searchtext={quote_plus(phrase)}',
            f'cgi-bin/search?searchtext={quote_plus(phrase)}',
        ):
            url = urljoin(self.base_url, path)
            response = client.get(url, headers=headers)
            if response.status_code == 403:
                raise CaptchaDetected(
                    'Tennis Warehouse US blocked automated access (HTTP 403). '
                    'Use manual offer entry until browser/proxy access is available.'
                )
            if response.status_code >= 400:
                continue
            found = self.parse_search_html(
                response.text, page_url=str(response.url), query=query
            )
            if found:
                return found
            # Broader parse for TW markup (descpage / .html product links)
            soup = BeautifulSoup(response.text, 'html.parser')
            extra: list[SearchCandidate] = []
            seen: set[str] = set()
            for a in soup.select('a[href]'):
                href = (a.get('href') or '').lower()
                title = a.get_text(' ', strip=True)
                if len(title) < 8:
                    continue
                if (
                    'descpage' not in href
                    and '/product' not in href
                    and not href.rstrip('/').endswith('.html')
                ):
                    continue
                full = urljoin(str(response.url), a['href'])
                if full in seen:
                    continue
                seen.add(full)
                extra.append(SearchCandidate(title=title[:300], url=full))
                if len(extra) >= 15:
                    break
            if extra:
                return extra
        return []
