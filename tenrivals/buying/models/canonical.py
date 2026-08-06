"""CanonicalProduct: accumulated knowledge about products the system has already
worked with. NOT a catalog/PIM — records appear only from the buying process.

RacquetSpecification holds maintained tennis racquet specs used to enrich
NormalizedProduct after AI parsing (deterministic — not LLM memory).
"""

from django.db import models

from .requests import ProductCategory


class CanonicalProduct(models.Model):
    brand = models.CharField(max_length=120)
    model_name = models.CharField('Model', max_length=200)
    generation = models.CharField(max_length=60, blank=True)
    category = models.CharField(max_length=20, choices=ProductCategory.choices, blank=True)
    gender = models.CharField(max_length=20, blank=True)
    court = models.CharField(max_length=30, blank=True)
    manufacturer_code = models.CharField(max_length=80, blank=True)
    ean = models.CharField(max_length=20, blank=True)
    upc = models.CharField(max_length=20, blank=True)
    aliases = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        indexes = [
            models.Index(fields=['brand', 'model_name', 'generation', 'category']),
        ]

    def __str__(self):
        parts = [self.brand, self.model_name, self.generation]
        return ' '.join(p for p in parts if p)


class RacquetSpecification(models.Model):
    """Maintained knowledge of a specific racquet SKU / line variant.

    Enrichment copies values onto NormalizedProduct at resolve time so later
    edits here do not rewrite historical buying requests.
    """

    canonical_product = models.ForeignKey(
        CanonicalProduct,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='racquet_specs',
    )
    brand = models.CharField(max_length=120)
    model_family = models.CharField(
        max_length=120,
        help_text='e.g. Pure Drive, Blade, EZONE — without head size / weight tokens',
    )
    generation = models.CharField(max_length=60, blank=True)
    variant = models.CharField(
        max_length=60,
        blank=True,
        help_text='Team, Lite, Tour, Plus, Junior, or empty for standard',
    )
    head_size_sqin = models.PositiveSmallIntegerField(null=True, blank=True)
    weight_g_unstrung = models.PositiveIntegerField(null=True, blank=True)
    string_pattern = models.CharField(max_length=20, blank=True)
    length_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    manufacturer_code = models.CharField(max_length=80, blank=True)
    aliases = models.JSONField(default=list, blank=True)
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        ordering = ['brand', 'model_family', 'generation', 'variant', 'head_size_sqin']
        indexes = [
            models.Index(fields=['brand', 'model_family', 'generation']),
        ]

    def __str__(self):
        parts = [
            self.brand,
            self.model_family,
            self.variant,
            f'{self.head_size_sqin}' if self.head_size_sqin else '',
            self.generation,
            f'{self.weight_g_unstrung}g' if self.weight_g_unstrung else '',
        ]
        return ' '.join(p for p in parts if p)

    def to_snapshot(self) -> dict:
        return {
            'id': self.pk,
            'brand': self.brand,
            'model_family': self.model_family,
            'generation': self.generation,
            'variant': self.variant,
            'head_size_sqin': self.head_size_sqin,
            'weight_g_unstrung': self.weight_g_unstrung,
            'string_pattern': self.string_pattern,
            'length_cm': str(self.length_cm) if self.length_cm is not None else None,
            'manufacturer_code': self.manufacturer_code,
            'aliases': list(self.aliases or []),
        }
