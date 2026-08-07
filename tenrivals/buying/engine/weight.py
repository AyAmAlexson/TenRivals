"""Shipping Weight Engine.

Product specification weight (e.g. racquet 300 g unstrung) is NEVER used as
the Onex / international shipping weight. Those are separate concepts:

- product_spec_g — identity / matching only (NormalizedProduct, canonical)
- chargeable shipping weight — logistics only

Chargeable shipping weight priority:

1. actual parcel/shipping weight on the offer (parsed or manual override);
2. historical confirmed shipment weight (ProductMapping.confirmed_weight_g);
3. canonical shipping weight (enrichment_snapshot.shipping_weight_g);
4. category shipping-weight rule (CalculationRule weight.default_g);
5. (manual is covered by #1 when offer.is_manual / weight_g_actual set).

Then chargeable = max(shipping_actual, volumetric) when dimensions exist.

Missing all shipping sources → None → pricing blocks with missing_weight.
"""

from __future__ import annotations

from decimal import Decimal

from buying.engine.rule_handlers import get_handler
from buying.engine.rules import resolve_rule
from buying.models import CalculationRule, ProductMapping, SupplierOffer

# Provenance labels stored in calculation_details.weight
SOURCE_PARSED = 'parsed'
SOURCE_HISTORICAL = 'historical'
SOURCE_CANONICAL_SHIPPING = 'canonical_shipping'
SOURCE_CONFIGURED_RULE = 'configured_rule'
SOURCE_MANUAL = 'manual_override'


def resolve_chargeable_weight(
    offer: SupplierOffer, category: str, quantity: int = 1
) -> tuple[int | None, dict]:
    """Return (total chargeable shipping weight in grams or None, provenance)."""
    scope = {
        'country': offer.supplier.country,
        'supplier': offer.supplier,
        'category': category,
    }

    weight_rule = resolve_rule(CalculationRule.RuleType.WEIGHT, **scope)
    packaging_g = 0
    if weight_rule:
        packaging_g = int(weight_rule.params.get('packaging_g', 0) or 0)

    product_spec_g = _product_specification_weight_g(offer)
    shipping_item_g, shipping_source, shipping_exact = _resolve_shipping_weight_g(
        offer, weight_rule
    )

    # Optional packaging add-on only when shipping weight came from a non-rule
    # source that may exclude packaging (parsed / historical / canonical).
    if (
        shipping_item_g is not None
        and packaging_g
        and shipping_source in (
            SOURCE_PARSED, SOURCE_HISTORICAL, SOURCE_CANONICAL_SHIPPING, SOURCE_MANUAL
        )
    ):
        shipping_item_g = int(shipping_item_g) + packaging_g

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

    shipping_total_g = shipping_item_g * quantity if shipping_item_g is not None else None
    volumetric_total_g = volumetric_item_g * quantity if volumetric_item_g is not None else None

    if shipping_total_g is None and volumetric_total_g is None:
        chargeable_g, basis, exact = None, None, False
    elif volumetric_total_g is None or (
        shipping_total_g is not None and shipping_total_g >= volumetric_total_g
    ):
        chargeable_g, basis, exact = shipping_total_g, 'shipping', shipping_exact
    else:
        chargeable_g, basis, exact = volumetric_total_g, 'volumetric', volumetric_exact

    meta = {
        # Product identity weight — never used for Onex tariff
        'product_spec_g': product_spec_g,
        'product_g': product_spec_g,  # backward-compatible alias for older UI
        # Shipping / chargeable
        'shipping_item_g': shipping_item_g,
        'shipping_source': shipping_source,
        'shipping_exact': shipping_exact,
        'actual_item_g': shipping_item_g,  # alias used by older breakdown consumers
        'actual_source': shipping_source,
        'actual_exact': shipping_exact,
        'packaging_g': packaging_g,
        'volumetric_item_g': volumetric_item_g,
        'volumetric_source': volumetric_source,
        'actual_total_g': shipping_total_g,
        'shipping_total_g': shipping_total_g,
        'volumetric_total_g': volumetric_total_g,
        'chargeable_g': chargeable_g,
        'chargeable_shipping_weight_g': chargeable_g,
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


def _product_specification_weight_g(offer: SupplierOffer) -> int | None:
    """Racquet unstrung weight etc. — identity only, never for shipping."""
    np = getattr(offer.buying_request, 'normalized_product', None)
    if np is not None and np.weight_g:
        return int(np.weight_g)
    snap = (getattr(np, 'enrichment_snapshot', None) or {}) if np is not None else {}
    if snap.get('weight_g_unstrung'):
        try:
            return int(snap['weight_g_unstrung'])
        except (TypeError, ValueError):
            pass
    return None


def _resolve_shipping_weight_g(
    offer: SupplierOffer, weight_rule: CalculationRule | None
) -> tuple[int | None, str, bool]:
    """Return (parcel grams before optional packaging, source, exact)."""
    # 1 / 5. Offer parcel weight (parsed connector or staff manual override)
    if offer.weight_g_actual:
        if offer.is_manual:
            return int(offer.weight_g_actual), SOURCE_MANUAL, True
        return int(offer.weight_g_actual), SOURCE_PARSED, True

    # 2. Historical confirmed shipment weight for this supplier product
    historical = _mapping_confirmed_shipping_weight(offer)
    if historical:
        return historical, SOURCE_HISTORICAL, True

    # 3. Canonical shipping weight (not product unstrung grams)
    canonical_ship = _canonical_shipping_weight_g(offer)
    if canonical_ship:
        return canonical_ship, SOURCE_CANONICAL_SHIPPING, True

    # 4. Category shipping-weight rule (full parcel estimate in default_g)
    if weight_rule and weight_rule.params.get('default_g'):
        return int(weight_rule.params['default_g']), SOURCE_CONFIGURED_RULE, False

    return None, 'missing', False


def _canonical_shipping_weight_g(offer: SupplierOffer) -> int | None:
    np = getattr(offer.buying_request, 'normalized_product', None)
    if np is None:
        return None
    snap = np.enrichment_snapshot or {}
    for key in ('shipping_weight_g', 'parcel_weight_g', 'chargeable_shipping_weight_g'):
        val = snap.get(key)
        if val:
            try:
                return int(val)
            except (TypeError, ValueError):
                continue
    return None


def _mapping_confirmed_shipping_weight(offer: SupplierOffer) -> int | None:
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
