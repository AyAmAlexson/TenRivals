"""Sales orders: variant-aware stock checks, reservation (listing + JSON), release on cancel/delete."""

from __future__ import annotations

from collections import defaultdict
from .models import Product, ProductListing, ProductListingChannel, ProductType, SalesOrder
from .sales_order_utils import stock_listing_quantity
from .size_inventory import normalize_sizes_to_qty_map

SHOE_TYPES = frozenset(
    {
        ProductType.MENS_SHOES,
        ProductType.WOMENS_SHOES,
        ProductType.JUNIOR_SHOES,
    }
)

APPAREL_TYPES = frozenset(
    {
        ProductType.MENS_APPAREL,
        ProductType.WOMENS_APPAREL,
        ProductType.JUNIOR_APPAREL,
    }
)


def order_status_reserves_stock(status: str) -> bool:
    """Counts against warehouse except cancelled/refunded."""
    return status not in (
        SalesOrder.Status.CANCELLED,
        SalesOrder.Status.REFUNDED,
    )


def product_requires_variant(product: Product) -> bool:
    if product.type == ProductType.RACKET:
        return True
    if product.type in SHOE_TYPES:
        return True
    if product.type in APPAREL_TYPES:
        return True
    if product.type == ProductType.STRINGS:
        return True
    return False


def get_variant_qty_map(product: Product) -> dict[str, int]:
    """Per-grip, shoe size, or apparel size quantities (normalized dict)."""
    listing_total = stock_listing_quantity(product.pk)
    if product.type == ProductType.RACKET:
        try:
            rk = product.racket
        except Exception:
            return {}
        return normalize_sizes_to_qty_map(rk.grip_sizes, fallback_total=listing_total)
    if product.type in SHOE_TYPES:
        try:
            sh = product.shoe
        except Exception:
            return {}
        return normalize_sizes_to_qty_map(sh.sizes, fallback_total=listing_total)
    if product.type in APPAREL_TYPES:
        try:
            ap = product.apparel
        except Exception:
            return {}
        return normalize_sizes_to_qty_map(ap.sizes, fallback_total=listing_total)
    if product.type == ProductType.STRINGS:
        try:
            st = product.string
        except Exception:
            return {}
        # Match racket/shoe/apparel: legacy `gauges` as a list only encodes labels; qty lives on the listing.
        m = normalize_sizes_to_qty_map(st.gauges, fallback_total=listing_total)
        if m:
            return m
        if st.gauge_mm is not None and listing_total > 0:
            return {f'{st.gauge_mm} mm': listing_total}
        return {}
    return {}


def variant_qty_available(product: Product, variant_key: str) -> int:
    vk = (variant_key or '').strip()
    if not product_requires_variant(product):
        return stock_listing_quantity(product.pk)
    m = get_variant_qty_map(product)
    return int(m.get(vk, 0))


def staff_order_product_option_label(product: Product) -> str:
    """Long label for order form dropdown: brand, model, type, specs, variant stock."""
    type_lbl = product.get_type_display()
    title = product.invoice_line_title()
    specs = (product.invoice_line_specs_slash() or '').strip() or '—'
    if product_requires_variant(product):
        m = get_variant_qty_map(product)
        if m:
            var_txt = ', '.join(f'{k}×{v}' for k, v in sorted(m.items()) if int(v or 0) > 0) or 'no qty'
        else:
            var_txt = 'no variants'
    else:
        var_txt = f"listing×{stock_listing_quantity(product.pk)}"
    return f'{title} — {type_lbl} — {specs} — {var_txt}'


def _listing_row_for_update(product_id: int) -> ProductListing | None:
    return (
        ProductListing.objects.select_for_update()
        .filter(product_id=product_id, channel=ProductListingChannel.STOCK)
        .first()
    )


def _apply_listing_delta(product_id: int, delta: int) -> None:
    row = _listing_row_for_update(product_id)
    if not row:
        raise ValueError(f'No STOCK listing for product {product_id}')
    nq = int(row.quantity) + int(delta)
    if nq < 0:
        raise ValueError('Insufficient stock (listing)')
    row.quantity = nq
    row.save(update_fields=['quantity'])


