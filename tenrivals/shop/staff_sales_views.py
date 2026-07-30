"""Superuser-only staff UI: retail customers & sales orders (invoices)."""

from __future__ import annotations

import calendar
import json
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.urls import reverse
from django.templatetags.static import static
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.views.decorators.http import require_POST

from .models import Customer, Product, SalesOrder, SalesOrderLine
from .sales_order_stock import (
    get_variant_qty_map,
    order_status_reserves_stock,
    product_requires_variant,
    release_lines_to_stock,
    snapshot_old_lines,
    take_lines_from_stock,
    validate_order_line_demands,
)
from .sales_order_utils import (
    allocate_invoice_number,
    compute_order_totals,
    gross_split_vat_net,
    line_amounts,
    max_issued_invoice_seq_for_year,
    parse_invoice_number,
    parse_services_payload,
    peek_next_invoice_number,
    product_unit_gross_price,
    set_next_invoice_number,
    stock_products_for_select,
)
from .staff_sales_forms import CustomerForm, SalesOrderForm, SalesOrderLineFormSet
from .email_links import absolute_url_for_email
from .sales_order_currency import (
    apply_payment_currency_fields,
    convert_gel_to_payment_currency,
    invoice_uses_foreign_currency,
    payment_currency_symbol,
)


def _staff_ok(user):
    return bool(user.is_authenticated and user.is_superuser)


def _sales_order_lines_prefetch():
    return Prefetch(
        'lines',
        queryset=SalesOrderLine.objects.select_related(
            'product',
            'product__racket',
            'product__shoe',
            'product__apparel',
            'product__string',
            'product__bag',
            'product__balls',
            'product__accessory',
        ).order_by('id'),
    )


def _invoice_context(order: SalesOrder, request=None) -> dict:
    use_fx = invoice_uses_foreign_currency(order)
    rate = order.exchange_rate or Decimal('1')
    cur = order.payment_currency_display()
    sym = payment_currency_symbol(cur)

    def _conv(amount: Decimal) -> Decimal:
        if not use_fx:
            return amount
        return convert_gel_to_payment_currency(amount, rate)

    lines = []
    any_disc = False
    for line in order.lines.all():
        d = line.discount_percent or Decimal('0')
        if d > 0:
            any_disc = True
        lines.append(
            {
                'name': line.invoice_display_label(),
                'qty': line.quantity,
                'disc': d,
                'unit': line.unit_price_gross,
                'gross': line.line_gross,
                'vat': line.line_vat,
                'net': line.line_net,
                'unit_fx': _conv(line.unit_price_gross),
                'gross_fx': _conv(line.line_gross),
                'vat_fx': _conv(line.line_vat),
                'net_fx': _conv(line.line_net),
            }
        )
    services = []
    for s in parse_services_payload(order.services):
        g = s['gross']
        net, vat = gross_split_vat_net(g)
        services.append(
            {
                'name': s['name'],
                'gross': g,
                'vat': vat,
                'net': net,
                'gross_fx': _conv(g),
                'vat_fx': _conv(vat),
                'net_fx': _conv(net),
            }
        )
    dg = order.delivery_gross
    delivery_gross = (
        Decimal(str(dg))
        if dg is not None
        else Decimal('0')
    ).quantize(Decimal('0.01'))
    delivery_net = delivery_vat = Decimal('0')
    if delivery_gross > 0:
        delivery_net, delivery_vat = gross_split_vat_net(delivery_gross)
    logo_rel = static('assets/img/invoice_logo_frame114.svg')
    logo_abs = None
    if request is not None:
        logo_abs = (
            logo_rel
            if logo_rel.startswith('http://') or logo_rel.startswith('https://')
            else request.build_absolute_uri(logo_rel)
        )
    return {
        'order': order,
        'lines': lines,
        'services': services,
        'delivery_gross': delivery_gross,
        'delivery_vat': delivery_vat,
        'delivery_net': delivery_net,
        'delivery_gross_fx': _conv(delivery_gross),
        'delivery_vat_fx': _conv(delivery_vat),
        'delivery_net_fx': _conv(delivery_net),
        'any_line_discount': any_disc,
        'customer_name': order.customer.display_name_for_invoice(),
        'embed_mode': False,
        'print_mode': False,
        'doc_date_display': order.order_date.strftime('%d.%m.%Y'),
        'invoice_logo_abs_url': logo_abs,
        'invoice_use_fx': use_fx,
        'invoice_currency': cur,
        'invoice_currency_symbol': sym,
        'invoice_exchange_rate': rate,
        'invoice_amount_foreign': order.amount_in_payment_currency,
    }


