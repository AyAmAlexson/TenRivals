"""Superuser-only staff UI: retail customers & sales orders (invoices)."""

from __future__ import annotations

import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import Customer, Product, SalesOrder, SalesOrderLine
from .sales_order_utils import (
    allocate_invoice_number,
    compute_order_totals,
    gross_split_vat_net,
    line_amounts,
    parse_services_payload,
    product_unit_gross_price,
    stock_products_for_select,
)
from .staff_sales_forms import CustomerForm, SalesOrderForm, SalesOrderLineFormSet


def _staff_ok(user):
    return bool(user.is_authenticated and user.is_superuser)


def _invoice_pdf_bytes(request, order: SalesOrder) -> bytes:
    ctx = _invoice_context(order)
    ctx['embed_mode'] = True
    ctx['print_mode'] = False
    html = render_to_string(
        'shop/staff/sales_invoice.html',
        ctx,
        request=request,
    )
    try:
        from weasyprint import HTML
    except ImportError as e:
        raise RuntimeError(
            'WeasyPrint is not installed. Add weasyprint to requirements and reinstall.'
        ) from e
    base_url = request.build_absolute_uri('/')[:-1]
    return HTML(string=html, base_url=base_url).write_pdf()


def _invoice_context(order: SalesOrder) -> dict:
    lines = []
    any_disc = False
    for line in order.lines.select_related('product').all():
        d = line.discount_percent or Decimal('0')
        if d > 0:
            any_disc = True
        lines.append(
            {
                'name': line.product.name,
                'qty': line.quantity,
                'disc': d,
                'unit': line.unit_price_gross,
                'gross': line.line_gross,
                'vat': line.line_vat,
                'net': line.line_net,
            }
        )
    services = []
    raw = order.services or []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                g = Decimal(str(item.get('gross', '0') or '0'))
                if g <= 0:
                    continue
                net, vat = gross_split_vat_net(g)
                services.append(
                    {
                        'name': item.get('name', ''),
                        'gross': g,
                        'vat': vat,
                        'net': net,
                    }
                )
    delivery_gross = order.delivery_gross or Decimal('0')
    delivery_net = delivery_vat = Decimal('0')
    if delivery_gross > 0:
        delivery_net, delivery_vat = gross_split_vat_net(delivery_gross)
    return {
        'order': order,
        'lines': lines,
        'services': services,
        'delivery_gross': delivery_gross,
        'delivery_vat': delivery_vat,
        'delivery_net': delivery_net,
        'any_line_discount': any_disc,
        'customer_name': order.customer.display_name_for_invoice(),
        'embed_mode': False,
        'print_mode': False,
        'doc_date_display': order.order_date.strftime('%d.%m.%Y'),
    }


def _save_invoice_pdf_to_media(request, order: SalesOrder) -> Path:
    pdf = _invoice_pdf_bytes(request, order)
    out_dir = Path(settings.MEDIA_ROOT) / 'staff_invoices'
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_num = order.invoice_number.replace(' ', '_')
    fn = f'Invoice_{safe_num}_{order.order_date.isoformat()}.pdf'
    path = out_dir / fn
    path.write_bytes(pdf)
    return path


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
    ser_out = [{'name': s['name'], 'gross': str(s['gross'])} for s in services]
    return gross, vat, net, ser_out, line_specs


def _product_queryset_for_order(instance: SalesOrder | None):
    base = stock_products_for_select()
    base_ids = list(base.values_list('pk', flat=True))
    if instance and instance.pk:
        line_ids = list(instance.lines.values_list('product_id', flat=True))
        all_ids = set(base_ids) | set(line_ids)
        return (
            Product.objects.filter(pk__in=all_ids)
            .select_related('shoe', 'racket', 'apparel')
            .order_by('brand', 'name')
        )
    return base


@login_required
@user_passes_test(_staff_ok)
def staff_customers(request):
    q = (request.GET.get('q') or '').strip()
    qs = Customer.objects.all().order_by('last_name', 'first_name', 'id')
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
    return render(
        request,
        'shop/staff/customers_list.html',
        {
            'customers': qs[:500],
            'search_q': q,
            'staff_nav_active': 'customers',
            'page_heading': 'Customers',
        },
    )


