"""Audit trail for manual overrides of buying data and calculations."""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class ManualOverride(models.Model):
    class Kind(models.TextChoices):
        # What exactly was overridden; rule changes are audited separately via
        # CalculationRule versioning + updated_by.
        COMPONENT = 'component', 'Cost component'
        FINAL_PRICE = 'final_price', 'Final customer price'
        OFFER_FIELD = 'offer_field', 'Supplier offer field'
        REMOVAL = 'removal', 'Override removed'

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')

    kind = models.CharField(max_length=15, choices=Kind.choices, default=Kind.COMPONENT)
    field = models.CharField(max_length=80)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='buying_overrides'
    )
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        default_permissions = ()
        indexes = [models.Index(fields=['content_type', 'object_id'])]

    def __str__(self):
        return f'Override {self.field} on {self.content_type} #{self.object_id}'