def _send_customer_order_confirmed_email(order: SalesOrder) -> None:
    recipient = (order.customer.email or '').strip()
    if not recipient:
        return
    logo_rel = '/static/assets/img/tr_footer_line_frame148.svg'
    logo_url = absolute_url_for_email(logo_rel, request=None)
    history_path = reverse('persons:shop_order_history')
    history_url = absolute_url_for_email(history_path, request=None)
    rows = [
        {
            'name': line.invoice_display_label(),
            'qty': int(line.quantity or 0),
            'line_total': line.line_gross,
        }
        for line in order.lines.all()
    ]
    ctx = {
        'order': order,
        'rows': rows,
        'history_url': history_url,
        'logo_url': logo_url,
        'company_name': 'Tennis Rivals Shop',
        'contact_tg': 'https://t.me/andyrivals',
        'contact_email': 'andy.rivals@tenrivals.com',
        'contact_phone': '+995 591 288 967',
    }
    html = render_to_string('shop/emails/order_confirmed.html', ctx)
    msg = EmailMultiAlternatives(
        subject=f'Tennis Rivals: Order Confirmed - {order.invoice_number}',
        body=strip_tags(html),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None) or 'no-reply@tenrivals.com',
        to=[recipient],
    )
    msg.attach_alternative(html, 'text/html')
    msg.send(fail_silently=True)


def _rebuild_order_from_formset(
    form: SalesOrderForm,
    formset: SalesOrderLineFormSet,
    services_payload,
) -> tuple[Decimal, Decimal, Decimal, list[dict], list[tuple]]:
    """Returns gross, vat, net, services_serializable, line_specs for save."""
    services = parse_services_payload(services_payload)
    svc_gross = [s['gross'] for s in services]
    delivery = form.cleaned_data.get('delivery_gross') or Decimal('0')
    line_grosses = []
    line_specs = []
    for f in formset.forms:
        cd = getattr(f, 'cleaned_data', None)
        if not cd or cd.get('DELETE'):
            continue
        lg, lv, ln = line_amounts(
            int(cd['quantity']),
            cd['unit_price_gross'],
            cd['discount_percent'] or Decimal('0'),
        )
        line_grosses.append(lg)
        line_specs.append(
            {
                'form': f,
                'cleaned': cd,
                'lg': lg,
                'lv': lv,
                'ln': ln,
            }
        )
    gross, vat, net = compute_order_totals(line_grosses, svc_gross, delivery)
    ser_out = [
        {
            'name': s['name'],
            'gross': str(s['gross']),
            'cost': str(s.get('cost') or Decimal('0.00')),
        }
        for s in services
    ]
    return gross, vat, net, ser_out, line_specs


def _product_queryset_for_order(instance: SalesOrder | None):
    base = stock_products_for_select()
    base_ids = list(base.values_list('pk', flat=True))
    if instance and instance.pk:
        line_ids = [
            pid
            for pid in instance.lines.values_list('product_id', flat=True)
            if pid is not None  # free-text lines have no product
        ]
        all_ids = set(base_ids) | set(line_ids)
        return (
            Product.objects.filter(pk__in=all_ids)
            .select_related(
                'shoe',
                'racket',
                'apparel',
                'string',
                'bag',
                'balls',
                'accessory',
            )
            .order_by('brand', 'name')
        )
    return base


@login_required
@user_passes_test(_staff_ok)
def staff_customers(request):
    q = (request.GET.get('q') or '').strip()
    qs = (
        Customer.objects.select_related('user')
        .annotate(orders_count=Count('sales_orders'))
        .order_by('last_name', 'first_name', 'id')
    )
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(name_local__icontains=q)
            | Q(surname_local__icontains=q)
            | Q(phone__icontains=q)
            | Q(email__icontains=q)
            | Q(tg_account__icontains=q)
        )
    paginator = Paginator(qs, 30)
    page_obj = paginator.get_page(request.GET.get('page'))
    page_numbers = list(
        paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1)
    )
    return render(
        request,
        'shop/staff/customers_list.html',
        {
            'customers': page_obj,
            'page_obj': page_obj,
            'paginator': paginator,
            'page_numbers': page_numbers,
            'search_q': q,
            'staff_nav_active': 'customers',
            'page_heading': 'Customers',
        },
    )


