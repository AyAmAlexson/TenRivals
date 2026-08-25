"""Staff views for the Buying section (mounted under /administration/buying/)."""

from __future__ import annotations

import json
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from .ai.base import AIProviderError
from .auth import buying_permission_required
from .forms import (
    BuyingBatchForm,
    BuyingBatchLineFormSet,
    BuyingRequestForm,
    CalculationRuleForm,
    FulfillmentProviderForm,
    FulfillmentRouteForm,
    FulfillmentWarehouseForm,
    NormalizedProductForm,
    OptimizationCandidateForm,
    OverrideForm,
    QuickSupplierForm,
    SupplierForm,
    SupplierOfferForm,
)
from .models import (
    BuyingBatch,
    BuyingBatchQuote,
    BuyingRequest,
    CalculationRule,
    CostScenario,
    FulfillmentProvider,
    FulfillmentRoute,
    FulfillmentWarehouse,
    FxRate,
    ManualOverride,
    NormalizedProduct,
    OptimizationCandidate,
    ProductMapping,
    Supplier,
    SupplierOffer,
)
from .services.normalization import normalize_buying_request
from .services.offers import rebuild_scenarios_for_offer, revise_offer
from .services.overrides import (
    OVERRIDABLE_COMPONENTS,
    audit_offer_changes,
    override_scenario_component,
    remove_scenario_override,
)


def _require(request, perm: str):
    if not request.user.has_perm(perm):
        raise PermissionDenied


# --------------------------------------------------------------------------- #
# Buying requests
# --------------------------------------------------------------------------- #

@buying_permission_required('buying.view')
def buying_requests(request):
    qs = BuyingRequest.objects.select_related('normalized_product', 'staff_user', 'client')
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(
            Q(original_query__icontains=q)
            | Q(normalized_product__brand__icontains=q)
            | Q(normalized_product__model_name__icontains=q)
        )
    status = (request.GET.get('status') or '').strip()
    if status:
        qs = qs.filter(status=status)
    if (request.GET.get('archived') or '') != '1':
        qs = qs.filter(is_archived=False)
    qs = qs.annotate(
        best_price=Min(
            'cost_scenarios__customer_price',
            filter=Q(
                cost_scenarios__status=CostScenario.Status.CALCULATED,
                cost_scenarios__is_hidden=False,
            ),
        )
    )
    return render(
        request,
        'buying/staff/requests_list.html',
        {
            'staff_nav_active': 'buying_requests',
            'page_heading': 'Buying requests',
            'requests': qs[:300],
            'filter_q': q,
            'filter_status': status,
            'statuses': BuyingRequest.Status.choices,
        },
    )


@buying_permission_required('buying.create')
def buying_request_new(request):
    form = BuyingRequestForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        buying_request = form.save(commit=False)
        buying_request.staff_user = request.user
        buying_request.save()
        if request.POST.get('action') == 'save_draft':
            messages.success(request, 'Draft saved.')
            return redirect('administration:buying_requests')
        return redirect('administration:buying_request_detail', pk=buying_request.pk)
    return render(
        request,
        'buying/staff/request_form.html',
        {
            'staff_nav_active': 'buying_new',
            'page_heading': 'New buying request',
            'form': form,
        },
    )


