"""Optimization: alternative cost scenarios and the allowed pool of add-on items.

Principle (ADD §7): savings alone never justify a recommendation — cash freeze,
turnover, unsold-stock risk and company strategy always take part in the decision.
"""

from django.conf import settings
from django.db import models

from .requests import ProductCategory


class OptimizationScenario(models.Model):
    class Type(models.TextChoices):
        FREE_SHIPPING = 'free_shipping', 'Free local shipping'
        COMBINED_SHIPMENT = 'combined_shipment', 'Combined shipment'
        MERGE_OPEN_REQUESTS = 'merge_open_requests', 'Merge with open requests'

    class Risk(models.TextChoices):
        LOW = 'low', 'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'

    class Recommendation(models.TextChoices):
        RECOMMEND = 'recommend', 'Recommend'
        CONSIDER = 'consider', 'Consider'
        DO_NOT_OPTIMIZE = 'do_not_optimize', 'Do not optimize'

    base_cost_scenario = models.ForeignKey(
        'buying.CostScenario', on_delete=models.CASCADE, related_name='optimizations'
    )
    result_cost_scenario = models.ForeignKey(
        'buying.CostScenario',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='optimization_sources',
    )
    type = models.CharField(max_length=30, choices=Type.choices)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    additional_items = models.JSONField(default=list, blank=True)
    additional_cash_required = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    total_order_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total_saving = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    saving_for_requested_item = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    optimized_customer_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    inventory_risk = models.CharField(max_length=10, choices=Risk.choices, blank=True)
    cash_freeze_risk = models.CharField(max_length=10, choices=Risk.choices, blank=True)
    recommendation = models.CharField(max_length=20, choices=Recommendation.choices, blank=True)
    rejected_reason = models.TextField(blank=True)
    calculation_details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        default_permissions = ()

    def __str__(self):
        return f'{self.get_type_display()}: {self.title}'


class OptimizationCandidate(models.Model):
    """Item the owner explicitly allows to be added to purchases for optimization.

    The Optimization Engine never suggests random products — only this pool and
    offers from open buying requests.
    """

    class Risk(models.TextChoices):
        LOW = 'low', 'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'

    product = models.ForeignKey(
        'shop.Product',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='buying_optimization_candidates',
        help_text='Existing catalog SKU; leave empty for a free-form item',
    )
    title = models.CharField(max_length=200, blank=True, help_text='Used when no catalog SKU is linked')
    supplier = models.ForeignKey(
        'buying.Supplier',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='optimization_candidates',
        help_text='Restrict to one shop; empty = any shop that sells it',
    )
    supplier_url = models.URLField(max_length=600, blank=True)
    estimated_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    category = models.CharField(max_length=20, choices=ProductCategory.choices, blank=True)
    allowed_for_optimization = models.BooleanField(default=True)
    maximum_quantity = models.PositiveIntegerField(default=1)
    expected_margin = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    expected_turnover_days = models.PositiveIntegerField(null=True, blank=True)
    inventory_risk = models.CharField(max_length=10, choices=Risk.choices, default=Risk.MEDIUM)
    priority = models.IntegerField(default=100)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='buying_optimization_candidates_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-priority', 'title']
        default_permissions = ()

    def __str__(self):
        return self.title or (self.product.name if self.product_id else f'Candidate #{self.pk}')
