"""Staff shipping queue: open orders and their delivery addresses."""

from __future__ import annotations

from urllib.parse import quote_plus

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import SalesOrder

# Statuses that still reserve stock and still need a delivery.
# Completed is delivered. Cancelled and refunded are not current addresses,
# and marking them completed would pull stock again.
OPEN_DELIVERY_STATUSES = (
    SalesOrder.Status.SUBMITTED,
    SalesOrder.Status.AWAITING_PAYMENT,
    SalesOrder.Status.CONFIRMED,
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
        rows.append(
            {
                'order': order,
                'address': address,
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