def adjust_product_variant_stock(product: Product, variant_key: str, delta: int) -> None:
    """
    delta negative = sell / reserve; positive = return to stock.
    Updates ProductListing.quantity and racket.grip_sizes / shoe.sizes dict when applicable.
    """
    vk = (variant_key or '').strip()
    if not product_requires_variant(product):
        _apply_listing_delta(product.pk, delta)
        return

    if not vk:
        m0 = get_variant_qty_map(product)
        nz = [k for k, v in sorted(m0.items()) if int(v or 0) > 0]
        if len(nz) == 1:
            vk = nz[0]
        elif len(nz) == 0 and delta > 0:
            _apply_listing_delta(product.pk, delta)
            return
        else:
            raise ValueError('Select grip, shoe size, apparel size, or string gauge for this product.')

    if product.type == ProductType.RACKET:
        from .models import Racket

        rk = Racket.objects.select_for_update().get(pk=product.pk)
        row = _listing_row_for_update(product.pk)
        if not row:
            raise ValueError('No STOCK listing for racket')
        d = normalize_sizes_to_qty_map(rk.grip_sizes, fallback_total=int(row.quantity))
        cur = int(d.get(vk, 0))
        new_v = cur + delta
        if new_v < 0:
            raise ValueError(f'Insufficient stock for grip {vk}')
        d[vk] = new_v
        rk.grip_sizes = d
        rk.save(update_fields=['grip_sizes'])
        row.quantity = sum(int(x or 0) for x in d.values())
        row.save(update_fields=['quantity'])
        return

    if product.type in SHOE_TYPES:
        from .models import Shoe

        sh = Shoe.objects.select_for_update().get(pk=product.pk)
        row = _listing_row_for_update(product.pk)
        if not row:
            raise ValueError('No STOCK listing for shoe')
        d = normalize_sizes_to_qty_map(sh.sizes, fallback_total=int(row.quantity))
        cur = int(d.get(vk, 0))
        new_v = cur + delta
        if new_v < 0:
            raise ValueError(f'Insufficient stock for size {vk}')
        d[vk] = new_v
        sh.sizes = d
        sh.save(update_fields=['sizes'])
        row.quantity = sum(int(x or 0) for x in d.values())
        row.save(update_fields=['quantity'])
        return

    if product.type == ProductType.STRINGS:
        from .models import String

        st = String.objects.select_for_update().get(pk=product.pk)
        row = _listing_row_for_update(product.pk)
        if not row:
            raise ValueError('No STOCK listing for string')
        d = dict(
            normalize_sizes_to_qty_map(st.gauges, fallback_total=int(row.quantity))
        )
        if not d and st.gauge_mm is not None:
            label = f'{st.gauge_mm} mm'
            d = {label: int(row.quantity)}
        cur = int(d.get(vk, 0))
        new_v = cur + delta
        if new_v < 0:
            raise ValueError(f'Insufficient stock for gauge {vk}')
        d[vk] = new_v
        st.gauges = d
        st.save(update_fields=['gauges'])
        row.quantity = sum(int(x or 0) for x in d.values())
        row.save(update_fields=['quantity'])
        return

    _apply_listing_delta(product.pk, delta)


def release_lines_to_stock(lines: list) -> None:
    """Return reserved units to listing / variant JSON. Best-effort if legacy lines lack variant."""
    for line in lines:
        try:
            adjust_product_variant_stock(line.product, line.variant_label or '', line.quantity)
        except ValueError as exc:
            # Legacy lines: variant-required SKU but empty variant_label — restore listing total only.
            if (
                product_requires_variant(line.product)
                and not (line.variant_label or '').strip()
                and (
                    'Select grip or shoe size' in str(exc)
                    or 'Select grip, shoe size' in str(exc)
                )
            ):
                _apply_listing_delta(line.product.pk, int(line.quantity))
            else:
                raise


def take_lines_from_stock(lines: list) -> None:
    for line in lines:
        adjust_product_variant_stock(line.product, line.variant_label or '', -int(line.quantity))


def snapshot_old_lines(order: SalesOrder) -> list:
    return list(
        order.lines.select_related(
            'product',
            'product__racket',
            'product__shoe',
            'product__apparel',
            'product__string',
        ).all()
    )


def available_qty_for_demand(
    product: Product,
    variant_key: str,
    *,
    order_pk: int | None,
    old_status: str | None,
    old_lines: list,
) -> int:
    """Stock in DB plus units already tied to this order (if that order still reserves)."""
    base = variant_qty_available(product, variant_key)
    if order_pk and old_status and order_status_reserves_stock(old_status):
        vk = (variant_key or '').strip()
        for ol in old_lines:
            if ol.product_id == product.pk and (ol.variant_label or '').strip() == vk:
                base += int(ol.quantity)
    return base


def validate_order_line_demands(
    demands: list[tuple[Product, str, int]],
    *,
    order_pk: int | None,
    old_status: str | None,
    old_lines: list,
) -> list[str]:
    """Returns list of error messages (empty if ok)."""
    grouped: defaultdict[tuple[int, str], int] = defaultdict(int)
    for prod, var, qty in demands:
        if qty <= 0:
            continue
        key = (prod.pk, (var or '').strip())
        grouped[key] += qty

    errors = []
    for (pid, vk), need in sorted(grouped.items()):
        try:
            p = Product.objects.select_related(
                'racket', 'shoe', 'apparel', 'string'
            ).get(pk=pid)
        except Product.DoesNotExist:
            errors.append(f'Unknown product #{pid}.')
            continue
        avail = available_qty_for_demand(p, vk, order_pk=order_pk, old_status=old_status, old_lines=old_lines)
        if need > avail:
            label = p.invoice_line_title()
            col = (p.color or '').strip()
            if col:
                label = f'{label} — {col}'
            if product_requires_variant(p) and vk:
                label = f'{label} ({vk})'
            errors.append(
                f'Not enough stock for {label}: need {need}, available {avail}.'
            )
    return errors
