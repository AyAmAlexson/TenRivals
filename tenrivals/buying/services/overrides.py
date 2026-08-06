"""Manual overrides with a full audit trail (old value, new value, user, reason).

Overrides never mutate an existing CostScenario: applying or removing one
creates a NEW scenario version and marks the old one superseded, preserving the
original numbers. Overrides survive recalculations (they are carried over in
calculation_details['overrides']) and can be undone, which restores the
automatic logic for that component.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from buying.engine.pricing import build_cost_scenario
from buying.models import CostScenario, ManualOverride, SupplierOffer
from buying.services.offers import rank_request_scenarios


def _current_overrides(scenario: CostScenario) -> dict[str, Decimal]:
    return {
        code: Decimal(value)
        for code, value in (scenario.calculation_details.get('overrides') or {}).items()
    }


def _replace_with_new_version(scenario: CostScenario, overrides: dict[str, Decimal]) -> CostScenario:
    """Build a successor scenario with the given overrides; supersede the old one."""
    successor = build_cost_scenario(
        scenario.supplier_offer,
        scenario.fulfillment_route,
        quantity=scenario.buying_request.quantity,
        scenario_type=scenario.scenario_type,
        overrides=overrides or None,
    )
    successor.is_hidden = scenario.is_hidden
    successor.save()
    CostScenario.objects.filter(pk=scenario.pk).update(
        status=CostScenario.Status.SUPERSEDED,
        superseded_by=successor,
        rank=None,
    )
    rank_request_scenarios(scenario.buying_request_id)
    successor.refresh_from_db()
    return successor


@transaction.atomic
def override_scenario_component(
    scenario: CostScenario, component_code: str, new_amount: Decimal, user, reason: str
) -> CostScenario:
    """Override one cost component (GEL). Returns the NEW scenario version.

    The original automatic value is kept in the audit trail and in the
    component's auto_amount_gel; a final-price override additionally marks the
    scenario as manual and keeps the computed price for side-by-side display.
    """
    overrides = _current_overrides(scenario)
    old_amount = overrides.get(component_code)
    if old_amount is None:
        old_amount = getattr(scenario, component_code, None)
    overrides[component_code] = new_amount

    successor = _replace_with_new_version(scenario, overrides)
    ManualOverride.objects.create(
        content_type=ContentType.objects.get_for_model(CostScenario),
        object_id=successor.pk,
        kind=(
            ManualOverride.Kind.FINAL_PRICE
            if component_code == 'customer_price'
            else ManualOverride.Kind.COMPONENT
        ),
        field=component_code,
        old_value=str(old_amount) if old_amount is not None else None,
        new_value=str(new_amount),
        user=user,
        reason=reason,
    )
    return successor


@transaction.atomic
def remove_scenario_override(
    scenario: CostScenario, component_code: str, user, reason: str
) -> CostScenario:
    """Undo one override: the successor scenario is computed with automatic
    logic for that component again. Returns the NEW scenario version."""
    overrides = _current_overrides(scenario)
    old_amount = overrides.pop(component_code, None)

    successor = _replace_with_new_version(scenario, overrides)
    ManualOverride.objects.create(
        content_type=ContentType.objects.get_for_model(CostScenario),
        object_id=successor.pk,
        kind=ManualOverride.Kind.REMOVAL,
        field=component_code,
        old_value=str(old_amount) if old_amount is not None else None,
        new_value=None,
        user=user,
        reason=reason,
    )
    return successor


OVERRIDABLE_COMPONENTS = [
    ('item_cost', 'Product price'),
    ('local_shipping', 'Local shipping'),
    ('local_tax', 'Local tax'),
    ('payment_fee', 'Payment fee'),
    ('international_shipping', 'International shipping'),
    ('insurance', 'Insurance'),
    ('customs', 'Customs declaration fee'),
    ('declaration_service', 'Declaration service'),
    ('georgia_vat', 'Import VAT'),
    ('fx_buffer', 'FX buffer'),
    ('risk_reserve', 'Risk reserve'),
    ('handling_cost', 'Handling'),
    ('customer_price', 'Customer price'),
]


@transaction.atomic
def audit_offer_changes(offer: SupplierOffer, changed: dict[str, tuple], user, reason: str) -> None:
    """Record audited field changes that produced a new offer version.

    `changed` maps field name -> (old_value, new_value); values are stored as-is
    in the JSON audit columns. Records attach to the NEW offer version.
    """
    content_type = ContentType.objects.get_for_model(SupplierOffer)
    for field, (old_value, new_value) in changed.items():
        ManualOverride.objects.create(
            content_type=content_type,
            object_id=offer.pk,
            kind=ManualOverride.Kind.OFFER_FIELD,
            field=field,
            old_value=_jsonable(old_value),
            new_value=_jsonable(new_value),
            user=user,
            reason=reason,
        )


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


def scenario_overrides(scenario: CostScenario) -> dict[str, str]:
    return scenario.calculation_details.get('overrides') or {}