_REVENUE_EXCLUDED_STATUSES = frozenset(
    {SalesOrder.Status.CANCELLED, SalesOrder.Status.REFUNDED}
)


@login_required
@user_passes_test(_staff_ok)
def staff_customer_detail(request, pk):
    customer = get_object_or_404(Customer.objects.select_related('user'), pk=pk)
    orders = list(
        customer.sales_orders.prefetch_related(_sales_order_lines_prefetch())
        .order_by('-order_date', '-id')
    )
    revenue_orders = [o for o in orders if o.status not in _REVENUE_EXCLUDED_STATUSES]

    total_gross = sum((o.gross_total for o in revenue_orders), Decimal('0.00'))
    avg_order_gross = (
        (total_gross / len(revenue_orders)).quantize(Decimal('0.01'))
        if revenue_orders
        else Decimal('0.00')
    )
    last_order = orders[0] if orders else None
    first_order = orders[-1] if orders else None
    days_since_last_order = (
        (date.today() - last_order.order_date).days if last_order else None
    )

    items_total = 0
    product_totals: dict[str, dict] = {}
    payment_counts: dict[str, int] = {}
    for o in revenue_orders:
        pm = (o.payment_method or '').strip()
        if pm:
            payment_counts[pm] = payment_counts.get(pm, 0) + 1
        for line in o.lines.all():
            qty = int(line.quantity or 0)
            items_total += qty
            label = line.display_title()
            entry = product_totals.setdefault(
                label, {'label': label, 'qty': 0, 'gross': Decimal('0.00')}
            )
            entry['qty'] += qty
            entry['gross'] += line.line_gross
    top_products = sorted(
        product_totals.values(), key=lambda e: (-e['qty'], -e['gross'], e['label'])
    )[:5]
    preferred_payment = (
        max(payment_counts.items(), key=lambda kv: kv[1])[0] if payment_counts else ''
    )

    status_counts_map: dict[str, int] = {}
    for o in orders:
        status_counts_map[o.status] = status_counts_map.get(o.status, 0) + 1
    status_counts = [
        {'value': val, 'label': label, 'count': status_counts_map[val]}
        for val, label in SalesOrder.Status.choices
        if status_counts_map.get(val)
    ]

    return render(
        request,
        'shop/staff/customer_detail.html',
        {
            'customer': customer,
            'linked_user': customer.user,
            'orders': orders,
            'orders_count': len(orders),
            'revenue_orders_count': len(revenue_orders),
            'total_gross': total_gross,
            'avg_order_gross': avg_order_gross,
            'items_total': items_total,
            'first_order': first_order,
            'last_order': last_order,
            'days_since_last_order': days_since_last_order,
            'top_products': top_products,
            'preferred_payment': preferred_payment,
            'status_counts': status_counts,
            'staff_nav_active': 'customers',
            'page_heading': customer.display_name(),
        },
    )


@login_required
@user_passes_test(_staff_ok)
def staff_customer_edit(request, pk=None):
    instance = get_object_or_404(Customer, pk=pk) if pk else None
    if request.method == 'POST':
        form = CustomerForm(request.POST, instance=instance)
        if form.is_valid():
            saved = form.save()
            messages.success(request, 'Customer saved.')
            return redirect('administration:staff_customer_detail', pk=saved.pk)
    else:
        form = CustomerForm(instance=instance)
    return render(
        request,
        'shop/staff/customer_form.html',
        {
            'form': form,
            'customer': instance,
            'staff_nav_active': 'customers',
            'page_heading': 'Edit customer' if instance else 'New customer',
        },
    )


@login_required
@user_passes_test(_staff_ok)
@require_POST
def staff_customer_delete(request, pk):
    c = get_object_or_404(Customer, pk=pk)
    if c.sales_orders.exists():
        messages.error(request, 'Cannot delete: customer has sales orders.')
        return redirect('administration:staff_customers')
    c.delete()
    messages.success(request, 'Customer deleted.')
    return redirect('administration:staff_customers')