@buying_permission_required('buying.view')
def buying_request_detail(request, pk: int):
    buying_request = get_object_or_404(
        BuyingRequest.objects.select_related('normalized_product', 'staff_user', 'client'), pk=pk
    )

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'normalize':
            _require(request, 'buying.create')
            try:
                normalize_buying_request(buying_request)
                messages.success(request, 'Request normalized. Review the fields below before searching.')
            except AIProviderError as exc:
                messages.warning(
                    request,
                    f'AI normalization unavailable ({exc}). '
                    'Fill the normalized product fields manually and save — search still works.',
                )
            return redirect('administration:buying_request_detail', pk=pk)

        if action == 'save_normalized':
            _require(request, 'buying.create')
            product = buying_request.normalized_product or NormalizedProduct()
            form = NormalizedProductForm(request.POST, instance=product)
            if form.is_valid():
                from buying.services.normalization import mark_staff_field_sources

                product = form.save(commit=False)
                product.edited_by_staff = True
                mark_staff_field_sources(product, list(form.changed_data))
                product.save()
                if not buying_request.normalized_product_id:
                    buying_request.normalized_product = product
                buying_request.quantity = product.quantity
                if buying_request.status == BuyingRequest.Status.DRAFT:
                    buying_request.status = BuyingRequest.Status.NORMALIZED
                buying_request.save()
                for offer in buying_request.offers.filter(superseded_by__isnull=True):
                    rebuild_scenarios_for_offer(offer)
                messages.success(request, 'Normalized product saved.')
                return redirect('administration:buying_request_detail', pk=pk)
            messages.error(request, 'Please correct the errors below.')
        elif action == 'recalculate':
            _require(request, 'buying.create')
            for offer in buying_request.offers.filter(superseded_by__isnull=True):
                rebuild_scenarios_for_offer(offer)
            messages.success(request, 'Scenarios recalculated.')
            return redirect('administration:buying_request_detail', pk=pk)
        elif action == 'cancel':
            _require(request, 'buying.create')
            buying_request.status = BuyingRequest.Status.CANCELLED
            buying_request.save(update_fields=['status', 'updated_at'])
            messages.success(request, 'Request cancelled.')
            return redirect('administration:buying_request_detail', pk=pk)
        elif action == 'archive':
            _require(request, 'buying.create')
            buying_request.is_archived = True
            buying_request.save(update_fields=['is_archived', 'updated_at'])
            messages.success(request, 'Request archived.')
            return redirect('administration:buying_requests')
        elif action == 'duplicate':
            _require(request, 'buying.create')
            clone = BuyingRequest.objects.create(
                original_query=buying_request.original_query,
                staff_user=request.user,
                client=buying_request.client,
                quantity=buying_request.quantity,
                notes=buying_request.notes,
            )
            messages.success(request, 'Request duplicated.')
            return redirect('administration:buying_request_detail', pk=clone.pk)
        elif action in ('run_search', 'force_search'):
            _require(request, 'buying.run_search')
            if not buying_request.normalized_product_id:
                messages.error(request, 'Normalize the product before running supplier search.')
                return redirect('administration:buying_request_detail', pk=pk)
            from buying.services.search import start_search
            result = start_search(buying_request, force=(action == 'force_search'))
            messages.success(
                request,
                f'Search started across {result["runs"]} suppliers '
                f'({"sync" if result["sync"] else "async"}).',
            )
            return redirect('administration:buying_request_detail', pk=pk)
        elif action == 'retry_failed':
            _require(request, 'buying.retry_connectors')
            from buying.services.search import retry_failed
            result = retry_failed(buying_request)
            messages.success(request, f'Retried {result["retried"]} failed connector(s).')
            return redirect('administration:buying_request_detail', pk=pk)
        else:
            form = None
    if request.method != 'POST' or form is None:
        form = (
            NormalizedProductForm(instance=buying_request.normalized_product)
            if buying_request.normalized_product_id
            else NormalizedProductForm()
        )

    scenarios = (
        buying_request.cost_scenarios.filter(status=CostScenario.Status.CALCULATED)
        .select_related('supplier_offer__supplier', 'fulfillment_route__provider')
        .order_by('rank', '-created_at')
    )
    blocked_scenarios = buying_request.cost_scenarios.filter(
        status=CostScenario.Status.CALCULATION_BLOCKED
    ).select_related('supplier_offer__supplier', 'fulfillment_route')
    failed_scenarios = buying_request.cost_scenarios.filter(
        status=CostScenario.Status.FAILED
    ).select_related('supplier_offer__supplier', 'fulfillment_route')
    from buying.services.search import progress_payload
    return render(
        request,
        'buying/staff/request_detail.html',
        {
            'staff_nav_active': 'buying_requests',
            'page_heading': f'Buying request #{buying_request.pk}',
            'buying_request': buying_request,
            'normalized_form': form,
            'offers': buying_request.offers.filter(superseded_by__isnull=True).select_related('supplier'),
            'scenarios': scenarios,
            'blocked_scenarios': blocked_scenarios,
            'failed_scenarios': failed_scenarios,
            'search_progress': progress_payload(buying_request),
            'uncertainties': (
                buying_request.normalized_product.uncertainties
                if buying_request.normalized_product_id
                else []
            ),
            'field_sources': (
                buying_request.normalized_product.field_sources
                if buying_request.normalized_product_id
                else {}
            ),
        },
    )


