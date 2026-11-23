"""Staff shipping queue: open orders and their delivery addresses."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from persons.account_display import normalize_telegram_username

from .models import SalesOrder

# Statuses that still reserve stock and still need a delivery.
# Completed is delivered. Cancelled and refunded are not current addresses,
# and marking them completed would pull stock again.
OPEN_DELIVERY_STATUSES = (
    SalesOrder.Status.SUBMITTED,
    SalesOrder.Status.CONFIRMED,
    SalesOrder.Status.BUYING,
    SalesOrder.Status.AWAITING_PAYMENT,
    SalesOrder.Status.SHIPPED,
)


def _staff_ok(user):
    return bool(user.is_authenticated and user.is_superuser)


def delivery_address_for_order(order: SalesOrder) -> str:
    """Checkout stores the address on the order notes; staff orders use the customer."""
    for line in (order.notes or '').splitlines():
        stripped = line.strip()
        if stripped.lower().startswith('address:'):
            value = stripped.split(':', 1)[1].strip()
            if value:
                return value
            break
    customer = order.customer
    if customer is None:
        return ''
    return (customer.address or '').strip()


def maps_search_urls(address: str) -> tuple[str, str]:
    query = quote_plus(address)
    return (
        f'https://www.google.com/maps/search/?api=1&query={query}',
        f'https://yandex.com/maps/?text={query}',
    )


def contact_phone_for_order(order: SalesOrder) -> str:
    """Checkout writes the contact phone onto the customer."""
    customer = order.customer
    if customer is None:
        return ''
    return (customer.phone or '').strip()


def phone_tel_uri(phone: str) -> str:
    """tel: URI a phone browser can dial. Local Georgian mobiles get +995."""
    raw = (phone or '').strip()
    if not raw:
        return ''
    compact = ''.join(ch for ch in raw if ch.isdigit() or ch == '+')
    if raw.startswith('00'):
        compact = '+' + ''.join(ch for ch in raw[2:] if ch.isdigit())
    digits = ''.join(ch for ch in compact if ch.isdigit())
    if not digits:
        return ''
    if compact.startswith('+'):
        return f'tel:+{digits}'
    if len(digits) == 9 and digits.startswith('5'):
        return f'tel:+995{digits}'
    if len(digits) == 10 and digits.startswith('0'):
        return f'tel:+995{digits[1:]}'
    if len(digits) == 12 and digits.startswith('995'):
        return f'tel:+{digits}'
    return f'tel:{digits}'


# Telegram usernames: a letter, then 4–31 letters, digits, or underscores.
_TG_USERNAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]{4,31}$')
_TG_EMPTY = {'', '—', '-', '–'}
_TG_URL_PREFIXES = (
    'https://t.me/',
    'http://t.me/',
    'https://telegram.me/',
    'http://telegram.me/',
    't.me/',
    'telegram.me/',
    'tg://resolve?domain=',
)


def _note_value(order: SalesOrder, prefix: str) -> str | None:
    for line in (order.notes or '').splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(prefix):
            return stripped.split(':', 1)[1].strip()
    return None


def contact_telegram_for_order(order: SalesOrder) -> str:
    """Checkout stores Telegram on the order notes; staff orders use the customer."""
    noted = _note_value(order, 'telegram:')
    if noted is not None and noted not in _TG_EMPTY:
        return noted
    customer = order.customer
    if customer is None:
        return ''
    return (customer.tg_account or '').strip()


def telegram_username(value: str) -> str:
    """Username that can open a Telegram profile, without @."""
    raw = (value or '').strip()
    if raw in _TG_EMPTY:
        return ''
    lower = raw.lower()
    for prefix in _TG_URL_PREFIXES:
        if lower.startswith(prefix):
            raw = raw[len(prefix):]
            break
    raw = raw.split('?', 1)[0].split('/', 1)[0].strip()
    username = normalize_telegram_username(raw)
    if not _TG_USERNAME.fullmatch(username):
        return ''
    return username


def telegram_profile_url(value: str) -> str:
    username = telegram_username(value)
    if not username:
        return ''
    return f'https://t.me/{username}'


@login_required
@user_passes_test(_staff_ok)
def staff_shipping_addresses(request):
    orders = (
        SalesOrder.objects.filter(status__in=OPEN_DELIVERY_STATUSES)
        .select_related('customer')
        .order_by('-order_date', '-invoice_number')
    )
    rows = []
    for order in orders:
        address = delivery_address_for_order(order)
        google_url, yandex_url = maps_search_urls(address) if address else ('', '')
        phone = contact_phone_for_order(order)
        telegram = contact_telegram_for_order(order)
        telegram_name = telegram_username(telegram)
        rows.append(
            {
                'order': order,
                'address': address,
                'phone': phone,
                'phone_tel': phone_tel_uri(phone),
                'telegram_label': f'@{telegram_name}' if telegram_name else '',
                'telegram_url': telegram_profile_url(telegram),
                'google_maps_url': google_url,
                'yandex_maps_url': yandex_url,
            }
        )
    return render(
        request,
        'shop/staff/shipping_addresses.html',
        {
            'rows': rows,
            'staff_nav_active': 'shipping',
            'page_heading': 'Shipping',
        },
    )


@login_required
@user_passes_test(_staff_ok)
@require_POST
def staff_shipping_confirm_delivery(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    with transaction.atomic():
        locked = SalesOrder.objects.select_for_update().get(pk=order.pk)
        if locked.status not in OPEN_DELIVERY_STATUSES:
            messages.error(request, 'This order is not waiting for delivery.')
            return redirect('administration:staff_shipping_addresses')
        locked.status = SalesOrder.Status.COMPLETED
        locked.save(update_fields=['status', 'updated_at'])
        invoice = locked.invoice_number
    messages.success(request, f'Order {invoice} marked completed.')
    return redirect('administration:staff_shipping_addresses')
