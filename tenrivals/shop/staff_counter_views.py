"""Touch-first counter for an offline shop."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .barcodes import lookup_barcode
from .counter import (
    PAY_CARD,
    PAY_CASH,
    PAY_SPLIT,
    PAY_TRANSFER,
    cancel_counter_order,
    create_counter_order,
    pay_counter_order,
    replace_counter_lines,
    shelf_price,
)
from .models import CounterStore, Product, ProductListingChannel, ProductType, SalesOrder
from .sales_order_stock import get_variant_qty_map, product_requires_variant
from .sales_order_utils import stock_listing_quantity

SESSION_STORE = 'counter_store_id'
SESSION_CART = 'counter_cart'


def _staff_ok(user):
    return bool(user.is_authenticated and user.is_superuser)


def _store(request) -> CounterStore | None:
    raw = request.session.get(SESSION_STORE)
    if not raw:
        return None
    return CounterStore.objects.filter(pk=raw).first()


def _need_store(request):
    store = _store(request)
    if store is None:
        return None, redirect('administration:staff_counter')
    return store, None


def _parse_price(raw: str) -> Decimal | None:
    try:
        return Decimal(str(raw).replace(',', '.').strip()).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None


def _cart(request) -> list[dict]:
    raw = request.session.get(SESSION_CART) or []
    return raw if isinstance(raw, list) else []


def _save_cart(request, rows: list[dict]) -> None:
    request.session[SESSION_CART] = rows
    request.session.modified = True


def _product_card(product: Product) -> dict:
    image = product.main_image
    variants = []
    if product_requires_variant(product):
        variants = [
            {'label': key, 'qty': int(qty)}
            for key, qty in sorted(get_variant_qty_map(product).items())
            if int(qty or 0) > 0
        ]
    return {
        'id': product.pk,
        'name': product.name,
        'brand': product.brand or '',
        'price': shelf_price(product),
        'image': image.url if image else '',
        'variants': variants,
        'needs_variant': bool(variants),
    }


def _catalog(type_filter: str):
    products = (
        Product.objects.filter(
            is_active=True,
            listings__channel=ProductListingChannel.STOCK,
            listings__quantity__gt=0,
        )
        .distinct()
        .select_related('racket', 'shoe', 'apparel', 'string')
        .order_by('brand', 'name', 'id')
    )
    if type_filter in dict(ProductType.choices):
        products = products.filter(type=type_filter)
    else:
        type_filter = ''
    present = set(
        Product.objects.filter(
            is_active=True,
            listings__channel=ProductListingChannel.STOCK,
            listings__quantity__gt=0,
        ).values_list('type', flat=True)
    )
    types = [
        {'value': value, 'label': label}
        for value, label in ProductType.choices
        if value in present
    ]
    return type_filter, types, [_product_card(p) for p in products]


def _cart_rows_resolved(rows: list[dict]) -> list[dict]:
    ids = [int(r['product_id']) for r in rows if str(r.get('product_id', '')).isdigit()]
    products = {
        p.pk: p
        for p in Product.objects.filter(pk__in=ids).select_related('racket', 'shoe', 'apparel', 'string')
    }
    resolved = []
    total = Decimal('0.00')
    for row in rows:
        product = products.get(int(row['product_id']))
        if product is None:
            continue
        qty = int(row['qty'])
        price = Decimal(str(row['unit_price']))
        line_total = (price * qty).quantize(Decimal('0.01'))
        total += line_total
        image = product.main_image
        resolved.append(
            {
                'product': product,
                'variant': row.get('variant') or '',
                'qty': qty,
                'unit_price': price,
                'line_total': line_total,
                'image': image.url if image else '',
                'label': ' '.join(bit for bit in (product.brand, product.name) if bit),
            }
        )
    return resolved, total


def _rows_for_order(rows: list[dict]) -> list[dict]:
    ids = [int(r['product_id']) for r in rows]
    products = {p.pk: p for p in Product.objects.filter(pk__in=ids)}
    out = []
    for row in rows:
        product = products.get(int(row['product_id']))
        if product is None:
            continue
        out.append(
            {
                'product': product,
                'variant': row.get('variant') or '',
                'qty': int(row['qty']),
                'unit_price': row['unit_price'],
            }
        )
    return out


def _add_line(rows: list[dict], product: Product, variant: str, qty: int = 1) -> list[dict]:
    variant = (variant or '').strip()
    price = str(shelf_price(product))
    for row in rows:
        if int(row['product_id']) == product.pk and (row.get('variant') or '') == variant:
            row['qty'] = int(row['qty']) + qty
            return rows
    rows.append(
        {
            'product_id': product.pk,
            'variant': variant,
            'qty': qty,
            'unit_price': price,
        }
    )
    return rows


def _order_to_cart(order: SalesOrder) -> list[dict]:
    return [
        {
            'product_id': line.product_id,
            'variant': line.variant_label or '',
            'qty': int(line.quantity),
            'unit_price': str(line.unit_price_gross),
        }
        for line in order.lines.select_related('product').all()
        if line.product_id
    ]


@login_required
@user_passes_test(_staff_ok)
def staff_barcode_lookup(request):
    found = lookup_barcode(request.GET.get('barcode') or '')
    if found is None or not found.product.is_active:
        return JsonResponse({'ok': False, 'error': 'Barcode not found.'})
    product = found.product
    return JsonResponse(
        {
            'ok': True,
            'product_id': product.pk,
            'name': ' '.join(bit for bit in (product.brand, product.name) if bit),
            'variant': found.variant_label,
            'price': str(shelf_price(product)),
            'stock_qty': stock_listing_quantity(product.pk),
        }
    )


@login_required
@user_passes_test(_staff_ok)
def staff_counter(request):
    if request.method == 'POST':
        name = (request.POST.get('store_name') or '').strip()
        if not name:
            messages.error(request, 'Enter the shop name.')
        else:
            store, _created = CounterStore.objects.get_or_create(name=name[:80])
            request.session[SESSION_STORE] = store.pk
            return redirect('administration:staff_counter')
    store = _store(request)
    open_sales = []
    if store is not None:
        open_sales = list(
            SalesOrder.objects.filter(
                status=SalesOrder.Status.AWAITING_PAYMENT,
                notes__startswith='Counter sale',
                notes__contains=f'Store: {store.name}',
            ).order_by('-id')[:8]
        )
    return render(
        request,
        'persons/staff_counter_home.html',
        {
            'store': store,
            'stores': CounterStore.objects.all(),
            'open_sales': open_sales,
            'staff_nav_active': 'counter',
            'page_heading': 'Counter',
            'page_note': '',
        },
    )


@login_required
@user_passes_test(_staff_ok)
@require_POST
def staff_counter_clear_store(request):
    request.session.pop(SESSION_STORE, None)
    return redirect('administration:staff_counter')


@login_required
@user_passes_test(_staff_ok)
def staff_counter_sale(request, order_id: int | None = None):
    store, bounce = _need_store(request)
    if bounce:
        return bounce
    order = None
    if order_id:
        order = get_object_or_404(SalesOrder, pk=order_id)
        if order.status != SalesOrder.Status.AWAITING_PAYMENT:
            messages.error(request, 'This sale is already closed. Open it from Orders.')
            return redirect('administration:staff_sales_order_edit', pk=order.pk)
        rows = _order_to_cart(order)
    else:
        rows = _cart(request)

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        try:
            if action == 'add_barcode':
                found = lookup_barcode(request.POST.get('barcode') or '')
                if found is None:
                    raise ValueError('Barcode not found.')
                rows = _add_line(rows, found.product, found.variant_label)
            elif action == 'add_product':
                product = Product.objects.select_related('racket', 'shoe', 'apparel', 'string').get(
                    pk=int(request.POST.get('product_id') or 0)
                )
                variant = (request.POST.get('variant') or '').strip()
                if product_requires_variant(product) and not variant:
                    raise ValueError('Choose a size.')
                rows = _add_line(rows, product, variant)
            elif action == 'set_qty':
                idx = int(request.POST.get('index') or -1)
                qty = int(request.POST.get('qty') or 0)
                if qty < 1:
                    rows.pop(idx)
                else:
                    rows[idx]['qty'] = qty
            elif action == 'set_price':
                idx = int(request.POST.get('index') or -1)
                price = _parse_price(request.POST.get('unit_price') or '')
                if price is None or price < 0:
                    raise ValueError('Enter a valid price.')
                rows[idx]['unit_price'] = str(price)
            elif action == 'remove':
                rows.pop(int(request.POST.get('index') or -1))
            elif action == 'checkout':
                if order is not None:
                    replace_counter_lines(order, _rows_for_order(rows))
                    return redirect('administration:staff_counter_review', order_id=order.pk)
                created = create_counter_order(store_name=store.name, cart_rows=_rows_for_order(rows))
                _save_cart(request, [])
                return redirect('administration:staff_counter_review', order_id=created.pk)
            else:
                raise ValueError('Unknown action.')
        except (Product.DoesNotExist, ValueError, IndexError, TypeError) as exc:
            messages.error(request, str(exc) or 'Could not update the sale.')
        else:
            if order is not None and action != 'checkout':
                try:
                    replace_counter_lines(order, _rows_for_order(rows))
                except ValueError as exc:
                    messages.error(request, str(exc))
                    rows = _order_to_cart(order)
            elif order is None:
                _save_cart(request, rows)
        if order is not None:
            return redirect('administration:staff_counter_sale_edit', order_id=order.pk)
        return redirect(
            reverse('administration:staff_counter_sale')
            + (('?type=' + request.POST.get('type', '')) if request.POST.get('type') else '')
        )

    type_filter, types, cards = _catalog((request.GET.get('type') or '').strip())
    pick = None
    pick_id = (request.GET.get('pick') or '').strip()
    if pick_id.isdigit():
        pick = next((card for card in cards if card['id'] == int(pick_id)), None)
        if pick is None:
            product = Product.objects.filter(pk=int(pick_id)).select_related(
                'racket', 'shoe', 'apparel', 'string'
            ).first()
            if product is not None:
                pick = _product_card(product)
    resolved, total = _cart_rows_resolved(rows if order is None else _order_to_cart(order))
    return render(
        request,
        'persons/staff_counter_sale.html',
        {
            'store': store,
            'order': order,
            'types': types,
            'type_filter': type_filter,
            'cards': cards,
            'pick': pick,
            'lines': resolved,
            'total': total,
            'staff_nav_active': 'counter',
            'page_heading': 'New sale' if order is None else f'Sale {order.invoice_number}',
            'page_note': '',
        },
    )


@login_required
@user_passes_test(_staff_ok)
def staff_counter_review(request, order_id: int):
    store, bounce = _need_store(request)
    if bounce:
        return bounce
    order = get_object_or_404(
        SalesOrder.objects.prefetch_related('lines__product', 'payments'),
        pk=order_id,
    )
    if order.status != SalesOrder.Status.AWAITING_PAYMENT:
        messages.error(request, 'This sale is already closed. Open it from Orders.')
        return redirect('administration:staff_sales_order_edit', pk=order.pk)
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        try:
            if action == 'cancel':
                cancel_counter_order(order)
                messages.success(request, f'Sale {order.invoice_number} cancelled. Stock returned.')
                return redirect('administration:staff_counter')
            if action == 'pay':
                pay_counter_order(
                    order,
                    method=(request.POST.get('method') or '').strip(),
                    fiscal_receipt=request.POST.get('fiscal_receipt') or '',
                    cash_amount=_parse_price(request.POST.get('cash_amount') or '0'),
                    card_amount=_parse_price(request.POST.get('card_amount') or '0'),
                )
                messages.success(request, f'Sale {order.invoice_number} paid.')
                return redirect('administration:staff_counter')
            raise ValueError('Unknown action.')
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('administration:staff_counter_review', order_id=order.pk)
    lines = []
    for line in order.lines.all():
        title = line.display_title()
        if line.variant_label:
            title = f'{title} ({line.variant_label})'
        lines.append({'title': title, 'qty': line.quantity, 'total': line.line_gross, 'price': line.unit_price_gross})
    return render(
        request,
        'persons/staff_counter_review.html',
        {
            'store': store,
            'order': order,
            'lines': lines,
            'methods': [
                (PAY_CASH, 'Cash'),
                (PAY_CARD, 'Card'),
                (PAY_TRANSFER, 'Transfer'),
                (PAY_SPLIT, 'Split'),
            ],
            'staff_nav_active': 'counter',
            'page_heading': order.invoice_number,
            'page_note': '',
        },
    )
