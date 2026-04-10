from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Customer


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_retail_customer_for_new_user(sender, instance, created, **kwargs):
    """Mirror site registration into Customer (optional FK to user)."""
    if not created:
        return
    if Customer.objects.filter(user_id=instance.pk).exists():
        return
    Customer.objects.create(
        user=instance,
        first_name=(instance.first_name or '').strip() or 'Customer',
        last_name=(instance.last_name or '').strip(),
        phone=(instance.mobile or '').strip(),
        email=(instance.email or '').strip(),
        newsletter_opt_in=bool(getattr(instance, 'newsletter_opt_in', False)),
        address='',
    )
