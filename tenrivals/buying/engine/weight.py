"""Weight Engine.

Chargeable weight is always max(actual weight, volumetric weight).

Actual-weight source priority (product body before packaging):

1. exact weight parsed from supplier product/variant (SupplierOffer.weight_g_actual);
2. confirmed weight from ProductMapping;
3. weight from NormalizedProduct;
4. weight from canonical enrichment / RacquetSpecification / CanonicalProduct link;
5. configured category estimate (CalculationRule weight.default_g);
6. manual override (offer.weight_g_actual when is_manual — already covered by #1).

Packaging grams from the weight rule are added on top of whichever product
weight was chosen. Missing all sources → None → pricing blocks with missing_weight.
"""

from __future__ import annotations

from decimal import Decimal

from buying.engine.rule_handlers import get_handler
from buying.engine.rules import resolve_rule
from buying.models import CalculationRule, ProductMapping, SupplierOffer


def resolve_chargeable_weight(
    offer: SupplierOffer, category: str, quantity: int = 1
) -> tuple[int | None, dict]:
    """Return (total chargeable weight in grams or None, provenance dict)."""
    scope = {
        'country': offer.supplier.country,
        'supplier': offer.supplier,
        'category': category,
    }

    packaging_g = 0
    weight_rule = resolve_rule(CalculationRule.RuleType.WEIGHT, **scope)
    if weight_rule:
        packaging_g = int(weight_rule.params.get('packaging_g', 0) or 0)

    product_g, actual_source, actual_exact = _resolve_product_weight_g(offer, weight_rule)

    actual_item_g = None
    if product_g is not None:
        actual_item_g = int(product_g) + packaging_g

    volumetric_rule = resolve_rule(CalculationRule.RuleType.VOLUMETRIC_WEIGHT, **scope)
    volumetric_item_g = None
    volumetric_source = None
    volumetric_exact = False
    if volumetric_rule:
        handler = get_handler(CalculationRule.RuleType.VOLUMETRIC_WEIGHT)
        divisor = handler.divisor(volumetric_rule.params)
        dims = (offer.length_cm, offer.width_cm, offer.height_cm)
        if divisor and all(d for d in dims):
            volume = Decimal(offer.length_cm) * Decimal(offer.width_cm) * Decimal(offer.height_cm)
            volumetric_item_g = int(volume / divisor * 1000)
            volumetric_source = 'dimensions'
            volumetric_exact = True
        elif volumetric_rule.params.get('default_volumetric_g'):
            volumetric_item_g = int(volumetric_rule.params['default_volumetric_g'])
            volumetric_source = 'configured_rule'

    actual_total_g = actual_item_g * quantity if actual_item_g is not None else None
    volumetric_total_g = volumetric_item_g * quantity if volumetric_item_g is not None else None

    if actual_total_g is None and volumetric_total_g is None:
        chargeable_g, basis, exact = None, None, False
    elif volumetric_total_g is None or (
        actual_total_g is not None and actual_total_g >= volumetric_total_g
    ):
        chargeable_g, basis, exact = actual_total_g, 'actual', actual_exact
    else:
        chargeable_g, basis, exact = volumetric_total_g, 'volumetric', volumetric_exact

    meta = {
        'product_g': product_g,
        'actual_item_g': actual_item_g,
        'actual_source': actual_source,
        'actual_exact': actual_exact,
        'packaging_g': packaging_g,
        'volumetric_item_g': volumetric_item_g,
        'volumetric_source': volumetric_source,
        'actual_total_g': actual_total_g,
        'volumetric_total_g': volumetric_total_g,
        'chargeable_g': chargeable_g,
        'basis': basis,
        'quantity': quantity,
        'exact': exact,
        'weight_rule': (
            {'id': weight_rule.pk, 'version': weight_rule.version, 'params': dict(weight_rule.params)}
            if weight_rule else None
        ),
        'volumetric_rule': (
            {
                'id': volumetric_rule.pk,
                'version': volumetric_rule.version,
                'params': dict(volumetric_rule.params),
            }
            if volumetric_rule else None
        ),
    }
    return chargeable_g, meta


def _resolve_product_weight_g(
    offer: SupplierOffer, weight_rule: CalculationRule | None
) -> tuple[int | None, str, bool]:
    """Return (grams before packaging, source label, exact flag)."""
    if offer.weight_g_actual:
        source = 'manual_entry' if offer.is_manual else 'parsed'
        return int(offer.weight_g_actual), source, True

    mapping_weight = _mapping_confirmed_weight(offer)
    if mapping_weight:
        return mapping_weight, 'product_mapping', True

    np = getattr(offer.buying_request, 'normalized_product', None)
    if np is not None and np.weight_g:
        sources = np.field_sources or {}
        if sources.get('weight_g') == 'canonical' or (np.enrichment_snapshot or {}).get(
            'weight_g_unstrung'
        ):
            return int(np.weight_g), 'canonical_product', True
        return int(np.weight_g), 'normalized_product', True

    snap = (getattr(np, 'enrichment_snapshot', None) or {}) if np is not None else {}
    if snap.get('weight_g_unstrung'):
        try:
            return int(snap['weight_g_unstrung']), 'canonical_product', True
        except (TypeError, ValueError):
            pass

    if weight_rule and weight_rule.params.get('default_g'):
        return int(weight_rule.params['default_g']), 'configured_rule', False

    return None, 'missing', False


def _mapping_confirmed_weight(offer: SupplierOffer) -> int | None:
    qs = ProductMapping.objects.filter(
        supplier_id=offer.supplier_id,
        confirmed_weight_g__isnull=False,
        mapping_status=ProductMapping.MappingStatus.CONFIRMED,
    )
    if offer.product_url:
        hit = qs.filter(supplier_product_url=offer.product_url).first()
        if hit:
            return int(hit.confirmed_weight_g)
    if offer.supplier_sku:
        hit = qs.filter(supplier_sku=offer.supplier_sku).first()
        if hit:
            return int(hit.confirmed_weight_g)
    return None
