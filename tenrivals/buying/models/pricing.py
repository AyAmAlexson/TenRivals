"""Versioned calculation rules and cost scenarios.

CostScenario = SupplierOffer + FulfillmentRoute + resolved CalculationRules.
All money components are stored in GEL; original currency, FX rate, rule versions
and per-component provenance live in calculation_details.
"""

from django.conf import settings
from django.db import models

from .requests import ProductCategory


class CalculationRule(models.Model):
    """Single versioned model for every pricing rule type.

    `params` carries the formula parameters (rates, thresholds, tariffs);
    `conditions` carries extra applicability conditions (price ranges, etc.).
    Resolution picks the most specific enabled rule active at calculation time.
    """

    class RuleType(models.TextChoices):
        LOCAL_TAX = 'local_tax', 'Local tax'
        LOCAL_SHIPPING = 'local_shipping', 'Local shipping'
        INTERNATIONAL_SHIPPING = 'international_shipping', 'International shipping'
        GEORGIA_VAT = 'georgia_vat', 'Georgia import VAT'
        CUSTOMS = 'customs', 'Customs declaration fee'
        DECLARATION_SERVICE = 'declaration_service', 'Declaration service (forwarder)'
        PAYMENT_FEE = 'payment_fee', 'Payment fee'
        FX_RATE = 'fx_rate', 'FX rate'
        FX_BUFFER = 'fx_buffer', 'FX buffer'
        RISK_RESERVE = 'risk_reserve', 'Risk reserve'
        SALES_VAT = 'sales_vat', 'Sales VAT'
        SMALL_BUSINESS_TAX = 'small_business_tax', 'Small business tax'
        MARGIN = 'margin', 'Margin (selling price markups)'
        WEIGHT = 'weight', 'Weight'
        VOLUMETRIC_WEIGHT = 'volumetric_weight', 'Volumetric weight'
        ROUNDING = 'rounding', 'Rounding'
        HANDLING = 'handling', 'Handling'
        INSURANCE = 'insurance', 'Insurance'
        ALLOCATION = 'allocation', 'Allocation'

    rule_type = models.CharField(max_length=30, choices=RuleType.choices)
    name = models.CharField(max_length=160)

    # Scope: empty/null means "any". The most specific match wins.
    country = models.CharField(max_length=2, blank=True)
    supplier = models.ForeignKey(
        'buying.Supplier', null=True, blank=True, on_delete=models.CASCADE,
        related_name='calculation_rules',
    )
    provider = models.ForeignKey(
        'buying.FulfillmentProvider', null=True, blank=True, on_delete=models.CASCADE,
        related_name='calculation_rules',
    )
    warehouse = models.ForeignKey(
        'buying.FulfillmentWarehouse', null=True, blank=True, on_delete=models.CASCADE,
        related_name='calculation_rules',
    )
    category = models.CharField(max_length=20, choices=ProductCategory.choices, blank=True)

    conditions = models.JSONField(default=dict, blank=True)
    params = models.JSONField(default=dict, blank=True)

    version = models.PositiveIntegerField(default=1)
    active_from = models.DateTimeField(null=True, blank=True)
    active_to = models.DateTimeField(null=True, blank=True)
    enabled = models.BooleanField(default=True)
    priority = models.IntegerField(default=100)

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='buying_rules_updated',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['rule_type', '-priority', '-version']
        default_permissions = ()

    def __str__(self):
        return f'{self.get_rule_type_display()}: {self.name} v{self.version}'

    def scope_specificity(self) -> int:
        return sum(
            1
            for value in (
                self.country,
                self.supplier_id,
                self.provider_id,
                self.warehouse_id,
                self.category,
            )
            if value
        )


