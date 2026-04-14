"""Session-backed storefront cart (no checkout / payment yet)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Prefetch
from django.utils import timezone

from .models import Product, ProductListing, ProductListingChannel, UserCart, UserCartLine
from .sales_order_stock import get_variant_qty_map, product_requires_variant, variant_qty_available
from .sales_order_utils import product_unit_gross_price, stock_listing_quantity


SESSION_CART_KEY = 'shop_cart_v1'
CART_TTL_DAYS = 20


def _norm_variant(v) -> str:
    if v is None:
        return ''
    if not isinstance(v, str):
        v = str(v)
    return v.strip()


def sole_in_stock_variant_key(product: Product) -> str | None:
    """If exactly one variant has qty > 0, return its key (for PDP / add-to-cart default)."""
    if not product_requires_variant(product):
        return None
    vm = get_variant_qty_map(product)
    pos = [k for k, v in sorted(vm.items()) if int(v or 0) > 0]
    if len(pos) == 1:
        return pos[0]
    return None


def _default_cart() -> dict[str, Any]:
    return {'lines': [], 'promo_code': ''}


def _clean_lines(lines) -> list[dict[str, Any]]:
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
    return cleaned


def _clean_promo(value: Any) -> str:
    if not isinstance(value, str):
        value = str(value or '')
    return value.strip()


def _is_authenticated(request) -> bool:
    user = getattr(request, 'user', None)
    return bool(user and getattr(user, 'is_authenticated', False))


def _session_payload(request) -> dict[str, Any]:
    raw = request.session.get(SESSION_CART_KEY)
    if not isinstance(raw, dict):
        return _default_cart()
    cleaned = _clean_lines(raw.get('lines'))
    return {'lines': cleaned, 'promo_code': _clean_promo(raw.get('promo_code'))}


def _save_session_payload(request, cart: dict[str, Any]) -> None:
    request.session[SESSION_CART_KEY] = {
        'lines': _clean_lines(cart.get('lines') or []),
        'promo_code': _clean_promo(cart.get('promo_code') or ''),
    }
    request.session.modified = True


def _clear_session_payload(request) -> None:
    request.session.pop(SESSION_CART_KEY, None)
    request.session.modified = True


def _load_user_cart_payload(user, *, create: bool = True) -> dict[str, Any]:
    cart = UserCart.objects.filter(user=user).first()
    if cart is None:
        if not create:
            return _default_cart()
        cart = UserCart.objects.create(user=user)
    if cart.updated_at and cart.updated_at < timezone.now() - timedelta(days=CART_TTL_DAYS):
        cart.lines.all().delete()
        cart.promo_code = ''
        cart.save(update_fields=['promo_code', 'updated_at'])
    lines = [
        {'product_id': ln.product_id, 'variant': _norm_variant(ln.variant), 'qty': int(ln.qty or 0)}
        for ln in cart.lines.order_by('id')
        if int(ln.qty or 0) > 0
    ]
    return {'lines': _clean_lines(lines), 'promo_code': _clean_promo(cart.promo_code)}


def _save_user_cart_payload(user, cart: dict[str, Any]) -> None:
    row, _ = UserCart.objects.get_or_create(user=user)
    cleaned_lines = _clean_lines(cart.get('lines') or [])
    row.promo_code = _clean_promo(cart.get('promo_code') or '')
    row.save(update_fields=['promo_code', 'updated_at'])
    row.lines.all().delete()
    if cleaned_lines:
        UserCartLine.objects.bulk_create(
            [
                UserCartLine(
                    cart=row,
                    product_id=int(ln['product_id']),
                    variant=_norm_variant(ln.get('variant')),
                    qty=int(ln.get('qty') or 0),
                )
                for ln in cleaned_lines
                if int(ln.get('qty') or 0) > 0
            ]
        )
        row.save(update_fields=['updated_at'])


def _merge_session_into_user_cart(request) -> None:
    if not _is_authenticated(request):
        return
    session_cart = _session_payload(request)
    if not session_cart['lines'] and not session_cart['promo_code']:
        return
    user = request.user
    user_cart = _load_user_cart_payload(user, create=True)
    merged: list[dict[str, Any]] = list(user_cart['lines'])
    idx_by_key: dict[tuple[int, str], int] = {
        (int(ln.get('product_id') or 0), _norm_variant(ln.get('variant'))): i
        for i, ln in enumerate(merged)
    }
    for ln in session_cart['lines']:
        key = (int(ln.get('product_id') or 0), _norm_variant(ln.get('variant')))
        i = idx_by_key.get(key)
        if i is None:
            idx_by_key[key] = len(merged)
            merged.append({'product_id': key[0], 'variant': key[1], 'qty': int(ln.get('qty') or 0)})
        else:
            merged[i]['qty'] = int(merged[i].get('qty') or 0) + int(ln.get('qty') or 0)
    merged_cart = {
        'lines': _clean_lines(merged),
        'promo_code': _clean_promo(session_cart.get('promo_code') or user_cart.get('promo_code') or ''),
    }
    _save_user_cart_payload(user, merged_cart)
    _clear_session_payload(request)


def get_cart(request) -> dict[str, Any]:
    if _is_authenticated(request):
        _merge_session_into_user_cart(request)
        return _load_user_cart_payload(request.user, create=True)
    return _session_payload(request)


def save_cart(request, cart: dict[str, Any]) -> None:
    if _is_authenticated(request):
        _save_user_cart_payload(request.user, cart)
        _clear_session_payload(request)
        return
    _save_session_payload(request, cart)


def _legacy_get_cart_compat(request) -> dict[str, Any]:
    """Back-compat for old imports/tests if needed."""
    return get_cart(request)


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
    vk = _norm_variant(variant_key)
    other = 0
    for i, ln in enumerate(lines):
        if exclude_line_index is not None and i == exclude_line_index:
            continue
        if int(ln.get('product_id') or 0) != product.pk:
            continue
        if _norm_variant(ln.get('variant')) != vk:
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

    vk = _norm_variant(variant)
    if product_requires_variant(product):
        vmap = get_variant_qty_map(product)
        if not vmap or not any(int(x or 0) > 0 for x in vmap.values()):
            return False, 'No sizes in stock for this product.', None
        if not vk:
            sole = sole_in_stock_variant_key(product)
            if sole:
                vk = sole
            else:
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
        if _norm_variant(ln.get('variant')) != vk:
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
    title = product.storefront_cart_line_title()
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
        vk = _norm_variant(ln.get('variant'))
        product = (
            Product.objects.filter(pk=pid, is_active=True)
            .select_related('shoe', 'racket', 'apparel', 'string')
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
    vk = _norm_variant(ln.get('variant'))

    product = (
        Product.objects.filter(pk=pid, is_active=True)
        .select_related('shoe', 'racket', 'apparel', 'string')
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
    vk = _norm_variant(variant_key)
    pid = int(product_id)
    for j, ln in enumerate(lines):
        try:
            lp = int(ln.get('product_id') or 0)
        except (TypeError, ValueError):
            continue
        if lp != pid:
            continue
        lv = _norm_variant(ln.get('variant'))
        if lv != vk:
            continue
        lines.pop(j)
        cart['lines'] = lines
        save_cart(request, cart)
        return True
    return False


def remove_line_at_index_verified(
    request, line_index: int, product_id: int, variant_key: str
) -> bool:
    """Remove line at index only if it matches product_id and variant (avoids wrong-row deletes)."""
    cart = get_cart(request)
    lines = list(cart['lines'])
    if line_index < 0 or line_index >= len(lines):
        return False
    ln = lines[line_index]
    try:
        if int(ln.get('product_id') or 0) != int(product_id):
            return False
    except (TypeError, ValueError):
        return False
    if _norm_variant(ln.get('variant')) != _norm_variant(variant_key):
        return False
    lines.pop(line_index)
    cart['lines'] = lines
    save_cart(request, cart)
    return True


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
        vk = _norm_variant(ln.get('variant'))
        product = (
            Product.objects.filter(pk=pid, is_active=True)
            .select_related('shoe', 'racket', 'apparel', 'string')
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
                'name': product.storefront_cart_line_title(),
                'variant_label': vk or '—',
                'variant_key': vk,
                'qty': qty,
                'max_qty': max(max_q, qty),
                'unit_price': unit,
                'line_total': line_total,
            }
        )

    return rows, subtotal
