from django.conf import settings
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Customer, Product, ProductListing, ProductListingChannel


def sync_product_in_stock_from_stock_listing(product_id: int) -> None:
    # Shelf truth only: PREORDER rows (including qty 0 vitrine) do not affect Product.in_stock.
    row = (
        ProductListing.objects.filter(
            product_id=product_id,
            channel=ProductListingChannel.STOCK,
        )
        .only('quantity')
        .first()
    )
    in_stock = bool(row and row.quantity > 0)
    Product.objects.filter(pk=product_id).update(in_stock=in_stock)


@receiver(post_save, sender=ProductListing)
def product_listing_post_save_sync_in_stock(sender, instance, **kwargs):
    if instance.channel != ProductListingChannel.STOCK:
        return
    sync_product_in_stock_from_stock_listing(instance.product_id)


@receiver(post_delete, sender=ProductListing)
def product_listing_post_delete_sync_in_stock(sender, instance, **kwargs):
    if instance.channel != ProductListingChannel.STOCK:
        return
    sync_product_in_stock_from_stock_listing(instance.product_id)


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
        tg_account=(getattr(instance, 'telegram', None) or '').strip(),
        newsletter_opt_in=bool(getattr(instance, 'newsletter_opt_in', False)),
        address='',
    )
