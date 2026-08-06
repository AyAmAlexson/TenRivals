"""Fulfillment layer: forwarders, their warehouses, and logistics routes to Georgia.

A supplier is never tied to one shipping cost directly: an offer is combined with
an applicable FulfillmentRoute inside a CostScenario. Onex is plain data here.
"""

from django.db import models

from .requests import ProductCategory


class FulfillmentProvider(models.Model):
    name = models.CharField(max_length=120)
    code = models.SlugField(max_length=60, unique=True)
    enabled = models.BooleanField(default=True)
    website = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        default_permissions = ()

    def __str__(self):
        return self.name


class FulfillmentWarehouse(models.Model):
    provider = models.ForeignKey(
        FulfillmentProvider, on_delete=models.CASCADE, related_name='warehouses'
    )
    country = models.CharField(max_length=2, help_text='ISO 3166-1 alpha-2')
    state_or_region = models.CharField(max_length=80, blank=True)
    city = models.CharField(max_length=80, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    address_reference = models.CharField(max_length=200, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['provider__name', 'country']
        default_permissions = ()

    def __str__(self):
        return f'{self.provider.name} {self.country} warehouse'


class FulfillmentRoute(models.Model):
    """Delivery scheme: supplier country (or a specific supplier) -> provider
    warehouse -> Georgia. Calculation rules are resolved dynamically by scope."""

    class DeliveryMethod(models.TextChoices):
        AIR = 'air', 'Air'
        GROUND = 'ground', 'Ground'
        SEA = 'sea', 'Sea'

    name = models.CharField(max_length=160)
    provider = models.ForeignKey(
        FulfillmentProvider, on_delete=models.CASCADE, related_name='routes'
    )
    warehouse = models.ForeignKey(
        FulfillmentWarehouse, on_delete=models.CASCADE, related_name='routes'
    )
    origin_country = models.CharField(max_length=2, help_text='ISO 3166-1 alpha-2')
    destination_country = models.CharField(max_length=2, default='GE')
    delivery_method = models.CharField(
        max_length=10, choices=DeliveryMethod.choices, default=DeliveryMethod.AIR
    )
    enabled = models.BooleanField(default=True)
    supplier = models.ForeignKey(
        'buying.Supplier',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='routes',
        help_text='Route specific to one shop; empty = any shop in the origin country',
    )
    category = models.CharField(
        max_length=20, choices=ProductCategory.choices, blank=True,
        help_text='Route specific to one category; empty = any category',
    )
    estimated_min_days = models.PositiveIntegerField(null=True, blank=True)
    estimated_max_days = models.PositiveIntegerField(null=True, blank=True)
    priority = models.IntegerField(default=100, help_text='Higher wins among applicable routes')
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-priority', 'name']
        default_permissions = ()

    def __str__(self):
        return self.name
