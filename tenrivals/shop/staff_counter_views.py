"""Touch-first counter for an offline shop."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from django.db.models import Prefetch
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
from .models import CounterStore, Product, ProductListing, ProductListingChannel, ProductType, SalesOrder
from .sales_order_stock import get_variant_qty_map, product_requires_variant
from .sales_order_utils import stock_listing_quantity
from .site_locale import shop_reverse

SESSION_STORE = 'counter_store_id'
SESSION_CART = 'counter_cart'


def _staff_ok(user):
    return bool(user.is_authenticated and user.is_superuser)


def _store(request) -> CounterStore | None:
    raw = request.session.get(SESSION_STORE)
    if not raw:
        return None
    return CounterStore.objects.filter(pk=raw, is_active=True).first()


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
    codes = {row.variant_label: row.barcode for row in product.barcodes.all()}
    variants = []
    if product_requires_variant(product):
        variants = [
            {'label': key, 'qty': int(qty), 'barcode': codes.get(key, '')}
            for key, qty in get_variant_qty_map(product).items()
            if int(qty or 0) > 0
        ]
        variants.sort(key=lambda row: _size_sort_key(row['label']))
    listings = getattr(product, 'stock_listings', None) or []
    listing_qty = int(listings[0].quantity) if listings else 0
    if variants:
        stock_qty = sum(row['qty'] for row in variants)
        barcode = ', '.join(codes[row['label']] for row in variants if codes.get(row['label']))
    else:
        stock_qty = listing_qty
        barcode = codes.get('', '')
    return {
        'id': product.pk,
        'name': product.name,
        'brand': product.brand or '',
        'price': shelf_price(product),
        'image': image.url if image else '',
        'variants': variants,
        'needs_variant': bool(variants),
        'stock_qty': stock_qty,
        'barcode': barcode,
        'codes': codes,
        'detail_url': shop_reverse('shop:product_detail', product.pk),
    }


def _spec_rows(product: Product) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []

    def add(label: str, value) -> None:
        text = '' if value is None else str(value).strip()
        if text:
            rows.append((label, text))

    add('SKU', product.sku)
    add('Type', product.get_type_display())
    add('Color', product.color)
    if product.category_id:
        add('Category', product.category.name)
    if product.type == ProductType.RACKET:
        try:
            racket = product.racket
        except ObjectDoesNotExist:
            racket = None
        if racket is not None:
            if racket.weight_grams:
                state = 'strung' if racket.is_strung else 'unstrung'
                add('Weight', f'{racket.weight_grams} g, {state}')
            if racket.head_size_sq_in:
                add('Head size', f'{racket.head_size_sq_in} sq in')
            add('String pattern', racket.string_pattern)
            if racket.length_in:
                add('Length', f'{racket.length_in} in')
            if racket.balance_mm:
                add('Balance', f'{racket.balance_mm} mm')
            add('Swingweight', racket.swingweight)
    elif product.type in {
        ProductType.MENS_SHOES,
        ProductType.WOMENS_SHOES,
        ProductType.JUNIOR_SHOES,
    }:
        try:
            shoe = product.shoe
        except ObjectDoesNotExist:
            shoe = None
        if shoe is not None:
            add('Surface', shoe.get_surface_display())
            add('Gender', shoe.get_gender_display())
            add('Width', shoe.width)
    elif product.type in {
        ProductType.MENS_APPAREL,
        ProductType.WOMENS_APPAREL,
        ProductType.JUNIOR_APPAREL,
    }:
        try:
            apparel = product.apparel
        except ObjectDoesNotExist:
            apparel = None
        if apparel is not None:
            add('Gender', apparel.get_gender_display())
            add('Material', apparel.material)
    elif product.type == ProductType.STRINGS:
        try:
            string = product.string
        except ObjectDoesNotExist:
            string = None
        if string is not None:
            add('Material', string.material)
            if string.length_m:
                add('Length', f'{string.length_m} m')
    elif product.type == ProductType.BAGS:
        try:
            bag = product.bag
        except ObjectDoesNotExist:
            bag = None
        if bag is not None and bag.capacity_rackets:
            add('Racket capacity', bag.capacity_rackets)
    elif product.type == ProductType.BALLS:
        try:
            balls = product.balls
        except ObjectDoesNotExist:
            balls = None
        if balls is not None:
            if balls.balls_per_can:
                add('Balls per can', balls.balls_per_can)
            add('Court surface', balls.get_surface_display())
    attrs = product.attributes if isinstance(product.attributes, dict) else {}
    for key, value in attrs.items():
        if key in {'source_images', 'source_url'} or isinstance(value, (list, dict)):
            continue
        add(str(key), value)
    return rows


def _panel_images(product: Product) -> list[str]:
    urls = []
    for field in ('image_1', 'image_2', 'image_3', 'image_4', 'image_5'):
        image = getattr(product, field, None)
        if image:
            urls.append(image.url)
    return urls


def _size_sort_key(label: str):
    parts = re.split(r'(\d+(?:\.\d+)?)', label or '')
    key = []
    for part in parts:
        if re.fullmatch(r'\d+(?:\.\d+)?', part or ''):
            key.append((0, float(part)))
        elif part:
            key.append((1, part.lower()))
    return key


def _href(**params) -> str:
    pairs = [(key, value) for key, value in params.items() if value]
    return ('?' + urlencode(pairs)) if pairs else '?'


def _kept_query(request) -> str:
    pairs = []
    for key in ('type', 'brand', 'size'):
        value = (request.POST.get(key) or '').strip()
        if value:
            pairs.append((key, value))
    return ('?' + urlencode(pairs)) if pairs else ''


def _stock_products():
    return (
        Product.objects.filter(
            is_active=True,
            listings__channel=ProductListingChannel.STOCK,
            listings__quantity__gt=0,
        )
        .distinct()
        .select_related('racket', 'shoe', 'apparel', 'string')
        .prefetch_related(
            'barcodes',
            Prefetch(
                'listings',
                queryset=ProductListing.objects.filter(channel=ProductListingChannel.STOCK),
                to_attr='stock_listings',
            ),
        )
        .order_by('brand', 'name', 'id')
    )


def _catalog(type_filter: str, brand_filter: str = '', size_filter: str = ''):
    products = list(_stock_products())
    present = {product.type for product in products}
    types = [
        {'value': value, 'label': label}
        for value, label in ProductType.choices
        if value in present
    ]
    if type_filter not in dict(ProductType.choices):
        type_filter = ''
        brand_filter = ''
        size_filter = ''
    else:
        products = [product for product in products if product.type == type_filter]
    cards = [_product_card(product) for product in products]
    brands = sorted({card['brand'] for card in cards if card['brand']})
    sizes = sorted(
        {variant['label'] for card in cards for variant in card['variants']},
        key=_size_sort_key,
    )
    if brand_filter not in brands:
        brand_filter = ''
    if size_filter not in sizes:
        size_filter = ''
    if brand_filter:
        cards = [card for card in cards if card['brand'] == brand_filter]
    if size_filter:
        narrowed = []
        for card in cards:
            hit = next((row for row in card['variants'] if row['label'] == size_filter), None)
            if hit is None:
                continue
            shown = dict(card)
            shown['stock_qty'] = hit['qty']
            shown['barcode'] = card['codes'].get(size_filter, '')
            shown['add_variant'] = size_filter
            narrowed.append(shown)
        cards = narrowed
    for card in cards:
        card.setdefault('add_variant', '')
        card['pick_href'] = _href(
            type=type_filter,
            brand=brand_filter,
            size=size_filter,
            pick=card['id'],
        )
    return type_filter, brand_filter, size_filter, types, brands, sizes, cards


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
        raw_id = (request.POST.get('store_id') or '').strip()
        store = CounterStore.objects.filter(pk=raw_id, is_active=True).first() if raw_id.isdigit() else None
        if store is None:
            messages.error(request, 'Choose a shop.')
        else:
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
            'stores': CounterStore.objects.filter(is_active=True),
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


def _clean_store_name(raw: str) -> str:
    return ' '.join((raw or '').split())[:80]


@login_required
@user_passes_test(_staff_ok)
def staff_counter_stores(request):
    """Shop list for the counter. Superuser-only so cashier access can stay wider."""
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if action == 'create':
            name = _clean_store_name(request.POST.get('name') or '')
            if not name:
                messages.error(request, 'Enter a shop name.')
            else:
                last = CounterStore.objects.order_by('-sort_order').values_list('sort_order', flat=True).first() or 0
                try:
                    CounterStore.objects.create(name=name, sort_order=last + 1)
                except IntegrityError:
                    messages.error(request, 'A shop with this name already exists.')
                else:
                    messages.success(request, f'{name} added.')
        elif action == 'rename':
            store = get_object_or_404(CounterStore, pk=request.POST.get('store_id') or 0)
            name = _clean_store_name(request.POST.get('name') or '')
            if not name:
                messages.error(request, 'Enter a shop name.')
            else:
                store.name = name
                try:
                    store.save(update_fields=['name'])
                except IntegrityError:
                    messages.error(request, 'A shop with this name already exists.')
                else:
                    messages.success(request, 'Shop renamed.')
        elif action == 'toggle':
            store = get_object_or_404(CounterStore, pk=request.POST.get('store_id') or 0)
            store.is_active = not store.is_active
            store.save(update_fields=['is_active'])
            messages.success(request, f'{store.name} is {"shown" if store.is_active else "hidden"} on the counter.')
        elif action == 'delete':
            store = get_object_or_404(CounterStore, pk=request.POST.get('store_id') or 0)
            label = store.name
            store.delete()
            messages.success(request, f'{label} removed. Past orders keep the shop name in their notes.')
        else:
            messages.error(request, 'Unknown action.')
        return redirect('administration:staff_counter_stores')
    return render(
        request,
        'persons/staff_counter_stores.html',
        {
            'stores': CounterStore.objects.all(),
            'staff_nav_active': 'counter_stores',
            'page_heading': 'Shops',
            'page_note': '',
        },
    )


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
            return redirect(
                reverse('administration:staff_counter_sale_edit', args=[order.pk]) + _kept_query(request)
            )
        return redirect(reverse('administration:staff_counter_sale') + _kept_query(request))

    type_filter, brand_filter, size_filter, types, brands, sizes, cards = _catalog(
        (request.GET.get('type') or '').strip(),
        (request.GET.get('brand') or '').strip(),
        (request.GET.get('size') or '').strip(),
    )
    panel = None
    close_href = _href(type=type_filter, brand=brand_filter, size=size_filter)
    pick_id = (request.GET.get('pick') or '').strip()
    if pick_id.isdigit():
        product = (
            Product.objects.filter(pk=int(pick_id))
            .select_related('category', 'racket', 'shoe', 'apparel', 'string', 'bag', 'balls', 'accessory')
            .prefetch_related(
                'barcodes',
                Prefetch(
                    'listings',
                    queryset=ProductListing.objects.filter(channel=ProductListingChannel.STOCK),
                    to_attr='stock_listings',
                ),
            )
            .first()
        )
        if product is not None:
            panel = _product_card(product)
            panel.setdefault('add_variant', '')
            panel['short_description'] = product.short_description or ''
            panel['description'] = product.description or ''
            panel['specs'] = _spec_rows(product)
            panel['images'] = _panel_images(product)
    resolved, total = _cart_rows_resolved(rows if order is None else _order_to_cart(order))
    return render(
        request,
        'persons/staff_counter_sale.html',
        {
            'store': store,
            'order': order,
            'types': types,
            'brands': brands,
            'sizes': sizes,
            'type_filter': type_filter,
            'brand_filter': brand_filter,
            'size_filter': size_filter,
            'cards': cards,
            'panel': panel,
            'close_href': close_href,
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