class CostScenario(models.Model):
    """A concrete combination of an offer, a route and pricing rules.

    IMMUTABLE snapshot: once calculated, a scenario is never recalculated in
    place. Any change (rules, FX, overrides, offer revision) produces a NEW
    scenario; the old one is marked superseded and keeps its original numbers,
    so 'why did we quote that price on that day' always has an answer.
    calculation_details stores values (amounts, rates, rule params), not live
    references.
    """

    class ScenarioType(models.TextChoices):
        CURRENT_SINGLE_ITEM = 'current_single_item', 'Current single item'
        FREE_SHIPPING_OPTIMIZATION = 'free_shipping_optimization', 'Free shipping optimization'
        GROUPED_ORDER = 'grouped_order', 'Grouped order'
        ADDED_INVENTORY = 'added_inventory', 'Added inventory'
        PENDING_BUYING_REQUEST_MERGE = 'pending_buying_request_merge', 'Merge with open request'
        ALTERNATIVE_ROUTE = 'alternative_route', 'Alternative route'
        MANUAL = 'manual', 'Manual'

    class Status(models.TextChoices):
        CALCULATED = 'calculated', 'Calculated'
        # A blocking input is missing (FX, tariff, weight, VAT/customs logic,
        # margin): partial numbers are stored but the scenario is excluded from
        # ranking until the missing rules are configured.
        CALCULATION_BLOCKED = 'calculation_blocked', 'Calculation blocked'
        FAILED = 'failed', 'Failed'
        SUPERSEDED = 'superseded', 'Superseded'

    class Recommendation(models.TextChoices):
        RECOMMENDED = 'recommended', 'Recommended'
        REVIEW = 'review', 'Review manually'
        NOT_RECOMMENDED = 'not_recommended', 'Not recommended'

    class Confidence(models.TextChoices):
        EXACT = 'exact', 'Exact'
        MOSTLY_EXACT = 'mostly_exact', 'Mostly exact'
        ESTIMATED = 'estimated', 'Estimated'

    buying_request = models.ForeignKey(
        'buying.BuyingRequest', on_delete=models.CASCADE, related_name='cost_scenarios'
    )
    supplier_offer = models.ForeignKey(
        'buying.SupplierOffer', on_delete=models.CASCADE, related_name='cost_scenarios'
    )
    fulfillment_route = models.ForeignKey(
        'buying.FulfillmentRoute', on_delete=models.PROTECT, related_name='cost_scenarios'
    )
    scenario_type = models.CharField(
        max_length=35, choices=ScenarioType.choices, default=ScenarioType.CURRENT_SINGLE_ITEM
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CALCULATED)

    # Components, GEL.
    item_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    local_shipping = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    local_tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    international_shipping = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    insurance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    customs = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    declaration_service = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Forwarder declaration service fee (e.g. Onex), applied when import is declared',
    )
    georgia_vat = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fx_buffer = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    risk_reserve = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    handling_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    allocated_shipping = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='Requested item share when shipping is split across items',
    )
    landed_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    minimum_margin = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    margin_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    margin_rate = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    # Selling side: break-even = landed_cost / net sales coefficient (the
    # coefficient snapshot lives in calculation_details['pricing']); the three
    # prices are markups on the break-even, not on the landed cost.
    break_even_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    price_minimum = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    price_standard = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    price_premium = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    customer_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    chargeable_weight_g = models.PositiveIntegerField(null=True, blank=True)
    estimated_min_days = models.PositiveIntegerField(null=True, blank=True)
    estimated_max_days = models.PositiveIntegerField(null=True, blank=True)

    calculation_version = models.CharField(max_length=40)
    calculation_details = models.JSONField(default=dict, blank=True)
    confidence = models.CharField(
        max_length=15, choices=Confidence.choices, default=Confidence.ESTIMATED
    )
    warnings = models.JSONField(default=list, blank=True)

    # Ranking is two-dimensional: rank orders by customer_price; recommendation
    # is a separate quality verdict (risk, confidence, availability, match).
    rank = models.PositiveIntegerField(null=True, blank=True)
    rank_labels = models.JSONField(default=list, blank=True)
    recommendation = models.CharField(
        max_length=20, choices=Recommendation.choices, blank=True
    )
    recommendation_reasons = models.JSONField(default=list, blank=True)
    is_hidden = models.BooleanField(default=False)
    superseded_by = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='supersedes_set'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['rank', '-created_at']
        default_permissions = ()

    def __str__(self):
        return f'Scenario #{self.pk} ({self.scenario_type}) for offer #{self.supplier_offer_id}'

    @property
    def breakdown(self) -> list[dict]:
        return self.calculation_details.get('components', [])
