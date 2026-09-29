"""Staff pages for the FIFO shelf stack, arrival dates, and non-sale write-offs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from shop.models import Product, ProductListing, ProductListingChannel, StockUnit, StockWriteOff
from shop.sales_order_stock import get_variant_qty_map, product_requires_variant
from shop.stock_units import (
    assign_received_on,
    local_day,
    record_write_off,
    redate_on_hand_batch,
    stack_drift,
    sync_on_hand_gaps,
    undo_write_off,
)


def _staff_ok(user):
    return bool(user.is_authenticated and user.is_superuser)


def _parse_date(raw: str) -> date | None:
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        return None


def _product_label(product: Product) -> str:
    bits = [product.brand or '', product.name]
    label = ' '.join(b for b in bits if b).strip()
    if product.color:
        label = f'{label} — {product.color}'
    return label


@login_required
@user_passes_test(_staff_ok)
def staff_stock_stack(request):
    sync_on_hand_gaps()
    status = (request.GET.get('status') or StockUnit.Status.ON_HAND).strip()
    valid = {c[0] for c in StockUnit.Status.choices} | {'all'}
    if status not in valid:
        status = StockUnit.Status.ON_HAND
    q = (request.GET.get('q') or '').strip()
    units = StockUnit.objects.select_related(
        'product',
        'sales_order_line__order',
        'write_off_line__write_off',
    )
    if status != 'all':
        units = units.filter(status=status)
    if q:
        units = units.filter(
            Q(product__name__icontains=q)
            | Q(product__brand__icontains=q)
            | Q(product__sku__icontains=q)
            | Q(variant_label__icontains=q)
        )
    if status == StockUnit.Status.ON_HAND:
        units = units.order_by(F('received_at').asc(nulls_last=True), 'id')
    else:
        units = units.order_by(F('sold_at').desc(nulls_last=True), F('written_off_at').desc(nulls_last=True), '-id')

    page = Paginator(units, 80).get_page(request.GET.get('page') or 1)
    today = date.today()
    rows = []
    for unit in page.object_list:
        received = local_day(unit.received_at)
        left = local_day(unit.sold_at or unit.written_off_at)
        age = None
        if received is not None:
            age = max(0, ((left or today) - received).days)
        left_via = ''
        if unit.sales_order_line_id and unit.sales_order_line.order_id:
            left_via = unit.sales_order_line.order.invoice_number
        elif unit.write_off_line_id and unit.write_off_line.write_off_id:
            left_via = unit.write_off_line.write_off.get_reason_display()
        rows.append(
            {
                'unit': unit,
                'label': _product_label(unit.product),
                'received_on': received,
                'age_days': age,
                'left_via': left_via,
            }
        )
    return render(
        request,
        'persons/staff_stock_stack.html',
        {
            'rows': rows,
            'page_obj': page,
            'status': status,
            'search_q': q,
            'status_choices': [('all', 'All')] + list(StockUnit.Status.choices),
            'drift': stack_drift() if status == StockUnit.Status.ON_HAND else [],
            'staff_nav_active': 'stock_stack',
            'page_heading': 'Shelf stack',
            'page_note': (
                'One row is one physical piece. On hand is sorted oldest first — '
                'that is the next unit a sale or write-off will take. '
                'Undated pieces are taken last.'
            ),
        },
    )


def _date_groups():
    units = (
        StockUnit.objects.filter(status=StockUnit.Status.ON_HAND)
        .select_related('product')
        .order_by('product__brand', 'product__name', 'variant_label', 'id')
    )
    groups: dict[tuple[int, str], dict] = {}
    for unit in units:
        key = (unit.product_id, unit.variant_label or '')
        group = groups.get(key)
        if group is None:
            group = {
                'product': unit.product,
                'label': _product_label(unit.product),
                'variant': unit.variant_label or '',
                'undated': 0,
                'batches': {},
            }
            groups[key] = group
        day = local_day(unit.received_at)
        if day is None:
            group['undated'] += 1
        else:
            group['batches'][day] = group['batches'].get(day, 0) + 1
    rows = []
    for group in groups.values():
        group['batch_rows'] = [
            {'date': day, 'qty': qty}
            for day, qty in sorted(group['batches'].items())
        ]
        rows.append(group)
    rows.sort(key=lambda g: ((g['product'].brand or '').lower(), g['product'].name.lower(), g['variant']))
    return rows


@login_required
@user_passes_test(_staff_ok)
def staff_stock_arrival_dates(request):
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        try:
            with transaction.atomic():
                if action == 'sync':
                    n = sync_on_hand_gaps()
                    messages.success(request, f'Stack synced. {n} undated unit(s) added for current stock.')
                elif action == 'assign':
                    raw_pid = int(request.POST.get('product_id') or 0)
                    product = Product.objects.get(pk=raw_pid)
                    variant = (request.POST.get('variant') or '').strip()
                    qty = int(request.POST.get('quantity') or 0)
                    received_on = _parse_date(request.POST.get('received_on') or '')
                    if received_on is None:
                        raise ValueError('Enter an arrival date.')
                    n = assign_received_on(
                        product=product,
                        variant_label=variant,
                        quantity=qty,
                        received_on=received_on,
                    )
                    messages.success(
                        request,
                        f'Dated {n} unit(s) of {product.name} as {received_on.isoformat()}.',
                    )
                elif action == 'redate':
                    raw_pid = int(request.POST.get('product_id') or 0)
                    product = Product.objects.get(pk=raw_pid)
                    variant = (request.POST.get('variant') or '').strip()
                    from_day = _parse_date(request.POST.get('from_day') or '')
                    to_day = _parse_date(request.POST.get('received_on') or '')
                    if from_day is None or to_day is None:
                        raise ValueError('Enter both dates.')
                    n = redate_on_hand_batch(
                        product=product,
                        variant_label=variant,
                        from_day=from_day,
                        to_day=to_day,
                    )
                    messages.success(request, f'Updated {n} unit(s) to {to_day.isoformat()}.')
                else:
                    raise ValueError('Unknown action.')
        except (Product.DoesNotExist, TypeError, ValueError) as exc:
            messages.error(request, str(exc) or 'Could not update arrival dates.')
        return redirect('administration:staff_stock_arrival_dates')

    sync_on_hand_gaps()
    return render(
        request,
        'persons/staff_stock_dates.html',
        {
            'groups': _date_groups(),
            'drift': stack_drift(),
            'staff_nav_active': 'stock_dates',
            'page_heading': 'Arrival dates',
            'page_note': (
                'Approximate arrival dates for pieces already on the shelf. '
                'A date applies to undated units of that product and size, oldest id first. '
                'New receipts are dated automatically.'
            ),
        },
    )


@login_required
@user_passes_test(_staff_ok)
def staff_write_offs(request):
    docs = (
        StockWriteOff.objects.prefetch_related('lines__product')
        .all()
    )
    rows = []
    for doc in docs[:200]:
        landed = sum((line.landed_cost_total or Decimal('0') for line in doc.lines.all()), Decimal('0'))
        bits = []
        for line in doc.lines.all():
            label = _product_label(line.product)
            if line.variant_label:
                label = f'{label} ({line.variant_label})'
            bits.append(f'{line.quantity}× {label}')
        rows.append({'doc': doc, 'summary': '; '.join(bits), 'landed': landed})
    return render(
        request,
        'persons/staff_write_offs.html',
        {
            'rows': rows,
            'staff_nav_active': 'write_offs',
            'page_heading': 'Write-offs',
            'page_note': (
                'Stock taken off the shelf without a sale — personal use, advertising, demo, '
                'barter, blogger mailers. Listing quantity and the shelf stack change. '
                'Revenue, profit, tax, and acquiring do not.'
            ),
        },
    )


@login_required
@user_passes_test(_staff_ok)
def staff_write_off_new(request):
    products = list(
        Product.objects.filter(
            is_active=True,
            listings__channel=ProductListingChannel.STOCK,
            listings__quantity__gt=0,
        )
        .distinct()
        .order_by('brand', 'name', 'id')
    )
    variant_map = {}
    for product in Product.objects.filter(pk__in=[p.pk for p in products]).select_related(
        'racket', 'shoe', 'apparel', 'string'
    ):
        if product_requires_variant(product):
            variant_map[str(product.pk)] = sorted(
                k for k, v in get_variant_qty_map(product).items() if int(v or 0) > 0
            )

    if request.method == 'POST':
        written_on = _parse_date(request.POST.get('written_on') or '')
        reason = (request.POST.get('reason') or '').strip()
        note = (request.POST.get('note') or '').strip()
        errors = []
        if written_on is None:
            errors.append('Enter the write-off date.')
        try:
            total_forms = int(request.POST.get('lines-TOTAL_FORMS') or 0)
        except (TypeError, ValueError):
            total_forms = 0
        line_specs = []
        for i in range(max(0, total_forms)):
            raw_pid = (request.POST.get(f'lines-{i}-product') or '').strip()
            if not raw_pid:
                continue
            try:
                product = Product.objects.get(pk=int(raw_pid))
            except (TypeError, ValueError, Product.DoesNotExist):
                errors.append(f'Line {i + 1}: select a valid product.')
                continue
            try:
                qty = int(request.POST.get(f'lines-{i}-quantity') or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty < 1:
                errors.append(f'Line {i + 1} ({product.name}): quantity must be at least 1.')
                continue
            line_specs.append(
                {
                    'product': product,
                    'variant_label': (request.POST.get(f'lines-{i}-variant') or '').strip(),
                    'quantity': qty,
                }
            )
        if not line_specs and not errors:
            errors.append('Add at least one product line.')
        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            try:
                doc = record_write_off(
                    written_on=written_on,
                    reason=reason,
                    note=note,
                    lines=line_specs,
                    created_by=request.user,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f'Write-off #{doc.pk} recorded. Stock and the shelf stack were updated.')
                return redirect('administration:staff_write_offs')

    return render(
        request,
        'persons/staff_write_off_form.html',
        {
            'products': products,
            'variant_map': variant_map,
            'reasons': StockWriteOff.Reason.choices,
            'today': date.today().isoformat(),
            'staff_nav_active': 'write_offs',
            'page_heading': 'New write-off',
            'page_note': 'Removes pieces from stock and from the FIFO stack. This is not an order and does not affect sales analytics.',
        },
    )


@login_required
@user_passes_test(_staff_ok)
@require_POST
def staff_write_off_undo(request, pk: int):
    try:
        undo_write_off(pk)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f'Write-off #{pk} undone. Units returned to stock.')
    return redirect('administration:staff_write_offs')
