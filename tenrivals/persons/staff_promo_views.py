"""Staff promo code CRUD (superuser)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from shop.models import Product, ProductCollection, PromoCode
from shop.promo_codes import annotate_promo_usage_counts, normalize_promo_code


def _superuser_required(user):
    return bool(user and user.is_superuser)


def _parse_optional_datetime(raw: str):
    s = (raw or '').strip()
    if not s:
        return None
    d = parse_datetime(s)
    if d is None:
        try:
            from datetime import datetime

            d = datetime.fromisoformat(s)
        except ValueError:
            return None
    if timezone.is_naive(d):
        d = timezone.make_aware(d, timezone.get_current_timezone())
    return d


def _parse_decimal(raw: str, default: Decimal | None = None) -> Decimal | None:
    s = (raw or '').strip()
    if not s:
        return default
    try:
        return Decimal(s).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None


@login_required
@user_passes_test(_superuser_required)
def staff_promo_codes(request):
    qs = PromoCode.objects.all()
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(title__icontains=q))
    active = (request.GET.get('active') or '').strip().lower()
    if active == '1':
        qs = qs.filter(is_manually_active=True)
    elif active == '0':
        qs = qs.filter(is_manually_active=False)
    sort = (request.GET.get('sort') or 'sort_order').strip()
    if sort == 'code':
        qs = qs.order_by('code')
    elif sort == '-created':
        qs = qs.order_by('-created_at')
    else:
        qs = qs.order_by('sort_order', 'code')
    promos = list(annotate_promo_usage_counts(qs))
    now = timezone.now()
    return render(
        request,
        'persons/staff_promo_codes.html',
        {
            'staff_nav_active': 'promo_codes',
            'page_heading': 'Promo codes',
            'page_note': 'Create and manage checkout discount codes.',
            'promos': promos,
            'filter_q': q,
            'filter_active': active,
            'filter_sort': sort,
            'now': now,
        },
    )


@login_required
@user_passes_test(_superuser_required)
def staff_promo_edit(request, promo_id: int | None = None):
    promo = get_object_or_404(PromoCode, pk=promo_id) if promo_id else None
    is_create = promo is None
    products = Product.objects.filter(is_active=True).order_by('name', 'id')[:3000]
    collections = ProductCollection.objects.filter(is_archived=False).order_by('title', 'id')
    users = get_user_model().objects.filter(is_active=True).order_by('email', 'id')[:500]

    if request.method == 'POST':
        code = normalize_promo_code(request.POST.get('code', ''))
        if not code:
            messages.error(request, 'Code is required.')
            if is_create:
                return redirect('administration:staff_promo_new')
            return redirect('administration:staff_promo_edit', promo_id=promo.pk)
        dup = PromoCode.objects.filter(code__iexact=code)
        if promo:
            dup = dup.exclude(pk=promo.pk)
        if dup.exists():
            messages.error(request, 'A promo with this code already exists.')
            if is_create:
                return redirect('administration:staff_promo_new')
            return redirect('administration:staff_promo_edit', promo_id=promo.pk)

        target = PromoCode() if is_create else promo
        target.code = code
        target.title = (request.POST.get('title') or '').strip()[:160]
        target.is_manually_active = request.POST.get('is_manually_active') == 'on'
        target.discount_type = (request.POST.get('discount_type') or PromoCode.DiscountType.PERCENT).strip()
        if target.discount_type not in {c[0] for c in PromoCode.DiscountType.choices}:
            target.discount_type = PromoCode.DiscountType.PERCENT

        target.application_scope = (
            request.POST.get('application_scope') or PromoCode.ApplicationScope.ALL
        ).strip()
        if target.application_scope not in {c[0] for c in PromoCode.ApplicationScope.choices}:
            target.application_scope = PromoCode.ApplicationScope.ALL

        fa = _parse_decimal(request.POST.get('fixed_amount_gross', ''))
        target.fixed_amount_gross = fa if target.discount_type == PromoCode.DiscountType.FIXED_GROSS else None
        cap = _parse_decimal(request.POST.get('fixed_cap_percent_of_eligible', '100'), Decimal('100'))
        if cap is None or cap < Decimal('0'):
            cap = Decimal('100')
        target.fixed_cap_percent_of_eligible = min(Decimal('100'), cap)

        po = _parse_decimal(request.POST.get('percent_off', ''))
        target.percent_off = po if target.discount_type == PromoCode.DiscountType.PERCENT else None
        pm = _parse_decimal(request.POST.get('percent_min_eligible_subtotal', '0'), Decimal('0'))
        target.percent_min_eligible_subtotal = pm or Decimal('0.00')

        target.valid_from = _parse_optional_datetime(request.POST.get('valid_from', ''))
        target.valid_until = _parse_optional_datetime(request.POST.get('valid_until', ''))

        target.single_use_globally = request.POST.get('single_use_globally') == 'on'
        maxu = (request.POST.get('max_redemptions') or '').strip()
        target.max_redemptions = int(maxu) if maxu.isdigit() and int(maxu) > 0 else None

        uid = (request.POST.get('restricted_to_user') or '').strip()
        if uid.isdigit():
            target.restricted_to_user_id = int(uid)
        else:
            target.restricted_to_user_id = None

        so = (request.POST.get('sort_order') or '0').strip()
        try:
            target.sort_order = int(so)
        except ValueError:
            target.sort_order = 0

        target.save()

        pids = [int(x) for x in request.POST.getlist('restricted_products') if str(x).isdigit()]
        cids = [int(x) for x in request.POST.getlist('restricted_collections') if str(x).isdigit()]
        target.restricted_products.set(Product.objects.filter(pk__in=pids))
        target.restricted_collections.set(ProductCollection.objects.filter(pk__in=cids))

        messages.success(request, 'Promo code saved.')
        return redirect('administration:staff_promo_codes')

    promo_product_ids: set[int] = set()
    promo_collection_ids: set[int] = set()
    if promo:
        promo_product_ids = set(promo.restricted_products.values_list('pk', flat=True))
        promo_collection_ids = set(promo.restricted_collections.values_list('pk', flat=True))

    ctx = {
        'staff_nav_active': 'promo_codes',
        'page_heading': 'New promo code' if is_create else f'Edit promo: {promo.code}',
        'page_note': '',
        'promo': promo,
        'is_create': is_create,
        'products': products,
        'collections': collections,
        'users': users,
        'promo_product_ids': promo_product_ids,
        'promo_collection_ids': promo_collection_ids,
        'discount_types': PromoCode.DiscountType.choices,
        'application_scopes': PromoCode.ApplicationScope.choices,
    }
    return render(request, 'persons/staff_promo_code_form.html', ctx)