@buying_permission_required('buying.view')
def buying_request_progress(request, pk: int):
    from django.http import JsonResponse

    from buying.services.search import finalize_search, progress_payload

    buying_request = get_object_or_404(BuyingRequest, pk=pk)
    finalize_search(buying_request.pk)
    buying_request.refresh_from_db()
    return JsonResponse(progress_payload(buying_request))


# --------------------------------------------------------------------------- #
# Manual supplier offers
# --------------------------------------------------------------------------- #

@buying_permission_required('buying.create')
def buying_offer_edit(request, request_pk: int, pk: int | None = None):
    """Create an offer, or save an edit as a NEW offer version-snapshot.

    Offers are immutable once scenarios exist: editing never rewrites the old
    row — revise_offer() creates version N+1, supersedes the old scenarios and
    builds fresh ones (carrying manual overrides)."""
    buying_request = get_object_or_404(BuyingRequest, pk=request_pk)
    offer = get_object_or_404(
        SupplierOffer, pk=pk, buying_request=buying_request, superseded_by__isnull=True
    ) if pk else None
    form = SupplierOfferForm(request.POST or None, instance=offer)
    if request.method == 'POST' and form.is_valid():
        old_values = {}
        if offer:
            old_values = {name: form.initial.get(name) for name in form.changed_data}
        saved = form.save(commit=False)
        saved.buying_request = buying_request
        saved.is_manual = True
        saved.checked_at = timezone.now()
        if saved.original_price and saved.original_price > saved.current_price:
            saved.discount_amount = saved.original_price - saved.current_price
            saved.discount_percent = round(
                saved.discount_amount / saved.original_price * 100, 2
            )
        if pk:
            old_offer = SupplierOffer.objects.get(pk=pk)
            saved.created_by = old_offer.created_by
            saved = revise_offer(old_offer, saved)
            if form.changed_data:
                audit_offer_changes(
                    saved,
                    {
                        name: (old_values.get(name), form.cleaned_data.get(name))
                        for name in form.changed_data
                    },
                    request.user,
                    reason=request.POST.get('change_reason', '') or 'Staff edit',
                )
            scenarios = list(
                saved.cost_scenarios.exclude(status=CostScenario.Status.SUPERSEDED)
            )
        else:
            saved.created_by = request.user
            saved.save()
            scenarios = rebuild_scenarios_for_offer(saved)
        if scenarios:
            messages.success(
                request, f'Offer saved. {len(scenarios)} cost scenario(s) calculated.'
            )
        else:
            messages.warning(
                request,
                'Offer saved, but no fulfillment route matches the supplier country — '
                'add a route to get a cost scenario.',
            )
        return redirect('administration:buying_request_detail', pk=buying_request.pk)
    return render(
        request,
        'buying/staff/form.html',
        {
            'staff_nav_active': 'buying_requests',
            'page_heading': 'Edit offer' if pk else 'New supplier offer',
            'page_note_text': f'For buying request #{buying_request.pk}: {buying_request.original_query[:120]}',
            'form': form,
            'show_reason': bool(pk),
            'back_url_name': 'administration:buying_request_detail',
            'back_url_arg': buying_request.pk,
        },
    )


# --------------------------------------------------------------------------- #
# Cost scenario detail + manual overrides
# --------------------------------------------------------------------------- #

