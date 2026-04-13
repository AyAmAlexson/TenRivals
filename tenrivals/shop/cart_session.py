"""Session-backed storefront cart (no checkout / payment yet)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db.models import Prefetch

from .models import Product, ProductListing, ProductListingChannel
from .sales_order_stock import get_variant_qty_map, product_requires_variant, variant_qty_available
from .sales_order_utils import product_unit_gross_price, stock_listing_quantity


SESSION_CART_KEY = 'shop_cart_v1'


def _default_cart() -> dict[str, Any]:
    return {'lines': [], 'promo_code': ''}


def get_cart(request) -> dict[str, Any]:
    raw = request.session.get(SESSION_CART_KEY)
    if not isinstance(raw, dict):
        return _default_cart()
    lines = raw.get('lines')
    if not isinstance(lines, list):
        lines = []
    cleaned: list[dict[str, Any]] = []
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        try:
            pid = int(ln.get('product_id'))
            qty = int(ln.get('qty') or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or qty <= 0:
            continue
        var = (ln.get('variant') or '')
        if not isinstance(var, str):
            var = str(var)
        cleaned.append({'product_id': pid, 'variant': var.strip(), 'qty': qty})
    promo = raw.get('promo_code')
    if not isinstance(promo, str):
        promo = ''
    return {'lines': cleaned, 'promo_code': promo.strip()}


def save_cart(request, cart: dict[str, Any]) -> None:
    request.session[SESSION_CART_KEY] = {
        'lines': cart.get('lines') or [],
        'promo_code': (cart.get('promo_code') or '').strip(),
    }
    request.session.modified = True


def cart_line_count_units(cart: dict[str, Any]) -> int:
    return sum(int(x.get('qty') or 0) for x in cart.get('lines') or [])


def product_eligible_for_storefront_cart(product: Product) -> bool:
    if not product.is_active:
        return False
    if stock_listing_quantity(product.pk) <= 0:
        return False
    if not ProductListing.objects.filter(channel=ProductListingChannel.STOCK).exists():
        return True
    return product.listings.filter(channel=ProductListingChannel.STOCK).exists()


def max_qty_allowed_in_cart(
    product: Product,
    variant_key: str,
    lines: list[dict[str, Any]],
    *,
    exclude_line_index: int | None = None,
) -> int:
    """DB availability minus qty of same product+variant on other cart lines."""
    db_avail = variant_qty_available(product, variant_key)
    vk = (variant_key or '').strip()
    other = 0
    for i, ln in enumerate(lines):
        if exclude_line_index is not None and i == exclude_line_index:
            continue
        if int(ln.get('product_id') or 0) != product.pk:
            continue
        if (ln.get('variant') or '').strip() != vk:
            continue
        other += int(ln.get('qty') or 0)
    return max(0, db_avail - other)


def try_add_to_cart(
    request,
    *,
    product_id: int,
    variant: str,
    qty: int,
) -> tuple[bool, str, dict[str, Any] | None]:
    """
    Returns (ok, message, payload).
    payload on success: cart_count, added summary for modal.
    """
    if qty < 1:
        return False, 'Quantity must be at least 1.', None

    qs = Product.objects.filter(pk=product_id, is_active=True).select_related(
        'shoe',
        'racket',
        'apparel',
        'string',
        'bag',
        'balls',
        'accessory',
    ).prefetch_related(
        Prefetch('listings', queryset=ProductListing.objects.filter(channel=ProductListingChannel.STOCK))
    )
    product = qs.first()
    if not product:
        return False, 'Product not found or unavailable.', None

    if not product_eligible_for_storefront_cart(product):
        return False, 'This product is not available for purchase online.', None

    vk = (variant or '').strip()
    if product_requires_variant(product):
        vmap = get_variant_qty_map(product)
        if not vmap or not any(int(x or 0) > 0 for x in vmap.values()):
            return False, 'No sizes in stock for this product.', None
        if not vk:
            return False, 'Select a size before adding to cart.', None
        if int(vmap.get(vk, 0)) <= 0:
            return False, 'This size is out of stock or unavailable.', None
    else:
        vk = ''

    cart = get_cart(request)
    lines: list[dict[str, Any]] = list(cart['lines'])

    merge_idx: int | None = None
    for i, ln in enumerate(lines):
        if int(ln.get('product_id') or 0) != product.pk:
            continue
        if (ln.get('variant') or '').strip() != vk:
            continue
        merge_idx = i
        break

    if merge_idx is not None:
        cur = int(lines[merge_idx].get('qty') or 0)
        new_total = cur + qty
        cap = max_qty_allowed_in_cart(product, vk, lines, exclude_line_index=merge_idx)
        if new_total > cap:
            if cap <= cur:
                return False, f'Not enough stock (only {cap} available for this line).', None
            return False, f'Not enough stock. You can add at most {cap - cur} more (total available: {cap}).', None
        lines[merge_idx] = {'product_id': product.pk, 'variant': vk, 'qty': new_total}
    else:
        cap = max_qty_allowed_in_cart(product, vk, lines, exclude_line_index=None)
        if qty > cap:
            return False, f'Not enough stock ({cap} available).', None
        lines.append({'product_id': product.pk, 'variant': vk, 'qty': qty})

    cart['lines'] = lines
    save_cart(request, cart)

    unit = product_unit_gross_price(product)
    line_total = (Decimal(str(unit)) * Decimal(qty)).quantize(Decimal('0.01'))
    title = product.invoice_line_title()
    var_label = vk or '—'
    payload = {
        'cart_count': cart_line_count_units(cart),
        'added': {
            'title': title,
            'variant': var_label,
            'qty': qty,
            'line_total': str(line_total),
        },
    }
    return True, 'Added to cart.', payload


def prune_and_clamp_cart(request) -> None:
    """Drop invalid lines and clamp qty to current stock (call before rendering cart)."""
    cart = get_cart(request)
    lines = list(cart['lines'])
    if not lines:
        return
    new_lines: list[dict[str, Any]] = []
    changed = False
    for i, ln in enumerate(lines):
        try:
            pid = int(ln.get('product_id') or 0)
            qty = int(ln.get('qty') or 0)
        except (TypeError, ValueError):
            changed = True
            continue
        if pid <= 0 or qty < 1:
            changed = True
            continue
        vk = (ln.get('variant') or '').strip()
        if not isinstance(vk, str):
            vk = str(vk).strip()
        product = (
            Product.objects.filter(pk=pid, is_active=True)
            .select_related('shoe', 'racket', 'apparel')
            .first()
        )
        if not product or not product_eligible_for_storefront_cart(product):
            changed = True
            continue
        if product_requires_variant(product):
            if not vk:
                changed = True
                continue
            vm = get_variant_qty_map(product)
            if int(vm.get(vk, 0)) <= 0:
                changed = True
                continue
        cap = max_qty_allowed_in_cart(product, vk, lines, exclude_line_index=i)
        q = min(qty, cap)
        if q < 1:
            changed = True
            continue
        if q != qty:
            changed = True
        new_lines.append({'product_id': pid, 'variant': vk, 'qty': q})
    if changed or len(new_lines) != len(lines):
        cart['lines'] = new_lines
        save_cart(request, cart)


def set_cart_promo(request, code: str) -> None:
    cart = get_cart(request)
    cart['promo_code'] = (code or '').strip()
    save_cart(request, cart)


def set_line_qty(request, line_index: int, qty: int) -> tuple[bool, str]:
    cart = get_cart(request)
    lines = list(cart['lines'])
    if line_index < 0 or line_index >= len(lines):
        return False, 'Invalid line.'
    if qty < 1:
        return False, 'Quantity must be at least 1.'

    ln = lines[line_index]
    pid = int(ln['product_id'])
    vk = (ln.get('variant') or '').strip()

    product = (
        Product.objects.filter(pk=pid, is_active=True)
        .select_related('shoe', 'racket', 'apparel')
        .first()
    )
    if not product or not product_eligible_for_storefront_cart(product):
        lines.pop(line_index)
        cart['lines'] = lines
        save_cart(request, cart)
        return True, 'Removed invalid line.'

    cap = max_qty_allowed_in_cart(product, vk, lines, exclude_line_index=line_index)
    if qty > cap:
        return False, f'Maximum available: {cap}.'

    lines[line_index] = {'product_id': pid, 'variant': vk, 'qty': qty}
    cart['lines'] = lines
    save_cart(request, cart)
    return True, 'Cart updated.'


def remove_line(request, line_index: int) -> bool:
    cart = get_cart(request)
    lines = list(cart['lines'])
    if line_index < 0 or line_index >= len(lines):
        return False
    lines.pop(line_index)
    cart['lines'] = lines
    save_cart(request, cart)
    return True


def remove_line_by_product_variant(request, product_id: int, variant_key: str) -> bool:
    """Remove the first cart line matching product + variant (stable vs session index drift)."""
    cart = get_cart(request)
    lines = list(cart['lines'])
    vk = (variant_key or '').strip()
    pid = int(product_id)
    for j, ln in enumerate(lines):
        try:
            lp = int(ln.get('product_id') or 0)
        except (TypeError, ValueError):
            continue
        if lp != pid:
            continue
        lv = (ln.get('variant') or '').strip()
        if not isinstance(lv, str):
            lv = str(lv).strip()
        if lv != vk:
            continue
        lines.pop(j)
        cart['lines'] = lines
        save_cart(request, cart)
        return True
    return False


def build_cart_page_rows(request) -> tuple[list[dict[str, Any]], Decimal]:
    """Rows for template + subtotal (GEL, gross)."""
    prune_and_clamp_cart(request)
    cart = get_cart(request)
    lines = cart['lines']
    rows: list[dict[str, Any]] = []
    subtotal = Decimal('0.00')

    for i, ln in enumerate(lines):
        pid = int(ln.get('product_id') or 0)
        qty = int(ln.get('qty') or 0)
        vk = (ln.get('variant') or '').strip()
        product = (
            Product.objects.filter(pk=pid, is_active=True)
            .select_related('shoe', 'racket', 'apparel')
            .first()
        )
        if not product or not product_eligible_for_storefront_cart(product):
            continue
        unit = product_unit_gross_price(product)
        max_q = max_qty_allowed_in_cart(product, vk, lines, exclude_line_index=i)
        line_total = (Decimal(str(unit)) * Decimal(qty)).quantize(Decimal('0.01'))
        subtotal += line_total
        rows.append(
            {
                'index': i,
                'product': product,
                'name': product.invoice_line_title(),
                'variant_label': vk or '—',
                'variant_key': vk,
                'qty': qty,
                'max_qty': max(max_q, qty),
                'unit_price': unit,
                'line_total': line_total,
            }
        )

    return rows, subtotal
