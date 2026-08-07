"""Connector for ole-tennis (Shopify)."""

from buying.connectors.base import NormalizedProductQuery, OfferData, SearchCandidate
from buying.connectors.generic import HtmlJsonLdConnector
from buying.connectors.registry import register_connector
from buying.connectors.shopify import (
    fetch_shopify_product,
    search_shopify,
    shopify_variant_labels,
)


@register_connector
class Connector(HtmlJsonLdConnector):
    code = 'ole-tennis'
    parser_version = 'phase3-3'
    base_url = 'https://oletennis.com/'
    search_path_template = '/search?q={query}'
    default_currency = 'USD'
    default_tax_mode = 'sales_tax_at_checkout'
    default_destination_country = 'US'

    def search(self, query: NormalizedProductQuery):
        phrase = (query.search_phrases or ['tennis'])[0]
        found = search_shopify(self._client(), base_url=self.base_url, phrase=phrase)
        return found or super().search(query)

    def get_product_details(self, candidate: SearchCandidate, query: NormalizedProductQuery) -> OfferData:
        offer = super().get_product_details(candidate, query)
        product = fetch_shopify_product(self._client(), product_url=candidate.url)
        if product:
            labels = shopify_variant_labels(product)
            if labels:
                offer.available_variants = labels
            desc = str(product.get('description') or '')
            body = ' '.join(
                filter(None, [
                    str(product.get('title') or ''),
                    desc,
                    ' '.join(v.get('label', '') for v in labels),
                ])
            )
            payload = dict(offer.raw_payload or {})
            payload['shopify_product'] = {
                'handle': product.get('handle'),
                'id': product.get('id'),
                'variants': labels,
            }
            payload['page_text'] = (payload.get('page_text') or '') + ' ' + body
            offer.raw_payload = payload
            # Clear generic warning; grip verification runs in search orchestration
            offer.warnings = [
                w for w in (offer.warnings or [])
                if 'variant availability not verified' not in (w or '').lower()
            ]
        return offer