@buying_permission_required('buying.view')
def buying_scenario_detail(request, pk: int):
    scenario = get_object_or_404(
        CostScenario.objects.select_related(
            'buying_request__normalized_product',
            'supplier_offer__supplier',
            'fulfillment_route__provider',
            'fulfillment_route__warehouse',
        ),
        pk=pk,
    )
    override_form = OverrideForm(components=OVERRIDABLE_COMPONENTS)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'override':
            _require(request, 'buying.override_calculation')
            override_form = OverrideForm(request.POST, components=OVERRIDABLE_COMPONENTS)
            if override_form.is_valid():
                successor = override_scenario_component(
                    scenario,
                    override_form.cleaned_data['component'],
                    Decimal(override_form.cleaned_data['new_amount']),
                    request.user,
                    override_form.cleaned_data['reason'],
                )
                messages.success(
                    request,
                    'Override applied — this created a new scenario version; '
                    'the previous one is kept as superseded.',
                )
                return redirect('administration:buying_scenario_detail', pk=successor.pk)
            messages.error(request, 'Please correct the override form.')
        elif action == 'remove_override':
            _require(request, 'buying.override_calculation')
            component = request.POST.get('component', '')
            reason = request.POST.get('reason', '') or 'Override removed'
            if component in (scenario.calculation_details.get('overrides') or {}):
                successor = remove_scenario_override(scenario, component, request.user, reason)
                messages.success(
                    request,
                    'Override removed — automatic logic restored in a new scenario version.',
                )
                return redirect('administration:buying_scenario_detail', pk=successor.pk)
            messages.error(request, 'No active override for that component.')
        elif action == 'toggle_hide':
            _require(request, 'buying.override_calculation')
            scenario.is_hidden = not scenario.is_hidden
            scenario.save(update_fields=['is_hidden'])
            return redirect('administration:buying_scenario_detail', pk=pk)

    from django.contrib.contenttypes.models import ContentType

    # Audit history across the whole version chain of this scenario.
    chain_pks = _scenario_chain_pks(scenario)
    overrides_history = ManualOverride.objects.filter(
        content_type=ContentType.objects.get_for_model(CostScenario), object_id__in=chain_pks
    ).select_related('user')
    return render(
        request,
        'buying/staff/scenario_detail.html',
        {
            'staff_nav_active': 'buying_requests',
            'page_heading': f'Cost scenario #{scenario.pk}',
            'scenario': scenario,
            'offer': scenario.supplier_offer,
            'route': scenario.fulfillment_route,
            'components': scenario.breakdown,
            'active_overrides': scenario.calculation_details.get('overrides') or {},
            'override_form': override_form,
            'overrides_history': overrides_history,
        },
    )


def _scenario_chain_pks(scenario: CostScenario) -> list[int]:
    """Pks of the scenario and all its predecessor versions."""
    pks = [scenario.pk]
    frontier = [scenario.pk]
    while frontier:
        frontier = list(
            CostScenario.objects.filter(superseded_by_id__in=frontier).values_list('pk', flat=True)
        )
        pks.extend(frontier)
    return pks


# --------------------------------------------------------------------------- #
# Reference data: suppliers, routes, rules, optimization candidates
# --------------------------------------------------------------------------- #

@buying_permission_required('buying.view')
def buying_suppliers(request):
    return render(
        request,
        'buying/staff/suppliers_list.html',
        {
            'staff_nav_active': 'buying_suppliers',
            'page_heading': 'Suppliers',
            'suppliers': Supplier.objects.all(),
        },
    )


@buying_permission_required('buying.manage_suppliers')
def buying_supplier_edit(request, pk: int | None = None):
    supplier = get_object_or_404(Supplier, pk=pk) if pk else None
    return _form_page(
        request,
        SupplierForm(request.POST or None, instance=supplier),
        heading='Edit supplier' if pk else 'New supplier',
        nav='buying_suppliers',
        back='administration:buying_suppliers',
    )


