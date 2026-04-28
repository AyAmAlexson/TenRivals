from django.conf import settings
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Customer, Product, ProductListing, ProductListingChannel


def sync_product_in_stock_from_stock_listing(product_id: int) -> None:
    """Sync ``Product.in_stock`` from the STOCK listing row.

    * ``quantity > 0`` → force ``in_stock=True`` (on shelf).
    * No STOCK row → ``in_stock=False``.
    * ``quantity == 0`` → do **not** auto-clear ``in_stock`` so staff can keep the In stock
      checkbox on for vitrine SKUs (shown in the stock grid after on-hand cards).
    """
    row = (
        ProductListing.objects.filter(
            product_id=product_id,
            channel=ProductListingChannel.STOCK,
        )
        .only('quantity')
        .first()
    )
    if row and row.quantity > 0:
        Product.objects.filter(pk=product_id).update(in_stock=True)
    elif not row:
        Product.objects.filter(pk=product_id).update(in_stock=False)


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
    """Mirror site registration into Customer (optional FK to user).

    If the same email already has a guest checkout ``Customer`` (``user`` is null), link that
    row to the new account instead of creating a duplicate — ``SalesOrder`` history stays on
    one customer record.
    """
    if not created:
        return
    if Customer.objects.filter(user_id=instance.pk).exists():
        return
    email = (instance.email or '').strip()
    if email:
        guest = (
            Customer.objects.filter(user__isnull=True, email__iexact=email)
            .order_by('pk')
            .first()
        )
        if guest:
            guest.user = instance
            fn = (instance.first_name or '').strip()
            ln = (instance.last_name or '').strip()
            if fn:
                guest.first_name = fn
            if ln:
                guest.last_name = ln
            mob = (instance.mobile or '').strip()
            if mob:
                guest.phone = mob
            tg = (getattr(instance, 'telegram', None) or '').strip()
            if tg:
                guest.tg_account = tg
            guest.email = email
            guest.newsletter_opt_in = guest.newsletter_opt_in or bool(
                getattr(instance, 'newsletter_opt_in', False)
            )
            guest.save(
                update_fields=[
                    'user',
                    'first_name',
                    'last_name',
                    'phone',
                    'email',
                    'tg_account',
                    'newsletter_opt_in',
                    'updated_at',
                ]
            )
            return
    Customer.objects.create(
        user=instance,
        first_name=(instance.first_name or '').strip() or 'Customer',
        last_name=(instance.last_name or '').strip(),
        phone=(instance.mobile or '').strip(),
        email=email,
        tg_account=(getattr(instance, 'telegram', None) or '').strip(),
        newsletter_opt_in=bool(getattr(instance, 'newsletter_opt_in', False)),
        address='',
    )
