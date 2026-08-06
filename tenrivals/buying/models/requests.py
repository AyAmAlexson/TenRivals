"""Buying request lifecycle: staff request + AI-normalized product for that request."""

from django.conf import settings
from django.db import models


class ProductCategory(models.TextChoices):
    RACQUET = 'racquet', 'Racquet'
    SHOES = 'shoes', 'Shoes'
    STRING_REEL = 'string_reel', 'String reel'
    STRING_SET = 'string_set', 'String set'
    OVERGRIP = 'overgrip', 'Overgrip'
    BALLS = 'balls', 'Balls'
    APPAREL = 'apparel', 'Apparel'
    BAG = 'bag', 'Bag'
    ACCESSORY = 'accessory', 'Accessory'


class NormalizedProduct(models.Model):
    """Canonical description of the product wanted in ONE buying request.

    This is the AI output for a specific request (editable by staff), not the
    knowledge base. Reusable knowledge lives in CanonicalProduct.
    """

    class ColorPolicy(models.TextChoices):
        REQUIRED = 'required', 'Required'
        PREFERRED = 'preferred', 'Preferred'
        OPTIONAL = 'optional', 'Optional'

    brand = models.CharField(max_length=120, blank=True)
    model_name = models.CharField('Model', max_length=200, blank=True)
    generation = models.CharField(max_length=60, blank=True)
    category = models.CharField(max_length=20, choices=ProductCategory.choices, blank=True)
    gender = models.CharField(max_length=20, blank=True)
    court = models.CharField(max_length=30, blank=True)
    size = models.CharField(max_length=30, blank=True)
    size_system = models.CharField(max_length=10, blank=True)
    grip_size = models.CharField(max_length=10, blank=True)
    color = models.CharField(max_length=60, blank=True)
    color_policy = models.CharField(
        max_length=10, choices=ColorPolicy.choices, default=ColorPolicy.OPTIONAL
    )
    weight_g = models.PositiveIntegerField(null=True, blank=True, help_text='Racquet weight, grams')
    head_size = models.CharField(max_length=20, blank=True)
    string_pattern = models.CharField(max_length=20, blank=True)
    variant = models.CharField(
        max_length=60, blank=True,
        help_text='Team / Lite / Tour / Plus / Junior / empty for standard',
    )
    length_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    required_attributes = models.JSONField(default=list, blank=True)
    optional_attributes = models.JSONField(default=list, blank=True)
    uncertainties = models.JSONField(default=list, blank=True)
    manufacturer_code = models.CharField(max_length=80, blank=True)
    ean = models.CharField(max_length=20, blank=True)
    upc = models.CharField(max_length=20, blank=True)
    aliases = models.JSONField(default=list, blank=True)

    # Per-field provenance: client | ai | canonical | staff
    field_sources = models.JSONField(default=dict, blank=True)
    # Snapshot of RacquetSpecification values applied at enrichment time
    enrichment_snapshot = models.JSONField(default=dict, blank=True)

    canonical_product = models.ForeignKey(
        'buying.CanonicalProduct',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='normalized_products',
    )

    raw_ai_output = models.JSONField(default=dict, blank=True)
    prompt_version = models.CharField(max_length=40, blank=True)
    model_version = models.CharField(max_length=80, blank=True)
    edited_by_staff = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        parts = [self.brand, self.model_name, self.generation]
        return ' '.join(p for p in parts if p) or f'NormalizedProduct #{self.pk}'

    def display_summary(self) -> str:
        extras = [
            self.get_category_display() if self.category else '',
            self.gender,
            self.court,
            f'{self.size_system} {self.size}'.strip() if self.size else '',
            f'grip {self.grip_size}' if self.grip_size else '',
            self.color,
        ]
        head = str(self)
        tail = ', '.join(e for e in extras if e)
        return f'{head} ({tail})' if tail else head


class BuyingRequest(models.Model):
    """One staff request: 'buy this specific product for a client'."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        NORMALIZED = 'normalized', 'Normalized'
        SEARCHING = 'searching', 'Searching'
        PARTIALLY_COMPLETED = 'partially_completed', 'Partially completed'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    original_query = models.TextField()
    normalized_product = models.OneToOneField(
        NormalizedProduct,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='buying_request',
    )
    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='buying_requests'
    )
    client = models.ForeignKey(
        'shop.Customer',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='buying_requests',
    )
    quantity = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.DRAFT)
    is_archived = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        default_permissions = ()

    def __str__(self):
        return f'Buying request #{self.pk}'

    @property
    def is_open(self) -> bool:
        return self.status not in (self.Status.CANCELLED, self.Status.FAILED) and not self.is_archived