@buying_permission_required('buying.view')
def buying_connectors(request):
    if request.method == 'POST':
        action = request.POST.get('action', '')
        supplier_id = request.POST.get('supplier_id')
        if action == 'health_all':
            _require(request, 'buying.manage_suppliers')
            from buying.services.search import run_health_check
            count = 0
            for supplier in Supplier.objects.filter(enabled=True):
                run_health_check(supplier)
                count += 1
            messages.success(request, f'Health-checked {count} supplier(s).')
            return redirect('administration:buying_connectors')
        if action == 'health_one' and supplier_id:
            _require(request, 'buying.manage_suppliers')
            from buying.services.search import run_health_check
            supplier = get_object_or_404(Supplier, pk=supplier_id)
            row = run_health_check(supplier)
            messages.success(request, f'{supplier.name}: {row.status}')
            return redirect('administration:buying_connectors')
        if action == 'test_auth' and supplier_id:
            _require(request, 'buying.manage_suppliers')
            supplier = get_object_or_404(Supplier, pk=supplier_id)
            from buying.connectors.registry import get_connector, get_connector_class
            code = supplier.connector_class or supplier.code
            if not get_connector_class(code):
                messages.error(request, f'No connector for {code}')
                return redirect('administration:buying_connectors')
            connector = get_connector(code)
            if not connector.capabilities.authenticated_pricing:
                messages.info(request, f'{supplier.name} does not use authenticated pricing.')
                return redirect('administration:buying_connectors')
            status = connector.authentication_status()
            if status.status in ('session_expired', 'credentials_missing'):
                result = connector.login()
                messages.info(
                    request,
                    f'{supplier.name} auth: {result.status}'
                    + (f' — {result.message}' if result.message else ''),
                )
            else:
                messages.info(
                    request,
                    f'{supplier.name} auth: {status.status}'
                    + (f' — {status.message}' if status.message else ''),
                )
            return redirect('administration:buying_connectors')

    suppliers = Supplier.objects.all()
    from buying.connectors.readiness import readiness_for
    for supplier in suppliers:
        supplier.latest_status = supplier.connector_statuses.first()
        supplier.readiness = readiness_for(supplier.code)
    return render(
        request,
        'buying/staff/connectors.html',
        {
            'staff_nav_active': 'buying_connectors',
            'page_heading': 'Connector status',
            'suppliers': suppliers,
            'auth_codes': ('itf-tennis-point', 'central-tennis'),
        },
    )


@buying_permission_required('buying.view')
def buying_ai_diagnostics(request):
    from buying.ai.diagnostics import build_diagnostics

    probe = False
    if request.method == 'POST':
        _require(request, 'buying.manage_suppliers')
        if request.POST.get('action') == 'probe':
            probe = True
            messages.info(request, 'API probe completed.')
    diagnostics = build_diagnostics(probe=probe or request.GET.get('probe') == '1')
    return render(
        request,
        'buying/staff/ai_diagnostics.html',
        {
            'staff_nav_active': 'buying_ai',
            'page_heading': 'AI diagnostics',
            'diagnostics': diagnostics,
        },
    )


@buying_permission_required('buying.view')
def buying_mappings(request):
    return render(
        request,
        'buying/staff/mappings_list.html',
        {
            'staff_nav_active': 'buying_mappings',
            'page_heading': 'Product mappings',
            'mappings': ProductMapping.objects.select_related('canonical_product', 'supplier')[:300],
        },
    )


@buying_permission_required('buying.view')
def buying_routes(request):
    return render(
        request,
        'buying/staff/routes.html',
        {
            'staff_nav_active': 'buying_routes',
            'page_heading': 'Fulfillment routes',
            'providers': FulfillmentProvider.objects.all(),
            'warehouses': FulfillmentWarehouse.objects.select_related('provider'),
            'routes': FulfillmentRoute.objects.select_related('provider', 'warehouse', 'supplier'),
        },
    )


@buying_permission_required('buying.manage_fulfillment_routes')
def buying_provider_edit(request, pk: int | None = None):
    provider = get_object_or_404(FulfillmentProvider, pk=pk) if pk else None
    return _form_page(
        request,
        FulfillmentProviderForm(request.POST or None, instance=provider),
        heading='Edit fulfillment provider' if pk else 'New fulfillment provider',
        nav='buying_routes',
        back='administration:buying_routes',
    )


