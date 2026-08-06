"""Weight Engine.

Chargeable weight is always max(actual weight, volumetric weight):

- actual weight: supplier value (SupplierOffer.weight_g_actual) has priority;
  falls back to the category norm from CalculationRule(rule_type='weight');
  packaging weight from the same rule is added on top;
- volumetric weight: the standard formula L×W×H (cm) / divisor (kg) when the
  offer has package dimensions, otherwise the category volumetric norm
  (default_volumetric_g) — both configured on the 'volumetric_weight' rule.

There is deliberately no single hardcoded default for all products: no rule
and no data means the weight is unknown and the scenario is blocked.
"""

from __future__ import annotations

from decimal import Decimal

from buying.engine.rule_handlers import get_handler
from buying.engine.rules import resolve_rule
from buying.models import CalculationRule, SupplierOffer


def resolve_chargeable_weight(
    offer: SupplierOffer, category: str, quantity: int = 1
) -> tuple[int | None, dict]:
    """Return (total chargeable weight in grams or None, provenance dict)."""
    scope = {
        'country': offer.supplier.country,
        'supplier': offer.supplier,
        'category': category,
    }

    # --- Actual weight per item (incl. packaging) -----------------------------
    packaging_g = 0
    weight_rule = resolve_rule(CalculationRule.RuleType.WEIGHT, **scope)
    if weight_rule:
        packaging_g = int(weight_rule.params.get('packaging_g', 0) or 0)

    actual_item_g = None
    actual_source = 'missing'
    actual_exact = False
    if offer.weight_g_actual:
        actual_item_g = offer.weight_g_actual + packaging_g
        actual_source = 'manual_entry' if offer.is_manual else 'parsed'
        actual_exact = True
    elif weight_rule and weight_rule.params.get('default_g'):
        actual_item_g = int(weight_rule.params['default_g']) + packaging_g
        actual_source = 'configured_rule'

    # --- Volumetric weight per item -------------------------------------------
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

    # --- Chargeable = max(actual, volumetric) ----------------------------------
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
