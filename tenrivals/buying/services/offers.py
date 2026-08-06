"""Offer lifecycle: versioned offer snapshots and their cost scenarios.

SupplierOffer and CostScenario are immutable snapshots:
- editing an offer creates a NEW offer version (revise_offer); the old row and
  its scenarios keep their original values;
- recalculating scenarios never rewrites old ones — they are marked superseded
  and linked to their successors via superseded_by.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction

from buying.engine.pricing import CALCULATION_VERSION, PricingError, build_cost_scenario
from buying.engine.routes import applicable_routes
from buying.models import CostScenario, SupplierOffer

logger = logging.getLogger('buying')

# Scenario statuses that take part in the "current" lifecycle and get
# superseded on recalculation.
_ACTIVE_STATUSES = (
    CostScenario.Status.CALCULATED,
    CostScenario.Status.CALCULATION_BLOCKED,
)


@transaction.atomic
def rebuild_scenarios_for_offer(offer: SupplierOffer) -> list[CostScenario]:
    """Create a current_single_item CostScenario per applicable route.

    Previous active scenarios of the offer are marked superseded and linked to
    their successors (history is kept; ranking only considers current rows).
    Manual overrides recorded on superseded scenarios are re-applied to their
    successors by component code — a recalculation never destroys an override.
    """
    previous = list(offer.cost_scenarios.filter(status__in=_ACTIVE_STATUSES))
    overrides_by_route: dict[int, dict[str, Decimal]] = {}
    previous_by_route: dict[int, CostScenario] = {}
    for scenario in previous:
        previous_by_route[scenario.fulfillment_route_id] = scenario
        stored = scenario.calculation_details.get('overrides') or {}
        if stored:
            overrides_by_route[scenario.fulfillment_route_id] = {
                code: Decimal(value) for code, value in stored.items()
            }

    category = (
        offer.buying_request.normalized_product.category
        if offer.buying_request.normalized_product_id
        else ''
    )
    routes = applicable_routes(offer.supplier, category=category)
    scenarios: list[CostScenario] = []
    for route in routes:
        scenario = _build_or_blocked(
            offer, route, overrides=overrides_by_route.get(route.pk)
        )
        scenario.save()
        scenarios.append(scenario)
        old = previous_by_route.pop(route.pk, None)
        if old:
            _supersede(old, scenario)
    # Routes that no longer apply: supersede without a successor.
    for old in previous_by_route.values():
        _supersede(old, None)

    if not routes:
        logger.warning(
            'No applicable fulfillment routes for offer #%s (supplier country %s)',
            offer.pk, offer.supplier.country,
        )
    rank_request_scenarios(offer.buying_request_id)
    for scenario in scenarios:
        scenario.refresh_from_db()
    return scenarios


def _build_or_blocked(offer, route, *, overrides=None, scenario_type=None) -> CostScenario:
    kwargs = {'quantity': offer.buying_request.quantity, 'overrides': overrides}
    if scenario_type:
        kwargs['scenario_type'] = scenario_type
    try:
        return build_cost_scenario(offer, route, **kwargs)
    except PricingError as exc:
        logger.warning('Pricing blocked for offer #%s via route #%s: %s', offer.pk, route.pk, exc)
        return CostScenario(
            buying_request=offer.buying_request,
            supplier_offer=offer,
            fulfillment_route=route,
            status=CostScenario.Status.CALCULATION_BLOCKED,
            calculation_version=CALCULATION_VERSION,
            calculation_details={'blocking_issues': [str(exc)]},
            warnings=[f'blocking:{exc}'],
        )


def _supersede(old: CostScenario, successor: CostScenario | None) -> None:
    CostScenario.objects.filter(pk=old.pk).update(
        status=CostScenario.Status.SUPERSEDED,
        superseded_by=successor,
        rank=None,
    )


@transaction.atomic
def revise_offer(old_offer: SupplierOffer, new_offer: SupplierOffer) -> SupplierOffer:
    """Persist an edited offer as a NEW version-snapshot.

    `new_offer` is an unsaved instance carrying the edited field values (e.g.
    from a ModelForm with commit=False). The old row is left untouched except
    for the superseded_by link; its scenarios are superseded, and fresh
    scenarios (with carried-over overrides) are built for the new version.
    """
    old_overrides: dict[int, dict[str, Decimal]] = {}
    for scenario in old_offer.cost_scenarios.filter(status__in=_ACTIVE_STATUSES):
        stored = scenario.calculation_details.get('overrides') or {}
        if stored:
            old_overrides[scenario.fulfillment_route_id] = {
                code: Decimal(value) for code, value in stored.items()
            }

    new_offer.pk = None
    new_offer.id = None
    new_offer._state.adding = True
    new_offer.version = old_offer.version + 1
    new_offer.superseded_by = None
    new_offer.save()

    SupplierOffer.objects.filter(pk=old_offer.pk).update(superseded_by=new_offer)
    for scenario in old_offer.cost_scenarios.filter(status__in=_ACTIVE_STATUSES):
        _supersede(scenario, None)

    # Build scenarios for the new version, carrying overrides route-by-route.
    category = (
        new_offer.buying_request.normalized_product.category
        if new_offer.buying_request.normalized_product_id
        else ''
    )
    for route in applicable_routes(new_offer.supplier, category=category):
        scenario = _build_or_blocked(
            new_offer, route, overrides=old_overrides.get(route.pk)
        )
        scenario.save()
    rank_request_scenarios(new_offer.buying_request_id)
    return new_offer


def rank_request_scenarios(buying_request_id: int) -> None:
    """Two-dimensional ranking.

    rank: ordinal by customer_price among calculated, non-hidden scenarios —
    "which is cheapest". recommendation: independent quality verdict
    (availability, match status, confidence, warnings) — "which should we pick".
    The cheapest option does not automatically get 'Recommended'.
    """
    scenarios = list(
        CostScenario.objects.filter(
            buying_request_id=buying_request_id,
            status=CostScenario.Status.CALCULATED,
            is_hidden=False,
        ).select_related('supplier_offer')
    )
    scenarios.sort(key=lambda s: s.customer_price)
    for index, scenario in enumerate(scenarios, start=1):
        labels = _price_labels(scenario, is_cheapest=index == 1)
        recommendation, reasons = _recommendation(scenario)
        CostScenario.objects.filter(pk=scenario.pk).update(
            rank=index,
            rank_labels=labels,
            recommendation=recommendation,
            recommendation_reasons=reasons,
        )

    # Blocked scenarios get an explicit verdict too, but never a rank.
    blocked = CostScenario.objects.filter(
        buying_request_id=buying_request_id,
        status=CostScenario.Status.CALCULATION_BLOCKED,
        is_hidden=False,
    )
    for scenario in blocked:
        CostScenario.objects.filter(pk=scenario.pk).update(
            rank=None,
            recommendation=CostScenario.Recommendation.REVIEW,
            recommendation_reasons=['Calculation blocked — required rules are missing'],
        )


def _price_labels(scenario: CostScenario, *, is_cheapest: bool) -> list[str]:
    labels = []
    if is_cheapest:
        labels.append('Lowest price')
    if scenario.calculation_details.get('manual_price'):
        labels.append('Manual price')
    match_labels = {
        'exact': 'Exact match',
        'alternative_color': 'Alternative color',
        'alternative_version': 'Alternative version',
        'manual_review': 'Manual review',
    }
    label = match_labels.get(scenario.supplier_offer.match_status)
    if label:
        labels.append(label)
    if 'estimated:local_tax' in scenario.warnings or 'missing_rule:local_tax' in scenario.warnings:
        labels.append('Estimated tax')
    if 'estimated:local_shipping' in scenario.warnings or 'missing_rule:local_shipping' in scenario.warnings:
        labels.append('Estimated shipping')
    if scenario.supplier_offer.requested_variant_available is False:
        labels.append('Unavailable')
    return labels


def _recommendation(scenario: CostScenario) -> tuple[str, list[str]]:
    """Deterministic Phase 1 verdict; reasons are shown in the UI so staff can
    see both why an option is cheap and why it is (not) recommended."""
    offer = scenario.supplier_offer
    reasons: list[str] = []

    if offer.requested_variant_available is False:
        return CostScenario.Recommendation.NOT_RECOMMENDED, ['Requested variant is not available']
    if getattr(offer, 'purchase_context_status', '') == 'purchase_context_unconfirmed':
        return CostScenario.Recommendation.NOT_RECOMMENDED, ['Destination not confirmed']
    if any('Onex route unsupported' in (w or '') for w in (offer.warnings or [])):
        return CostScenario.Recommendation.NOT_RECOMMENDED, ['Onex route unsupported']
    if offer.match_status == 'manual_review':
        reasons.append('Product match needs manual review')
    if offer.match_status in ('alternative_color', 'alternative_version'):
        reasons.append(f'Offer is an alternative ({offer.get_match_status_display().lower()})')
    if scenario.confidence == CostScenario.Confidence.ESTIMATED:
        reasons.append('Price is estimated — some components lack exact data')
    if scenario.calculation_details.get('manual_price'):
        reasons.append('Final price was set manually')
    if any(w.startswith('rule_conflict:') for w in scenario.warnings):
        reasons.append('Conflicting pricing rules were tie-broken — review the rules')
    if any('stale' in (w or '').lower() for w in (offer.warnings or [])):
        reasons.append('Offer freshness not confirmed (stale search cache)')
    if any('Authenticated session expired' in (w or '') for w in (offer.warnings or [])):
        reasons.append('Authenticated session expired')
    if any('Promotion shown but not confirmed' in (w or '') for w in (offer.warnings or [])):
        reasons.append('Promotion shown but not confirmed')

    if reasons:
        return CostScenario.Recommendation.REVIEW, reasons
    return CostScenario.Recommendation.RECOMMENDED, ['Verified offer with exact or near-exact costing']
