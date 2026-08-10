"""Connector for midwest-racquet-sports (Shopify).

Storefront sits behind AWS WAF / CloudFront (`x-amzn-waf-action: challenge`).
Automated HTTP gets HTTP 202 empty bodies for suggest.json / products.json /
HTML. ConnectorHttpClient raises CaptchaDetected so runs are not mislabeled
as search_empty; manual offer entry remains the fallback until browser/proxy.
"""

from buying.connectors.base import NormalizedProductQuery
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector
from buying.connectors.shopify import search_shopify


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'midwest-racquet-sports'
    parser_version = 'phase3-3'
    base_url = 'https://www.midwestracquetsports.com/'
    search_path_template = '/search?q={query}'
    default_currency = 'USD'
    default_tax_mode = 'sales_tax_at_checkout'
    default_destination_country = 'US'

    def search(self, query: NormalizedProductQuery):
        phrase = (query.search_phrases or ['tennis'])[0]
        found = search_shopify(self._client(), base_url=self.base_url, phrase=phrase)
        return found or super().search(query)
