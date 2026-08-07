"""Shared factories for buying tests."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from buying.models import (
    BuyingRequest,
    CalculationRule,
    FulfillmentProvider,
    FulfillmentRoute,
    FulfillmentWarehouse,
    Supplier,
    SupplierOffer,
)


def make_superuser(email='boss@tenrivals.test'):
    return get_user_model().objects.create_superuser(email=email, password='x')


def make_supplier(**kwargs):
    defaults = dict(
        name='Tennis Warehouse US',
        code='tw-us',
        base_url='https://example.com',
        country='US',
        currency='USD',
        tax_display_mode='prices_include_vat',
        onex_applicability='supported',
        default_destination_country='US',
    )
    defaults.update(kwargs)
    return Supplier.objects.create(**defaults)


def make_route(supplier_country='US', **kwargs):
    provider, _ = FulfillmentProvider.objects.get_or_create(
        code='onex', defaults={'name': 'Onex'}
    )
    warehouse, _ = FulfillmentWarehouse.objects.get_or_create(
        provider=provider,
        country=supplier_country,
        defaults={'currency': 'USD' if supplier_country == 'US' else 'EUR'},
    )
    defaults = dict(
        name=f'{supplier_country} shops → Onex → Georgia',
        provider=provider,
        warehouse=warehouse,
        origin_country=supplier_country,
        estimated_min_days=10,
        estimated_max_days=20,
    )
    defaults.update(kwargs)
    return FulfillmentRoute.objects.create(**defaults)


def make_request(user, query='Wilson Blade 100 V10, 300 g, grip 3'):
    return BuyingRequest.objects.create(original_query=query, staff_user=user)


def make_offer(buying_request, supplier, **kwargs):
    from buying.models import ConfirmationState, OfferEligibility

    defaults = dict(
        title='Wilson Blade 100 V10',
        current_price=Decimal('100.00'),
        currency='USD',
        is_manual=True,
        checked_at=timezone.now(),
        local_shipping_cost=Decimal('12.00'),
        local_shipping_source='parsed',
        tax_display_mode='prices_include_vat',
        weight_g_actual=1000,
        match_status='exact',
        requested_variant_available=True,
        destination_selection_confirmed=True,
        purchase_context_status='confirmed',
        purchase_context_confirmed=ConfirmationState.CONFIRMED,
        product_identity_confirmed=ConfirmationState.CONFIRMED,
        requested_variant_confirmed=ConfirmationState.CONFIRMED,
        eligibility_status=OfferEligibility.VERIFIED,
    )
    defaults.update(kwargs)
    return SupplierOffer.objects.create(
        buying_request=buying_request, supplier=supplier, **defaults
    )


def make_standard_rules(route):
    """A coherent Phase 2 rule set used by pricing tests.

    TEST FIXTURES ONLY: rates, thresholds and tariffs here exercise the engine
    mechanics. Production values are configured separately as CalculationRules
    (see the seed_onex management command)."""
    CalculationRule.objects.create(
        rule_type=CalculationRule.RuleType.FX_RATE,
        name='USD manual rate',
        params={'currency': 'USD', 'rate_gel': '2.70'},
    )
    CalculationRule.objects.create(
        rule_type=CalculationRule.RuleType.INTERNATIONAL_SHIPPING,
        name='Onex delivery',
        provider=route.provider,
        params={'currency': 'GEL', 'per_kg': '27'},
    )
    CalculationRule.objects.create(
        rule_type=CalculationRule.RuleType.PAYMENT_FEE,
        name='Onex payment fee',
        provider=route.provider,
        params={'rate': '0.02', 'base': 'international_shipping'},
    )
    CalculationRule.objects.create(
        rule_type=CalculationRule.RuleType.GEORGIA_VAT,
        name='Import VAT',
        params={
            'rate': '0.18', 'threshold_gel': '300',
            'threshold_base': 'local_cost', 'base': 'local_cost_plus_intl_plus_fee',
        },
    )
    CalculationRule.objects.create(
        rule_type=CalculationRule.RuleType.CUSTOMS,
        name='Customs declaration fee',
        params={'fixed_gel': '20', 'applies': 'when_declared'},
    )
    CalculationRule.objects.create(
        rule_type=CalculationRule.RuleType.DECLARATION_SERVICE,
        name='Onex declaration service',
        provider=route.provider,
        params={'fixed_gel': '15'},
    )
    CalculationRule.objects.create(
        rule_type=CalculationRule.RuleType.SALES_VAT,
        name='Sales VAT',
        params={'rate': '0.18', 'mode': 'included_in_sale_price'},
    )
    CalculationRule.objects.create(
        rule_type=CalculationRule.RuleType.SMALL_BUSINESS_TAX,
        name='Small business tax',
        params={'rate': '0.01', 'mode': 'percentage_of_gross_sale_price'},
    )
    CalculationRule.objects.create(
        rule_type=CalculationRule.RuleType.MARGIN,
        name='Selling price markups',
        params={'minimum': '0.03', 'standard': '0.10', 'premium': '0.25'},
    )
    CalculationRule.objects.create(
        rule_type=CalculationRule.RuleType.VOLUMETRIC_WEIGHT,
        name='Volumetric weight',
        params={'divisor': '6000'},
    )
