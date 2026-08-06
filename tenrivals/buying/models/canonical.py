"""CanonicalProduct: accumulated knowledge about products the system has already
worked with. NOT a catalog/PIM — records appear only from the buying process."""

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