@buying_permission_required('buying.manage_fulfillment_routes')
def buying_warehouse_edit(request, pk: int | None = None):
    warehouse = get_object_or_404(FulfillmentWarehouse, pk=pk) if pk else None
    return _form_page(
        request,
        FulfillmentWarehouseForm(request.POST or None, instance=warehouse),
        heading='Edit warehouse' if pk else 'New warehouse',
        nav='buying_routes',
        back='administration:buying_routes',
    )


@buying_permission_required('buying.manage_fulfillment_routes')
def buying_route_edit(request, pk: int | None = None):
    route = get_object_or_404(FulfillmentRoute, pk=pk) if pk else None
    return _form_page(
        request,
        FulfillmentRouteForm(request.POST or None, instance=route),
        heading='Edit route' if pk else 'New route',
        nav='buying_routes',
        back='administration:buying_routes',
    )


@buying_permission_required('buying.view')
def buying_pricing_rules(request):
    qs = CalculationRule.objects.select_related('supplier', 'provider', 'warehouse')
    rule_type = (request.GET.get('type') or '').strip()
    if rule_type:
        qs = qs.filter(rule_type=rule_type)
    return render(
        request,
        'buying/staff/rules_list.html',
        {
            'staff_nav_active': 'buying_rules',
            'page_heading': 'Pricing rules',
            'rules': qs,
            'rule_types': CalculationRule.RuleType.choices,
            'filter_type': rule_type,
        },
    )


@buying_permission_required('buying.view')
def buying_fx_rates(request):
    from buying.services.fx_rates import georgia_today, sync_nbg_rates
    from buying.integrations.nbg import NbgApiError

    today = georgia_today()
    if request.method == 'POST' and request.POST.get('action') == 'refresh':
        _require(request, 'buying.manage_pricing_rules')
        try:
            stats = sync_nbg_rates(force=True)
            if stats.get('skipped'):
                messages.info(request, f"Rates for {stats['rate_date']} already present.")
            else:
                messages.success(
                    request,
                    f"NBG rates refreshed for {stats['rate_date']}: "
                    f"+{stats['created']} new, {stats['updated']} updated.",
                )
        except NbgApiError as exc:
            messages.error(request, f'NBG refresh failed: {exc}')
        return redirect('administration:buying_fx_rates')

    currency = (request.GET.get('currency') or '').strip().upper()
    history = (request.GET.get('history') or '') == '1'
    qs = FxRate.objects.all()
    if currency:
        qs = qs.filter(currency=currency)
    if not history:
        # Latest row per currency (for the summary table).
        from django.db.models import Max
        latest_dates = (
            FxRate.objects.values('currency')
            .annotate(latest=Max('rate_date'))
        )
        from django.db.models import Q as DQ
        filter_q = DQ()
        for row in latest_dates:
            filter_q |= DQ(currency=row['currency'], rate_date=row['latest'])
        qs = qs.filter(filter_q) if latest_dates else qs.none()

    rates = list(qs.order_by('currency', '-rate_date')[:500])
    for rate in rates:
        rate.is_fresh = rate.rate_date == today
    return render(
        request,
        'buying/staff/fx_rates.html',
        {
            'staff_nav_active': 'buying_fx',
            'page_heading': 'FX rates (NBG)',
            'rates': rates,
            'today': today,
            'filter_currency': currency,
            'show_history': history,
            'currencies': sorted({r.currency for r in FxRate.objects.all()}) or ['USD', 'EUR', 'GBP', 'CNY'],
        },
    )


