"""Fulfillment Engine: which routes can carry a given supplier offer.

A route applies when its origin country equals the supplier country, it is
enabled and inside its validity window, and its optional supplier/category
restrictions match. Supplier/category-specific routes are more specific and
therefore ordered before generic ones.

UNSUPPORTED suppliers never receive Onex CostScenarios. MANUAL_REVIEW suppliers
get provisional scenarios for staff display but are excluded from auto-rank.
"""

from __future__ import annotations

import datetime

from django.db.models import Q

from buying.models import FulfillmentRoute, OnexApplicability, Supplier


def supplier_supports_onex(supplier: Supplier) -> bool:
    """True when Onex is confirmed for automatic ranking / recommendation."""
    return getattr(supplier, 'onex_applicability', OnexApplicability.SUPPORTED) == (
        OnexApplicability.SUPPORTED
    )


def supplier_allows_onex_scenarios(supplier: Supplier) -> bool:
    """True when CostScenarios may be built (supported or pending staff review)."""
    value = getattr(supplier, 'onex_applicability', OnexApplicability.SUPPORTED)
    return value in (OnexApplicability.SUPPORTED, OnexApplicability.MANUAL_REVIEW)


def applicable_routes(supplier: Supplier, category: str = '') -> list[FulfillmentRoute]:
    if not supplier_allows_onex_scenarios(supplier):
        return []

    today = datetime.date.today()
    qs = (
        FulfillmentRoute.objects.filter(
            enabled=True,
            provider__enabled=True,
            warehouse__enabled=True,
            origin_country=supplier.country,
        )
        .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today))
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
        .filter(Q(supplier__isnull=True) | Q(supplier=supplier))
        .filter(Q(category='') | Q(category=category))
        .select_related('provider', 'warehouse')
    )
    routes = list(qs)
    routes.sort(
        key=lambda r: (bool(r.supplier_id), bool(r.category), r.priority),
        reverse=True,
    )
    return routes
