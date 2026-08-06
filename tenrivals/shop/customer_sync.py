"""Keep shop.Customer and linked CustomUser profile fields aligned."""

from __future__ import annotations

from persons.account_display import normalize_telegram_username


def sync_customer_to_user(customer) -> None:
    """Push Customer contact fields onto the linked site user (if any)."""
    if not getattr(customer, 'user_id', None):
        return
    user = customer.user
    if user is None:
        return

    update_fields: list[str] = []
    fn = (customer.first_name or '').strip()
    ln = (customer.last_name or '').strip()
    phone = (customer.phone or '').strip()
    tg = normalize_telegram_username(customer.tg_account)
    email = (customer.email or '').strip()

    if fn and user.first_name != fn:
        user.first_name = fn
        update_fields.append('first_name')
    if user.last_name != ln:
        user.last_name = ln
        update_fields.append('last_name')
    if user.mobile != phone:
        user.mobile = phone
        update_fields.append('mobile')
    if (user.telegram or '') != tg:
        user.telegram = tg
        update_fields.append('telegram')
    if bool(user.newsletter_opt_in) != bool(customer.newsletter_opt_in):
        user.newsletter_opt_in = bool(customer.newsletter_opt_in)
        update_fields.append('newsletter_opt_in')

    # Email: only sync when Customer has a non-empty address and it differs.
    # Skip if another user already owns that email.
    if email and user.email.strip().lower() != email.lower():
        from django.contrib.auth import get_user_model

        User = get_user_model()
        taken = (
            User.objects.filter(email__iexact=email)
            .exclude(pk=user.pk)
            .exists()
        )
        if not taken:
            user.email = email.lower()
            update_fields.append('email')

    if update_fields:
        user.save(update_fields=update_fields)
        if 'email' in update_fields:
            try:
                from allauth.account.models import EmailAddress
            except Exception:
                EmailAddress = None
            if EmailAddress is not None:
                email_lower = user.email.strip().lower()
                primary = EmailAddress.objects.filter(user=user, primary=True).first()
                if primary:
                    if primary.email.lower() != email_lower:
                        primary.email = email_lower
                        primary.verified = False
                        primary.save(update_fields=['email', 'verified'])
                else:
                    EmailAddress.objects.update_or_create(
                        user=user,
                        email=email_lower,
                        defaults={'primary': True, 'verified': False},
                    )


def sync_user_to_customer(user) -> None:
    """Push CustomUser profile fields onto the linked retail Customer (if any)."""
    from shop.models import Customer

    customer = Customer.objects.filter(user_id=user.pk).first()
    if customer is None:
        return

    update_fields: list[str] = []
    fn = (user.first_name or '').strip()
    ln = (user.last_name or '').strip()
    phone = (user.mobile or '').strip()
    tg = normalize_telegram_username(getattr(user, 'telegram', None))
    email = (user.email or '').strip()

    if fn and customer.first_name != fn:
        customer.first_name = fn
        update_fields.append('first_name')
    if customer.last_name != ln:
        customer.last_name = ln
        update_fields.append('last_name')
    if customer.phone != phone:
        customer.phone = phone
        update_fields.append('phone')
    if normalize_telegram_username(customer.tg_account) != tg:
        customer.tg_account = tg
        update_fields.append('tg_account')
    if (customer.email or '').strip().lower() != email.lower():
        customer.email = email
        update_fields.append('email')
    if bool(customer.newsletter_opt_in) != bool(getattr(user, 'newsletter_opt_in', False)):
        customer.newsletter_opt_in = bool(user.newsletter_opt_in)
        update_fields.append('newsletter_opt_in')

    if update_fields:
        update_fields.append('updated_at')
        customer.save(update_fields=update_fields)