@buying_permission_required('buying.manage_pricing_rules')
def buying_rule_edit(request, pk: int | None = None):
    rule = get_object_or_404(CalculationRule, pk=pk) if pk else None
    form = CalculationRuleForm(request.POST or None, instance=rule)
    if request.method == 'POST' and form.is_valid():
        saved = form.save(commit=False)
        saved.updated_by = request.user
        saved.save()
        messages.success(request, 'Saved.')
        return redirect('administration:buying_pricing_rules')
    return render(
        request,
        'buying/staff/form.html',
        {
            'staff_nav_active': 'buying_rules',
            'page_heading': 'Edit rule' if pk else 'New pricing rule',
            'page_note_text': (
                'Params examples — FX rate: {"currency": "USD", "rate_gel": "2.70"}; '
                'margin (markups on break-even): {"minimum": "0.03", "standard": "0.10", "premium": "0.25"}; '
                'import VAT: {"rate": "0.18", "threshold_gel": "300", "threshold_base": "local_cost", "base": "local_cost_plus_intl_plus_fee"}; '
                'international delivery: {"currency": "GEL", "per_kg": "27"}; '
                'payment fee: {"rate": "0.02", "base": "international_shipping"}; '
                'sales VAT: {"rate": "0.18", "mode": "included_in_sale_price"}; '
                'small business tax: {"rate": "0.01", "mode": "percentage_of_gross_sale_price"}; '
                'weight: {"default_g": "800", "packaging_g": "150"}; '
                'volumetric weight: {"divisor": "6000"}'
            ),
            'form': form,
            'back_url_name': 'administration:buying_pricing_rules',
        },
    )


@buying_permission_required('buying.view')
def buying_optimization_rules(request):
    return render(
        request,
        'buying/staff/optimization_list.html',
        {
            'staff_nav_active': 'buying_optimization',
            'page_heading': 'Optimization rules',
            'candidates': OptimizationCandidate.objects.select_related('product', 'supplier'),
        },
    )


@buying_permission_required('buying.manage_optimization_rules')
def buying_candidate_edit(request, pk: int | None = None):
    candidate = get_object_or_404(OptimizationCandidate, pk=pk) if pk else None
    form = OptimizationCandidateForm(request.POST or None, instance=candidate)
    if request.method == 'POST' and form.is_valid():
        saved = form.save(commit=False)
        if not pk:
            saved.created_by = request.user
        saved.save()
        messages.success(request, 'Saved.')
        return redirect('administration:buying_optimization_rules')
    return render(
        request,
        'buying/staff/form.html',
        {
            'staff_nav_active': 'buying_optimization',
            'page_heading': 'Edit optimization candidate' if pk else 'New optimization candidate',
            'form': form,
            'back_url_name': 'administration:buying_optimization_rules',
        },
    )


def _form_page(request, form, *, heading: str, nav: str, back: str):
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Saved.')
        return redirect(back)
    return render(
        request,
        'buying/staff/form.html',
        {
            'staff_nav_active': nav,
            'page_heading': heading,
            'form': form,
            'back_url_name': back,
        },
    )


# --------------------------------------------------------------------------- #
# Buying calculator (manual batch landed-cost)
# --------------------------------------------------------------------------- #

@buying_permission_required('buying.view')
def buying_calculator_list(request):
    qs = (
        BuyingBatch.objects.select_related('supplier', 'created_by')
        .exclude(status=BuyingBatch.Status.ARCHIVED)
        .prefetch_related('lines')[:100]
    )
    return render(
        request,
        'buying/staff/calculator_list.html',
        {
            'staff_nav_active': 'buying_calculator',
            'page_heading': 'Buying calculator',
            'batches': qs,
        },
    )