@login_required
@user_passes_test(_staff_ok)
def staff_sales_orders(request):
    q = (request.GET.get('q') or '').strip()
    today_y = date.today().year
    try:
        seq_year = int(request.GET.get('seq_year', today_y))
    except (TypeError, ValueError):
        seq_year = today_y
    if seq_year < 1990 or seq_year > 2100:
        seq_year = today_y
    qs = (
        SalesOrder.objects.select_related('customer')
        .prefetch_related(_sales_order_lines_prefetch())
        .order_by('-invoice_number')
    )
    if q:
        qs = qs.filter(
            Q(invoice_number__icontains=q)
            | Q(fiscal_receipt__icontains=q)
            | Q(customer__first_name__icontains=q)
            | Q(customer__last_name__icontains=q)
            | Q(customer__phone__icontains=q)
            | Q(customer__email__icontains=q)
        )
    seq_year_options = list(range(today_y - 4, today_y + 7))
    max_seq = max_issued_invoice_seq_for_year(seq_year)
    month_choices = [(m, calendar.month_name[m]) for m in range(1, 13)]
    return render(
        request,
        'shop/staff/sales_orders_list.html',
        {
            'orders': qs[:500],
            'search_q': q,
            'order_status_choices': SalesOrder.Status.choices,
            'staff_nav_active': 'sales_orders',
            'page_heading': 'Orders',
            'seq_year': seq_year,
            'seq_year_options': seq_year_options,
            'peek_next_invoice': peek_next_invoice_number(seq_year),
            'max_issued_seq': max_seq,
            'month_choices': month_choices,
            'report_year': _safe_int(
                request.GET.get('report_year'), date.today().year, 1990, 2100
            ),
            'report_month': _safe_int(
                request.GET.get('report_month'), date.today().month, 1, 12
            ),
        },
    )


def _safe_int(raw, default: int, lo: int, hi: int) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    if v < lo or v > hi:
        return default
    return v


@login_required
@user_passes_test(_staff_ok)
@require_POST
def staff_sales_invoice_sequence_set(request):
    full = (request.POST.get('next_invoice_full') or '').strip()
    try:
        set_next_invoice_number(full)
    except ValueError as e:
        messages.error(request, str(e))
        py = parse_invoice_number(full)
        redir_year = py[0] if py else date.today().year
        return redirect(f"{reverse('administration:staff_sales_orders')}?seq_year={redir_year}")
    messages.success(request, f'Next new order will be assigned invoice number {full.strip()}.')
    py = parse_invoice_number(full)
    redir_year = py[0] if py else date.today().year
    return redirect(f"{reverse('administration:staff_sales_orders')}?seq_year={redir_year}")


@login_required
@user_passes_test(_staff_ok)
def staff_sales_orders_month_report(request):
    today = date.today()
    y = _safe_int(request.GET.get('year'), today.year, 1990, 2100)
    m = _safe_int(request.GET.get('month'), today.month, 1, 12)
    qs = (
        SalesOrder.objects.filter(order_date__year=y, order_date__month=m)
        .order_by('invoice_number')
    )
    agg = qs.aggregate(
        tg=Sum('gross_total'),
        tv=Sum('vat_total'),
        tn=Sum('net_total'),
    )
    tg = (agg['tg'] or Decimal('0')).quantize(Decimal('0.01'))
    tv = (agg['tv'] or Decimal('0')).quantize(Decimal('0.01'))
    tn = (agg['tn'] or Decimal('0')).quantize(Decimal('0.01'))
    logo_rel = static('assets/img/invoice_logo_frame114.svg')
    logo_abs = (
        request.build_absolute_uri(logo_rel)
        if not (logo_rel.startswith('http://') or logo_rel.startswith('https://'))
        else logo_rel
    )
    month_title = calendar.month_name[m]
    return render(
        request,
        'shop/staff/sales_orders_month_report.html',
        {
            'report_year': y,
            'report_month': m,
            'month_title': month_title,
            'orders': qs,
            'total_gross': tg,
            'total_vat': tv,
            'total_net': tn,
            'invoice_logo_abs_url': logo_abs,
        },
    )


