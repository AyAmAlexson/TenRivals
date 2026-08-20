"""Manual buying calculator batches (one supplier, N lines, shared shipment quotes).

Designed so a later partition optimizer can evaluate split-vs-combined purchases
on the same lines without changing line storage.
"""

from django.conf import settings
from django.db import models

from .requests import ProductCategory


class BuyingBatch(models.Model):
    """Staff-built cart for one supplier — calculator session, not a search request."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        CALCULATED = 'calculated', 'Calculated'
        ARCHIVED = 'archived', 'Archived'

    supplier = models.ForeignKey(
        'buying.Supplier',
        on_delete=models.PROTECT,
        related_name='buying_batches',
    )
    title = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    # Future: recommended partition of line ids, e.g. [[1,2],[3]].
    recommended_partition = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='buying_batches_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']
        default_permissions = ()

    def __str__(self):
        label = (self.title or '').strip() or f'Batch #{self.pk}'
        return f'{label} ({self.supplier_id})'

    def display_title(self) -> str:
        if (self.title or '').strip():
            return self.title.strip()
        lines = list(self.lines.all()[:3])
        if not lines:
            return f'Batch #{self.pk or "new"}'
        names = ', '.join(ln.title for ln in lines)
        extra = self.lines.count() - len(lines)
        if extra > 0:
            names = f'{names} +{extra}'
        return names


class BuyingBatchLine(models.Model):
    """One product row in a calculator batch (manual entry, no scraping)."""

    batch = models.ForeignKey(
        BuyingBatch, on_delete=models.CASCADE, related_name='lines'
    )
    sort_order = models.PositiveSmallIntegerField(default=1, db_index=True)
    title = models.CharField(max_length=240)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    quantity = models.PositiveIntegerField(default=1)
    category = models.CharField(
        max_length=20, choices=ProductCategory.choices, blank=True
    )
    weight_g = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='Parcel / shipping weight per unit (g). Empty → category weight rule.',
    )
    url = models.URLField(max_length=600, blank=True)
    sku_hint = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'id']
        default_permissions = ()

    def __str__(self):
        return f'{self.title} ×{self.quantity}'

    @property
    def line_subtotal(self):
        return self.unit_price * self.quantity


class BuyingBatchQuote(models.Model):
    """Immutable quote snapshot: one fulfillment route for a batch partition.

    MVP always uses partition_key='all' (every line in one shipment). Later the
    optimizer can store multiple quotes with different partition keys.
    """

    class Status(models.TextChoices):
        CALCULATED = 'calculated', 'Calculated'
        CALCULATION_BLOCKED = 'calculation_blocked', 'Calculation blocked'
        FAILED = 'failed', 'Failed'
        SUPERSEDED = 'superseded', 'Superseded'

    class Confidence(models.TextChoices):
        EXACT = 'exact', 'Exact'
        MOSTLY_EXACT = 'mostly_exact', 'Mostly exact'
        ESTIMATED = 'estimated', 'Estimated'

    class ScenarioKind(models.TextChoices):
        COMBINED_SHIPMENT = 'combined_shipment', 'Combined shipment'
        PARTITION = 'partition', 'Partition'

    batch = models.ForeignKey(
        BuyingBatch, on_delete=models.CASCADE, related_name='quotes'
    )
    fulfillment_route = models.ForeignKey(
        'buying.FulfillmentRoute',
        on_delete=models.PROTECT,
        related_name='batch_quotes',
    )
    scenario_kind = models.CharField(
        max_length=32,
        choices=ScenarioKind.choices,
        default=ScenarioKind.COMBINED_SHIPMENT,
    )
    # 'all' = every line together; later e.g. '1|2+3' for split packs.
    partition_key = models.CharField(max_length=120, default='all', db_index=True)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.CALCULATED
    )

    item_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    local_shipping = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    local_tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    international_shipping = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    insurance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    customs = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    declaration_service = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    georgia_vat = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fx_buffer = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    risk_reserve = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    handling_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shared_cost_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Sum of costs allocated across lines (excl. direct item cost).',
    )
    landed_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    break_even_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    price_minimum = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    price_standard = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    price_premium = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    customer_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    margin_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    chargeable_weight_g = models.PositiveIntegerField(null=True, blank=True)
    estimated_min_days = models.PositiveIntegerField(null=True, blank=True)
    estimated_max_days = models.PositiveIntegerField(null=True, blank=True)

    calculation_version = models.CharField(max_length=40)
    calculation_details = models.JSONField(default=dict, blank=True)
    line_results = models.JSONField(
        default=list,
        blank=True,
        help_text='Per-line shares: item_cost, allocated_shared, landed, prices…',
    )
    confidence = models.CharField(
        max_length=15, choices=Confidence.choices, default=Confidence.ESTIMATED
    )
    warnings = models.JSONField(default=list, blank=True)
    rank = models.PositiveIntegerField(null=True, blank=True)
    superseded_by = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='supersedes_set',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['rank', '-created_at']
        default_permissions = ()

    def __str__(self):
        return f'BatchQuote #{self.pk} batch={self.batch_id} route={self.fulfillment_route_id}'
