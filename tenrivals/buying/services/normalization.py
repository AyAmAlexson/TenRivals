"""Normalization service: free-form client request -> NormalizedProduct.

Flow: AI normalize → racquet enrichment (CanonicalProduct / RacquetSpecification)
→ staff review. Enrichment snapshots specs onto the product so later KB edits
do not rewrite historical requests.
"""

from __future__ import annotations

import logging

from django.db import transaction

from buying.ai.base import AIProvider, AIProviderError
from buying.ai.registry import get_ai_provider
from buying.models import BuyingRequest, NormalizedProduct
from buying.services.enrichment import (
    SOURCE_STAFF,
    apply_enrichment_to_product,
    enrich_racquet,
    enrichment_input_from_ai,
)

logger = logging.getLogger('buying')


@transaction.atomic
def normalize_buying_request(
    buying_request: BuyingRequest, provider: AIProvider | None = None
) -> NormalizedProduct:
    """Run AI normalization + tennis enrichment for a request and persist.

    Raises AIProviderError on provider failures; the caller decides how to
    surface it (the request itself is left untouched in that case).
    """
    provider = provider or get_ai_provider()
    result = provider.normalize(buying_request.original_query)
    data = result.data

    product = buying_request.normalized_product or NormalizedProduct()
    for key in (
        'brand', 'generation', 'category', 'gender', 'court', 'size', 'size_system',
        'grip_size', 'color', 'head_size', 'string_pattern', 'manufacturer_code',
        'ean', 'upc',
    ):
        setattr(product, key, data.get(key, '') or '')
    product.model_name = data.get('model', '') or ''
    product.variant = data.get('variant', '') or ''
    product.weight_g = _parse_weight_g(data.get('weight', ''))
    product.quantity = data.get('quantity', 1)
    product.required_attributes = data.get('required_attributes', [])
    product.optional_attributes = data.get('optional_attributes', [])
    product.uncertainties = data.get('uncertainties', [])
    product.aliases = data.get('aliases', [])
    product.raw_ai_output = result.raw_response
    product.prompt_version = result.prompt_version
    product.model_version = result.model_version
    product.edited_by_staff = False

    enrichment = enrich_racquet(
        enrichment_input_from_ai(data, original_query=buying_request.original_query)
    )
    apply_enrichment_to_product(product, enrichment)

    product.color_policy = (
        NormalizedProduct.ColorPolicy.REQUIRED
        if 'color' in (product.required_attributes or [])
        else NormalizedProduct.ColorPolicy.PREFERRED
        if 'color' in (product.optional_attributes or [])
        else NormalizedProduct.ColorPolicy.OPTIONAL
    )
    product.save()

    buying_request.normalized_product = product
    buying_request.quantity = product.quantity
    buying_request.status = BuyingRequest.Status.NORMALIZED
    buying_request.save(update_fields=['normalized_product', 'quantity', 'status', 'updated_at'])
    logger.info(
        'Normalized buying request #%s via %s (%s / %s); enrichment resolved=%s ambiguous=%s',
        buying_request.pk, provider.name, result.model_version, result.prompt_version,
        enrichment.resolved, enrichment.ambiguous,
    )
    return product


def mark_staff_field_sources(product: NormalizedProduct, changed_fields: list[str]) -> None:
    sources = dict(product.field_sources or {})
    for name in changed_fields:
        sources[name] = SOURCE_STAFF
    product.field_sources = sources


def _parse_weight_g(raw: str) -> int | None:
    """'300 g' / '300г' / '0.3 kg' -> grams. Unparseable values return None."""
    text = (raw or '').strip().lower().replace(',', '.')
    if not text:
        return None
    digits = ''
    for char in text:
        if char.isdigit() or char == '.':
            digits += char
        elif digits:
            break
    if not digits:
        return None
    try:
        value = float(digits)
    except ValueError:
        return None
    if 'kg' in text or 'кг' in text:
        value *= 1000
    return int(value) if value > 0 else None