@buying_permission_required('buying.create')
def buying_calculator(request, pk: int | None = None):
    """Create or edit a calculator batch, then recalculate routes."""
    from buying.services.batch_calculator import recalculate_batch

    batch = get_object_or_404(BuyingBatch, pk=pk) if pk else None
    if request.method == 'POST':
        form = BuyingBatchForm(request.POST, instance=batch)
        formset = BuyingBatchLineFormSet(request.POST, instance=batch)
        if form.is_valid() and formset.is_valid():
            batch = form.save(commit=False)
            if not batch.pk:
                batch.created_by = request.user
            batch.save()
            formset.instance = batch
            formset.save()
            for i, line in enumerate(batch.lines.order_by('id'), start=1):
                updates = []
                if line.sort_order != i:
                    line.sort_order = i
                    updates.append('sort_order')
                if not (line.currency or '').strip():
                    line.currency = batch.supplier.currency
                    updates.append('currency')
                if updates:
                    updates.append('updated_at')
                    line.save(update_fields=updates)
            quotes = recalculate_batch(batch)
            if not quotes:
                messages.warning(
                    request,
                    'Batch saved, but no fulfillment routes apply for this store country '
                    f'({batch.supplier.country}). Check Onex applicability and routes.',
                )
            else:
                messages.success(
                    request,
                    f'Calculated {len(quotes)} delivery scenario(s) for {batch.supplier.name}.',
                )
            return redirect('administration:buying_calculator_detail', pk=batch.pk)
    else:
        form = BuyingBatchForm(instance=batch)
        formset = BuyingBatchLineFormSet(instance=batch)

    return render(
        request,
        'buying/staff/calculator_form.html',
        {
            'staff_nav_active': 'buying_calculator',
            'page_heading': 'Edit calculator cart' if pk else 'Buying calculator',
            'page_note_text': (
                'Pick a store (country comes from the supplier record). Add one or more items '
                'from that store — a single line is enough. Category is required: shipping weight '
                'is estimated as product weights by category plus one box for the whole cart. '
                'Results show every applicable Onex route with batch and per-line landed cost.'
            ),            'form': form,
            'formset': formset,
            'batch': batch,
            'quick_supplier_form': QuickSupplierForm(),
            'suppliers_json': mark_safe(json.dumps([
                {
                    'id': s.pk,
                    'currency': s.currency,
                    'country': s.country,
                    'name': s.name,
                    'tax_display_mode': s.tax_display_mode,
                    'free_shipping_threshold': (
                        str(s.free_shipping_threshold)
                        if s.free_shipping_threshold is not None
                        else None
                    ),
                }
                for s in Supplier.objects.filter(enabled=True).order_by('name')
            ])),
        },
    )


@buying_permission_required('buying.manage_suppliers')
def buying_calculator_add_supplier(request):
    """Quick-add a manual store, then return to the calculator form."""
    if request.method != 'POST':
        return redirect('administration:buying_calculator')
    form = QuickSupplierForm(request.POST)
    if form.is_valid():
        supplier = form.save()
        messages.success(request, f'Store “{supplier.name}” added ({supplier.country}).')
        return redirect(f"{reverse('administration:buying_calculator')}?supplier={supplier.pk}")
    messages.error(request, 'Could not add store: ' + '; '.join(
        f'{k}: {", ".join(v)}' for k, v in form.errors.items()
    ))
    return redirect('administration:buying_calculator')


@buying_permission_required('buying.view')
def buying_calculator_detail(request, pk: int):
    batch = get_object_or_404(
        BuyingBatch.objects.select_related('supplier', 'created_by').prefetch_related('lines'),
        pk=pk,
    )
    quotes = list(
        BuyingBatchQuote.objects.filter(
            batch=batch,
            status__in=(
                BuyingBatchQuote.Status.CALCULATED,
                BuyingBatchQuote.Status.CALCULATION_BLOCKED,
            ),
            partition_key='all',
        )
        .select_related('fulfillment_route', 'fulfillment_route__provider', 'fulfillment_route__warehouse')
        .order_by('rank', 'id')
    )
    return render(
        request,
        'buying/staff/calculator_detail.html',
        {
            'staff_nav_active': 'buying_calculator',
            'page_heading': batch.display_title(),
            'page_note_text': (
                f'{batch.supplier.name} · {batch.supplier.country} · '
                f'{batch.lines.count()} item(s) · combined shipment'
            ),
            'batch': batch,
            'quotes': quotes,
        },
    )


@buying_permission_required('buying.create')
def buying_calculator_recalculate(request, pk: int):
    from buying.services.batch_calculator import recalculate_batch

    if request.method != 'POST':
        return redirect('administration:buying_calculator_detail', pk=pk)
    batch = get_object_or_404(BuyingBatch, pk=pk)
    quotes = recalculate_batch(batch)
    messages.success(request, f'Recalculated {len(quotes)} scenario(s).')
    return redirect('administration:buying_calculator_detail', pk=pk)