@login_required
@user_passes_test(_staff_ok)
def staff_customer_edit(request, pk=None):
    instance = get_object_or_404(Customer, pk=pk) if pk else None
    if request.method == 'POST':
        form = CustomerForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, 'Customer saved.')
            return redirect('administration:staff_customers')
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
    qs = (
        SalesOrder.objects.select_related('customer')
        .prefetch_related(
            Prefetch('lines', queryset=SalesOrderLine.objects.select_related('product'))
        )
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
    return render(
        request,
        'shop/staff/sales_orders_list.html',
        {
            'orders': qs[:500],
            'search_q': q,
            'staff_nav_active': 'sales_orders',
            'page_heading': 'Orders',
        },
    )


@login_required
@user_passes_test(_staff_ok)
def staff_sales_order_edit(request, pk=None):
    instance = get_object_or_404(SalesOrder, pk=pk) if pk else None
    stock_qs = _product_queryset_for_order(instance)
    product_prices = {p.pk: str(product_unit_gross_price(p)) for p in stock_qs}

    if request.method == 'POST':
        form = SalesOrderForm(request.POST, instance=instance)
        parent = instance or SalesOrder()
        formset = SalesOrderLineFormSet(request.POST, instance=parent)
        for f in formset.forms:
            if hasattr(f, 'fields') and 'product' in f.fields:
                f.fields['product'].queryset = stock_qs
        services_raw = request.POST.get('services_json', '[]')
        if form.is_valid() and formset.is_valid():
            gross, vat, net, ser_out, line_specs = _rebuild_order_from_formset(
                form, formset, services_raw
            )
            if not line_specs:
                messages.error(request, 'Add at least one product line.')
            else:
                try:
                    with transaction.atomic():
                        order = form.save(commit=False)
                        if instance is None:
                            order.invoice_number = allocate_invoice_number(
                                order.order_date.year
                            )
                        order.gross_total = gross
                        order.vat_total = vat
                        order.net_total = net
                        order.services = ser_out
                        order.save()
                        order.lines.all().delete()
                        for spec in line_specs:
                            cd = spec['cleaned']
                            SalesOrderLine.objects.create(
                                order=order,
                                product=cd['product'],
                                quantity=cd['quantity'],
                                unit_price_gross=cd['unit_price_gross'],
                                discount_percent=cd['discount_percent'] or Decimal('0'),
                                line_gross=spec['lg'],
                                line_vat=spec['lv'],
                                line_net=spec['ln'],
                            )
                    messages.success(request, 'Order saved.')
                    return redirect('administration:staff_sales_orders')
                except Exception as e:
                    messages.error(request, f'Could not save order: {e}')
    else:
        initial = {}
        if instance is None:
            initial['order_date'] = date.today()
        form = SalesOrderForm(instance=instance, initial=initial)
        formset = SalesOrderLineFormSet(instance=instance or SalesOrder())

    if request.method == 'POST':
        services_for_js = request.POST.get('services_json', '[]')
    else:
        services_for_js = json.dumps(instance.services or []) if instance else '[]'

    for f in formset.forms:
        if hasattr(f, 'fields') and 'product' in f.fields:
            f.fields['product'].queryset = stock_qs

    return render(
        request,
        'shop/staff/sales_order_form.html',
        {
            'form': form,
            'formset': formset,
            'order': instance,
            'product_prices': product_prices,
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
    order.delete()
    messages.success(request, f'Deleted order {inv}.')
    return redirect('administration:staff_sales_orders')


@login_required
@user_passes_test(_staff_ok)
def staff_sales_order_invoice(request, pk):
    order = get_object_or_404(
        SalesOrder.objects.select_related('customer').prefetch_related(
            Prefetch('lines', queryset=SalesOrderLine.objects.select_related('product'))
        ),
        pk=pk,
    )
    ctx = _invoice_context(order)
    ctx['print_mode'] = request.GET.get('print') == '1'
    ctx['embed_mode'] = False
    return render(request, 'shop/staff/sales_invoice.html', ctx)


@login_required
@user_passes_test(_staff_ok)
def staff_sales_order_invoice_pdf(request, pk):
    order = get_object_or_404(
        SalesOrder.objects.select_related('customer').prefetch_related(
            Prefetch('lines', queryset=SalesOrderLine.objects.select_related('product'))
        ),
        pk=pk,
    )
    try:
        pdf = _invoice_pdf_bytes(request, order)
    except RuntimeError as e:
        messages.error(request, str(e))
        return redirect(reverse('administration:staff_sales_order_invoice', kwargs={'pk': pk}))
    path = _save_invoice_pdf_to_media(request, order)
    messages.info(request, f'PDF also saved to {path.relative_to(settings.MEDIA_ROOT)}')
    fn = path.name
    resp = FileResponse(
        io.BytesIO(pdf),
        as_attachment=True,
        filename=fn,
        content_type='application/pdf',
    )
    return resp