@login_required
@user_passes_test(_staff_ok)
def staff_sales_order_edit(request, pk=None):
    instance = get_object_or_404(SalesOrder, pk=pk) if pk else None
    stock_qs = _product_queryset_for_order(instance)
    product_prices = {p.pk: str(product_unit_gross_price(p)) for p in stock_qs}
    # Staff-only page: per-unit landed costs for JS autofill on product change.
    product_landed_costs = {
        p.pk: (str(p.landed_cost_gel) if p.landed_cost_gel is not None else '')
        for p in stock_qs
    }

    if request.method == 'POST':
        form = SalesOrderForm(request.POST, instance=instance)
        services_raw = request.POST.get('services_json', '[]')
        if form.is_valid():
            parent = form.save(commit=False)
            formset = SalesOrderLineFormSet(request.POST, instance=parent)
        else:
            parent = instance or SalesOrder()
            formset = SalesOrderLineFormSet(request.POST, instance=parent)
        for f in formset.forms:
            if hasattr(f, 'fields') and 'product' in f.fields:
                f.fields['product'].queryset = stock_qs
        if form.is_valid() and formset.is_valid():
            gross, vat, net, ser_out, line_specs = _rebuild_order_from_formset(
                form, formset, services_raw
            )
            if not line_specs:
                messages.error(request, 'Add at least one product line.')
            else:
                try:
                    with transaction.atomic():
                        if instance and instance.pk:
                            locked = SalesOrder.objects.select_for_update().get(pk=instance.pk)
                            old_lines = snapshot_old_lines(locked)
                            if order_status_reserves_stock(locked.status):
                                release_lines_to_stock(old_lines)

                        order = parent
                        if instance is None:
                            order.invoice_number = allocate_invoice_number(
                                order.order_date.year
                            )
                        order.gross_total = gross
                        order.vat_total = vat
                        order.net_total = net
                        order.services = ser_out
                        apply_payment_currency_fields(order, gross)
                        order.save()
                        order.lines.all().delete()
                        for spec in line_specs:
                            cd = spec['cleaned']
                            vl = (cd.get('variant_label') or '').strip()
                            product = cd.get('product')
                            ptype = (cd.get('product_type') or '').strip()
                            if product is not None and not ptype:
                                ptype = product.type or ''
                            SalesOrderLine.objects.create(
                                order=order,
                                product=product,
                                custom_label=(cd.get('custom_label') or '').strip(),
                                product_type=ptype,
                                landed_cost_gel=cd.get('landed_cost_gel'),
                                sale_channel=(
                                    cd.get('sale_channel')
                                    or SalesOrderLine.SaleChannel.STOCK
                                ),
                                variant_label=vl,
                                quantity=cd['quantity'],
                                unit_price_gross=cd['unit_price_gross'],
                                discount_percent=cd['discount_percent'] or Decimal('0'),
                                line_gross=spec['lg'],
                                line_vat=spec['lv'],
                                line_net=spec['ln'],
                            )
                        if order_status_reserves_stock(order.status):
                            new_lines = list(
                                order.lines.select_related(
                                    'product',
                                    'product__racket',
                                    'product__shoe',
                                ).all()
                            )
                            take_lines_from_stock(new_lines)
                    messages.success(request, 'Order saved.')
                    return redirect('administration:staff_sales_orders')
                except Exception as e:
                    messages.error(request, f'Could not save order: {e}')
    else:
        initial = {}
        if instance is None:
            initial['order_date'] = date.today()
            # Preselect customer when coming from a customer card (?customer=<pk>).
            cust_raw = (request.GET.get('customer') or '').strip()
            if cust_raw.isdigit():
                cust_pk = Customer.objects.filter(pk=int(cust_raw)).values_list(
                    'pk', flat=True
                ).first()
                if cust_pk:
                    initial['customer'] = cust_pk
        form = SalesOrderForm(instance=instance, initial=initial)
        formset = SalesOrderLineFormSet(instance=instance or SalesOrder())

    if request.method == 'POST':
        services_for_js = request.POST.get('services_json', '[]')
    else:
        services_for_js = json.dumps(instance.services or []) if instance else '[]'

    for f in formset.forms:
        if hasattr(f, 'fields') and 'product' in f.fields:
            f.fields['product'].queryset = stock_qs

    old_lines = []
    old_status = None
    if instance and instance.pk:
        old_lines = snapshot_old_lines(instance)
        old_status = instance.status

    catalog_variants = {}
    for p in stock_qs:
        pid = str(p.pk)
        if product_requires_variant(p):
            effective = dict(get_variant_qty_map(p))
            if instance and instance.pk and order_status_reserves_stock(old_status):
                for ol in old_lines:
                    if ol.product_id != p.pk:
                        continue
                    vk = (ol.variant_label or '').strip()
                    if not vk:
                        continue
                    effective[vk] = int(effective.get(vk, 0) or 0) + int(ol.quantity or 0)
            catalog_variants[pid] = effective
        else:
            catalog_variants[pid] = {}

    return render(
        request,
        'shop/staff/sales_order_form.html',
        {
            'form': form,
            'formset': formset,
            'order': instance,
            'product_prices': product_prices,
            'product_landed_costs': product_landed_costs,
            # Pass a dict; |json_script in the template serializes once. json.dumps here
            # would double-encode and JSON.parse in the browser yields a string, not an object.
            'product_variants_json': catalog_variants,
            'services_json_initial': services_for_js,
            'staff_nav_active': 'sales_orders',
            'page_heading': ('Edit order ' + instance.invoice_number)
            if instance
            else 'New order',
        },
    )


@login_required
@user_passes_test(_staff_ok)
@require_POST
def staff_sales_order_delete(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    inv = order.invoice_number
    try:
        with transaction.atomic():
            locked = SalesOrder.objects.select_for_update().get(pk=order.pk)
            lines = snapshot_old_lines(locked)
            if order_status_reserves_stock(locked.status):
                release_lines_to_stock(lines)
            locked.delete()
    except Exception as e:
        messages.error(request, f'Could not delete order: {e}')
        return redirect('administration:staff_sales_orders')
    messages.success(request, f'Deleted order {inv}.')
    return redirect('administration:staff_sales_orders')


@login_required
@user_passes_test(_staff_ok)
@require_POST
def staff_sales_order_set_status(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    raw = (request.POST.get('status') or '').strip()
    valid = {c[0] for c in SalesOrder.Status.choices}
    if raw not in valid:
        messages.error(request, 'Invalid status.')
        return redirect('administration:staff_sales_orders')
    try:
        send_customer_confirmed = False
        with transaction.atomic():
            locked = SalesOrder.objects.select_for_update().get(pk=order.pk)
            old = locked.status
            lines = snapshot_old_lines(locked)
            old_res = order_status_reserves_stock(old)
            new_res = order_status_reserves_stock(raw)
            if old_res and not new_res:
                release_lines_to_stock(lines)
            elif not old_res and new_res:
                demands = [
                    (
                        ln.product,
                        (ln.variant_label or '').strip(),
                        int(ln.quantity),
                    )
                    for ln in lines
                    if ln.product_id is not None
                ]
                v_errs = validate_order_line_demands(
                    demands,
                    order_pk=locked.pk,
                    old_status=old,
                    old_lines=lines,
                )
                if v_errs:
                    raise ValueError('; '.join(v_errs))
                take_lines_from_stock(lines)
            locked.status = raw
            locked.save(update_fields=['status', 'updated_at'])
            if raw == SalesOrder.Status.CONFIRMED and old != SalesOrder.Status.CONFIRMED:
                send_customer_confirmed = True
    except Exception as e:
        messages.error(request, f'Could not update status: {e}')
        return redirect('administration:staff_sales_orders')
    if send_customer_confirmed:
        _send_customer_order_confirmed_email(locked)
    messages.success(request, 'Status updated.')
    return redirect('administration:staff_sales_orders')


@login_required
@user_passes_test(_staff_ok)
def staff_sales_order_invoice(request, pk):
    order = get_object_or_404(
        SalesOrder.objects.select_related('customer').prefetch_related(
            _sales_order_lines_prefetch()
        ),
        pk=pk,
    )
    ctx = _invoice_context(order, request=request)
    ctx['print_mode'] = request.GET.get('print') == '1'
    ctx['embed_mode'] = False
    return render(request, 'shop/staff/sales_invoice.html', ctx)
