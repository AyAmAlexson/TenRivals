"""Batch / combined-shipment pricing for the Buying Calculator.

Given one supplier + N manual lines + one FulfillmentRoute, compute a shared
parcel quote and allocate common costs to lines by value.

Reuses CalculationRule handlers, FX, and the same component order as
``build_cost_scenario`` — does not invent tariffs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any

from buying.engine.fx import FxRateUnavailable, get_fx_rate_to_gel
from buying.engine.pricing import (
    COEFFICIENT_PLACES,
    PricingError,
    _Breakdown,
    _dec,
    _q2,
    _rule_snapshot,
)
from buying.engine.rule_handlers import get_handler
from buying.engine.rules import resolve_rule
from buying.engine.weight import SOURCE_CONFIGURED_RULE, SOURCE_MANUAL
from buying.models import (
    BuyingBatchQuote,
    CalculationRule,
    FulfillmentRoute,
    Supplier,
)

RuleType = CalculationRule.RuleType

BATCH_CALCULATION_VERSION = 'batch-combined-2026.08'


@dataclass
class BatchLineInput:
    line_id: int | None
    title: str
    unit_price: Decimal
    currency: str
    quantity: int
    category: str = ''
    weight_g: int | None = None
    sort_order: int = 1


def resolve_line_chargeable_weight(
    supplier: Supplier,
    category: str,
    weight_g: int | None,
    quantity: int,
) -> tuple[int | None, dict]:
    """Shipping weight for one calculator line (manual g or category rule)."""
    scope = {
        'country': supplier.country,
        'supplier': supplier,
        'category': category or '',
    }
    weight_rule = resolve_rule(RuleType.WEIGHT, **scope)
    packaging_g = int(weight_rule.params.get('packaging_g', 0) or 0) if weight_rule else 0

    if weight_g:
        shipping_item_g = int(weight_g) + packaging_g
        source, exact = SOURCE_MANUAL, True
    elif weight_rule and weight_rule.params.get('default_g'):
        shipping_item_g = int(weight_rule.params['default_g'])
        source, exact = SOURCE_CONFIGURED_RULE, False
    else:
        shipping_item_g, source, exact = None, 'missing', False

    total = shipping_item_g * quantity if shipping_item_g is not None else None
    meta = {
        'shipping_item_g': shipping_item_g,
        'shipping_source': source,
        'shipping_exact': exact,
        'packaging_g': packaging_g,
        'chargeable_g': total,
        'quantity': quantity,
        'exact': exact,
        'category': category or '',
        'weight_rule': (
            {'id': weight_rule.pk, 'version': weight_rule.version, 'params': dict(weight_rule.params)}
            if weight_rule
            else None
        ),
    }
    return total, meta


def allocate_by_value(
    amounts: list[Decimal], total_to_split: Decimal
) -> list[Decimal]:
    """Split ``total_to_split`` across lines proportional to ``amounts`` (GEL)."""
    n = len(amounts)
    if n == 0:
        return []
    if total_to_split == 0:
        return [Decimal('0.00')] * n
    base = sum(amounts, Decimal('0'))
    if base <= 0:
        # Equal split when values are zero.
        share = _q2(total_to_split / n)
        out = [share] * (n - 1)
        out.append(_q2(total_to_split - share * (n - 1)))
        return out
    out: list[Decimal] = []
    allocated = Decimal('0')
    for i, amt in enumerate(amounts):
        if i == n - 1:
            out.append(_q2(total_to_split - allocated))
        else:
            part = _q2(total_to_split * (amt / base))
            out.append(part)
            allocated += part
    return out


def build_batch_route_quote(
    supplier: Supplier,
    lines: list[BatchLineInput],
    route: FulfillmentRoute,
    *,
    local_shipping_cost: Decimal | None = None,
    free_shipping_threshold: Decimal | None = None,
    tax_display_mode: str | None = None,
) -> BuyingBatchQuote:
    """Compute an unsaved BuyingBatchQuote for one combined shipment.

    Raises PricingError when FX cannot be resolved for a line currency.
    """
    if not lines:
        raise PricingError('Batch has no lines to price.')

    bd = _Breakdown(None)
    tax_mode = tax_display_mode or getattr(supplier, 'tax_display_mode', '') or ''
    threshold = free_shipping_threshold
    if threshold is None:
        threshold = getattr(supplier, 'free_shipping_threshold', None)

    # Primary scope uses first non-empty category; route filter is country-based.
    primary_category = next((ln.category for ln in lines if ln.category), '')
    scope = {
        'country': supplier.country,
        'supplier': supplier,
        'category': primary_category,
    }
    route_scope = {**scope, 'provider': route.provider, 'warehouse': route.warehouse}

    line_item_gel: list[Decimal] = []
    line_meta: list[dict] = []
    cart_supplier_currency = Decimal('0')
    fx_by_currency: dict[str, tuple[Decimal, dict]] = {}

    for ln in lines:
        currency = (ln.currency or supplier.currency or 'GEL').upper()
        if currency not in fx_by_currency:
            try:
                fx_by_currency[currency] = get_fx_rate_to_gel(currency)
            except FxRateUnavailable as exc:
                raise PricingError(str(exc)) from exc
            if fx_by_currency[currency][1].get('stale'):
                bd.warn('stale_fx_rate')
                bd.warn(
                    f"stale_fx_rate:{currency}:{fx_by_currency[currency][1].get('rate_date') or 'unknown'}"
                )
        fx_rate, fx_meta = fx_by_currency[currency]
        original = _dec(ln.unit_price) * int(ln.quantity or 1)
        gel = _q2(original * fx_rate)
        line_item_gel.append(gel)
        if currency == (supplier.currency or '').upper():
            cart_supplier_currency += original
        else:
            # Convert to supplier currency for free-shipping threshold basis.
            try:
                sup_rate, _ = get_fx_rate_to_gel(supplier.currency)
                # gel / sup_rate ≈ amount in supplier currency
                cart_supplier_currency += _q2(gel / sup_rate) if sup_rate else original
            except FxRateUnavailable:
                cart_supplier_currency += original
        w_total, w_meta = resolve_line_chargeable_weight(
            supplier, ln.category or '', ln.weight_g, int(ln.quantity or 1)
        )
        line_meta.append(
            {
                'line_id': ln.line_id,
                'title': ln.title,
                'sort_order': ln.sort_order,
                'quantity': int(ln.quantity or 1),
                'category': ln.category or '',
                'currency': currency,
                'unit_price': str(_dec(ln.unit_price)),
                'item_original': str(_q2(original)),
                'item_cost_gel': str(gel),
                'fx': {**fx_meta, 'rate_gel': str(fx_rate)},
                'weight': w_meta,
                'weight_g_total': w_total,
            }
        )

    item_cost = bd.add(
        'item_cost',
        'Product prices (batch)',
        sum(line_item_gel, Decimal('0')),
        source='manual_entry',
        exact=True,
        note=f'{len(lines)} line(s)',
    )

    # --- Local shipping (once for the cart) ---------------------------------
    free_ship_meta = {
        'threshold': str(threshold) if threshold is not None else None,
        'threshold_currency': supplier.currency,
        'cart_subtotal': str(_q2(cart_supplier_currency)),
        'reached': False,
    }
    local_shipping_status = 'unknown'
    if threshold is not None and cart_supplier_currency >= _dec(threshold):
        local_shipping = bd.add(
            'local_shipping',
            'Local shipping',
            Decimal('0'),
            source='parsed',
            exact=True,
            note=f'Free shipping over {threshold} {supplier.currency}',
        )
        local_shipping_status = 'confirmed_zero'
        free_ship_meta['reached'] = True
    elif local_shipping_cost is not None:
        try:
            rate, _ = get_fx_rate_to_gel(supplier.currency)
        except FxRateUnavailable as exc:
            raise PricingError(str(exc)) from exc
        amount = _dec(local_shipping_cost)
        local_shipping = bd.add(
            'local_shipping',
            'Local shipping',
            _q2(amount * rate),
            source='manual_entry',
            exact=True,
            amount_original=amount,
            currency=supplier.currency,
        )
        local_shipping_status = 'confirmed_amount'
    else:
        rule = bd.resolve(RuleType.LOCAL_SHIPPING, scope)
        if rule:
            handler = get_handler(RuleType.LOCAL_SHIPPING)
            amount = handler.amount_in_currency(
                rule.params, items_in_currency=cart_supplier_currency
            )
            currency = str(rule.params.get('currency') or supplier.currency)
            try:
                rate, _ = get_fx_rate_to_gel(currency)
            except FxRateUnavailable as exc:
                raise PricingError(str(exc)) from exc
            local_shipping = bd.add(
                'local_shipping',
                'Local shipping',
                _q2(amount * rate),
                source='configured_rule',
                exact=False,
                rule=rule,
                amount_original=amount,
                currency=currency,
                note='estimated_amount — configured supplier rule',
            )
            local_shipping_status = 'estimated_amount'
            bd.warn('estimated:local_shipping')
        else:
            local_shipping = bd.add(
                'local_shipping',
                'Local shipping',
                Decimal('0'),
                source='unknown',
                exact=False,
                note='unknown — not confirmed',
            )
            local_shipping_status = 'unknown'
            bd.warn('missing_rule:local_shipping')
            bd.warn('unknown:local_shipping')

    # --- Local tax ----------------------------------------------------------
    if tax_mode in ('prices_include_vat', 'no_local_tax'):
        note = (
            'VAT already included in product prices'
            if tax_mode == 'prices_include_vat'
            else 'Supplier charges no local tax'
        )
        local_tax = bd.add(
            'local_tax', 'Local tax', Decimal('0'), source='parsed', exact=True, note=note
        )
    else:
        rule = bd.resolve(RuleType.LOCAL_TAX, scope)
        if rule:
            handler = get_handler(RuleType.LOCAL_TAX)
            amount = handler.amount(
                rule.params, items_gel=item_cost, local_shipping_gel=local_shipping
            )
            is_none_mode = rule.params.get('mode', 'rate') == 'none'
            local_tax = bd.add(
                'local_tax',
                'Local tax',
                amount,
                source='configured_rule',
                exact=is_none_mode,
                rule=rule,
            )
            if not is_none_mode:
                bd.warn('estimated:local_tax')
        else:
            local_tax = bd.missing_rule(RuleType.LOCAL_TAX, 'local_tax', 'Local tax')

    local_cost = _q2(item_cost + local_shipping)

    # --- Weight -------------------------------------------------------------
    weight_exact = True
    total_weight_g = 0
    any_weight = False
    for meta in line_meta:
        w = meta.get('weight_g_total')
        if w is None:
            weight_exact = False
            continue
        any_weight = True
        total_weight_g += int(w)
        if not (meta.get('weight') or {}).get('exact', False):
            weight_exact = False
    if not any_weight:
        total_weight_g = None
        bd.block('missing_weight')
        weight_exact = False

    # --- International shipping ---------------------------------------------
    intl_rule = bd.resolve(RuleType.INTERNATIONAL_SHIPPING, route_scope)
    if intl_rule and total_weight_g is not None:
        handler = get_handler(RuleType.INTERNATIONAL_SHIPPING)
        currency = handler.currency(intl_rule.params)
        try:
            rate, _ = get_fx_rate_to_gel(currency)
        except FxRateUnavailable as exc:
            raise PricingError(str(exc)) from exc
        amount, kg = handler.amount_in_currency(intl_rule.params, weight_g=total_weight_g)
        intl_shipping = bd.add(
            'international_shipping',
            f'{route.provider.name} international delivery',
            _q2(amount * rate),
            source='configured_rule',
            exact=weight_exact,
            rule=intl_rule,
            amount_original=amount,
            currency=currency,
            note=f'{kg} kg chargeable batch weight',
        )
        if not weight_exact:
            bd.warn('estimated:weight')
    elif intl_rule:
        intl_shipping = bd.add(
            'international_shipping',
            f'{route.provider.name} international delivery',
            Decimal('0'),
            source='configured_rule',
            exact=False,
            rule=intl_rule,
            note='Weight unknown — delivery not computed',
        )
    else:
        intl_shipping = bd.missing_rule(
            RuleType.INTERNATIONAL_SHIPPING, 'international_shipping', 'International delivery'
        )

    fee_rule = bd.resolve(RuleType.PAYMENT_FEE, route_scope)
    if fee_rule:
        handler = get_handler(RuleType.PAYMENT_FEE)
        payment_fee = bd.add(
            'payment_fee',
            f'{route.provider.name} payment fee',
            handler.amount(
                fee_rule.params,
                intl_shipping_gel=intl_shipping,
                items_plus_local_gel=item_cost + local_shipping + local_tax,
            ),
            source='configured_rule',
            exact=weight_exact,
            rule=fee_rule,
            note=f'base: {handler.base_kind(fee_rule.params)}',
        )
    else:
        payment_fee = bd.missing_rule(RuleType.PAYMENT_FEE, 'payment_fee', 'Payment fee')

    vat_rule = bd.resolve(RuleType.GEORGIA_VAT, route_scope)
    declaration_required = False
    threshold_gel = None
    if vat_rule:
        vat_handler = get_handler(RuleType.GEORGIA_VAT)
        declaration_required, threshold_gel, compared = vat_handler.declaration_required(
            vat_rule.params, items_gel=item_cost, local_shipping_gel=local_shipping
        )
        if declaration_required:
            georgia_vat = bd.add(
                'georgia_vat',
                'Import VAT',
                vat_handler.amount(
                    vat_rule.params,
                    items_gel=item_cost,
                    local_shipping_gel=local_shipping,
                    intl_shipping_gel=intl_shipping,
                    payment_fee_gel=payment_fee,
                ),
                source='configured_rule',
                exact=weight_exact,
                rule=vat_rule,
                note=f'Local cost {compared} GEL ≥ {threshold_gel} GEL threshold',
            )
        else:
            georgia_vat = bd.add(
                'georgia_vat',
                'Import VAT',
                Decimal('0'),
                source='configured_rule',
                exact=True,
                rule=vat_rule,
                note=f'Local cost {compared} GEL below the {threshold_gel} GEL threshold',
            )
    else:
        georgia_vat = bd.missing_rule(RuleType.GEORGIA_VAT, 'georgia_vat', 'Import VAT')

    customs_rule = bd.resolve(RuleType.CUSTOMS, route_scope)
    if customs_rule:
        handler = get_handler(RuleType.CUSTOMS)
        if declaration_required or handler.applies_always(customs_rule.params):
            customs = bd.add(
                'customs',
                'Customs declaration fee',
                handler.amount(customs_rule.params, items_gel=item_cost),
                source='configured_rule',
                exact=True,
                rule=customs_rule,
            )
        else:
            customs = bd.add(
                'customs',
                'Customs declaration fee',
                Decimal('0'),
                source='configured_rule',
                exact=True,
                rule=customs_rule,
                note='No import declaration required',
            )
    else:
        customs = bd.missing_rule(RuleType.CUSTOMS, 'customs', 'Customs declaration fee')

    declaration_rule = bd.resolve(RuleType.DECLARATION_SERVICE, route_scope)
    if declaration_required and declaration_rule:
        declaration_service = bd.add(
            'declaration_service',
            f'{route.provider.name} declaration service',
            get_handler(RuleType.DECLARATION_SERVICE).amount(declaration_rule.params),
            source='configured_rule',
            exact=True,
            rule=declaration_rule,
        )
    elif declaration_required:
        declaration_service = bd.missing_rule(
            RuleType.DECLARATION_SERVICE, 'declaration_service', 'Declaration service'
        )
    else:
        declaration_service = bd.add(
            'declaration_service',
            'Declaration service',
            Decimal('0'),
            source='configured_rule',
            exact=True,
            rule=declaration_rule,
            note='No import declaration required',
        )

    foreign_base = item_cost + local_shipping + local_tax + payment_fee + intl_shipping

    rule = bd.resolve(RuleType.INSURANCE, route_scope)
    insurance = bd.add(
        'insurance',
        'Insurance',
        get_handler(RuleType.INSURANCE).amount(rule.params, base_gel=item_cost) if rule else Decimal('0'),
        source='configured_rule',
        exact=bool(rule),
        rule=rule,
        note='' if rule else 'No insurance rule — not applicable',
    )

    rule = bd.resolve(RuleType.FX_BUFFER, scope)
    fx_buffer = bd.add(
        'fx_buffer',
        'FX buffer',
        get_handler(RuleType.FX_BUFFER).amount(rule.params, base_gel=foreign_base) if rule else Decimal('0'),
        source='configured_rule',
        exact=bool(rule),
        rule=rule,
        note='' if rule else 'No FX buffer rule — not applicable',
    )

    rule = bd.resolve(RuleType.RISK_RESERVE, scope)
    risk_reserve = bd.add(
        'risk_reserve',
        'Risk reserve',
        get_handler(RuleType.RISK_RESERVE).amount(rule.params, base_gel=foreign_base)
        if rule
        else Decimal('0'),
        source='configured_rule',
        exact=bool(rule),
        rule=rule,
        note='' if rule else 'No risk reserve rule — not applicable',
    )

    rule = bd.resolve(RuleType.HANDLING, scope)
    handling = bd.add(
        'handling_cost',
        'Handling',
        get_handler(RuleType.HANDLING).amount(rule.params) if rule else Decimal('0'),
        source='configured_rule',
        exact=bool(rule),
        rule=rule,
        note='' if rule else 'No handling rule — not applicable',
    )

    shared_cost_total = _q2(
        local_shipping
        + local_tax
        + intl_shipping
        + payment_fee
        + georgia_vat
        + customs
        + declaration_service
        + insurance
        + fx_buffer
        + risk_reserve
        + handling
    )
    landed_cost = _q2(item_cost + shared_cost_total)

    # Allocation method (default by_value).
    alloc_rule = bd.resolve(RuleType.ALLOCATION, scope)
    alloc_method = 'by_value'
    if alloc_rule and alloc_rule.params.get('method') in (
        'by_value',
        'by_weight',
        'by_volumetric_weight',
        'hybrid',
        'manual',
    ):
        alloc_method = alloc_rule.params['method']
    if alloc_method != 'by_value':
        bd.warn(f'allocation_method_fallback:by_value (configured={alloc_method})')
        alloc_method = 'by_value'

    allocated_shared = allocate_by_value(line_item_gel, shared_cost_total)

    # Selling side on the batch total.
    sales_vat_rule = bd.resolve(RuleType.SALES_VAT, scope)
    sbt_rule = bd.resolve(RuleType.SMALL_BUSINESS_TAX, scope)
    if not sales_vat_rule:
        bd.block(f'missing_rule:{RuleType.SALES_VAT}')
    if not sbt_rule:
        bd.block(f'missing_rule:{RuleType.SMALL_BUSINESS_TAX}')

    net_coefficient = None
    if sales_vat_rule and sbt_rule:
        vat_share = get_handler(RuleType.SALES_VAT).coefficient_share(sales_vat_rule.params)
        sbt_share = get_handler(RuleType.SMALL_BUSINESS_TAX).coefficient_share(sbt_rule.params)
        net_coefficient = (Decimal('1') - vat_share - sbt_share).quantize(COEFFICIENT_PLACES)
        if net_coefficient <= 0:
            bd.block('invalid_net_sales_coefficient')
            net_coefficient = None

    margin_rule = bd.resolve(RuleType.MARGIN, scope)
    if not margin_rule:
        bd.block(f'missing_rule:{RuleType.MARGIN}')

    rounding_rule = bd.resolve(RuleType.ROUNDING, scope)

    def _round_price(price: Decimal) -> Decimal:
        if rounding_rule:
            step = get_handler(RuleType.ROUNDING).step_gel(rounding_rule.params)
            if step > 0:
                price = (price / step).quantize(Decimal('1'), rounding=ROUND_CEILING) * step
        return _q2(price)

    break_even = Decimal('0')
    prices: dict[str, Decimal] = {}
    markups: dict[str, Decimal] = {}
    if net_coefficient:
        break_even = _q2(landed_cost / net_coefficient)
    if net_coefficient and margin_rule:
        markups = get_handler(RuleType.MARGIN).markups(margin_rule.params)
        prices = {
            level: _round_price(break_even * (Decimal('1') + markup))
            for level, markup in markups.items()
        }

    customer_price = prices.get('standard', landed_cost)
    margin_amount = _q2(customer_price - landed_cost)

    # Per-line results (allocate selling prices by value too).
    allocated_customer = allocate_by_value(line_item_gel, customer_price)
    allocated_be = allocate_by_value(line_item_gel, break_even)
    allocated_min = allocate_by_value(line_item_gel, prices.get('minimum', Decimal('0')))
    allocated_std = allocate_by_value(line_item_gel, prices.get('standard', Decimal('0')))
    allocated_prem = allocate_by_value(line_item_gel, prices.get('premium', Decimal('0')))

    line_results: list[dict[str, Any]] = []
    for i, meta in enumerate(line_meta):
        line_landed = _q2(line_item_gel[i] + allocated_shared[i])
        line_results.append(
            {
                **meta,
                'allocated_shared_gel': str(allocated_shared[i]),
                'landed_cost_gel': str(line_landed),
                'break_even_price_gel': str(allocated_be[i]),
                'price_minimum_gel': str(allocated_min[i]),
                'price_standard_gel': str(allocated_std[i]),
                'price_premium_gel': str(allocated_prem[i]),
                'customer_price_gel': str(allocated_customer[i]),
            }
        )

    status = (
        BuyingBatchQuote.Status.CALCULATION_BLOCKED
        if bd.blocking_issues
        else BuyingBatchQuote.Status.CALCULATED
    )

    estimated_total = sum(
        Decimal(component['amount_gel'])
        for component in bd.components
        if not component['exact'] and component['code'] != 'margin'
    )
    has_missing_rules = any(w.startswith(('missing_rule:', 'blocking:')) for w in bd.warnings)
    if estimated_total == 0 and not bd.warnings:
        confidence = BuyingBatchQuote.Confidence.EXACT
    elif not has_missing_rules and landed_cost > 0 and estimated_total / landed_cost <= Decimal('0.15'):
        confidence = BuyingBatchQuote.Confidence.MOSTLY_EXACT
    else:
        confidence = BuyingBatchQuote.Confidence.ESTIMATED

    return BuyingBatchQuote(
        fulfillment_route=route,
        scenario_kind=BuyingBatchQuote.ScenarioKind.COMBINED_SHIPMENT,
        partition_key='all',
        status=status,
        item_cost=item_cost,
        local_shipping=local_shipping,
        local_tax=local_tax,
        payment_fee=payment_fee,
        international_shipping=intl_shipping,
        insurance=insurance,
        customs=customs,
        declaration_service=declaration_service,
        georgia_vat=georgia_vat,
        fx_buffer=fx_buffer,
        risk_reserve=risk_reserve,
        handling_cost=handling,
        shared_cost_total=shared_cost_total,
        landed_cost=landed_cost,
        break_even_price=break_even,
        price_minimum=prices.get('minimum', Decimal('0')),
        price_standard=prices.get('standard', Decimal('0')),
        price_premium=prices.get('premium', Decimal('0')),
        customer_price=customer_price,
        margin_amount=margin_amount,
        chargeable_weight_g=total_weight_g,
        estimated_min_days=route.estimated_min_days,
        estimated_max_days=route.estimated_max_days,
        calculation_version=BATCH_CALCULATION_VERSION,
        calculation_details={
            'components': bd.components,
            'allocation': {
                'method': alloc_method,
                'rule': _rule_snapshot(alloc_rule),
            },
            'local_shipping_status': local_shipping_status,
            'free_shipping': free_ship_meta,
            'import': {
                'local_cost_gel': str(local_cost),
                'threshold_gel': str(threshold_gel) if threshold_gel is not None else None,
                'declaration_required': declaration_required,
                'rule': _rule_snapshot(vat_rule),
            },
            'pricing': {
                'sales_vat': _rule_snapshot(sales_vat_rule),
                'small_business_tax': _rule_snapshot(sbt_rule),
                'net_sales_coefficient': str(net_coefficient) if net_coefficient else None,
                'break_even_price': str(break_even),
                'markups': {level: str(markup) for level, markup in markups.items()},
                'prices': {level: str(price) for level, price in prices.items()},
                'rounding': _rule_snapshot(rounding_rule),
            },
            'line_count': len(lines),
            'blocking_issues': bd.blocking_issues,
            'partition_key': 'all',
            # Contract stub for later optimizer:
            # evaluate_partitions(batch, partitions) → list of quotes.
            'optimizer_hook': 'evaluate_partitions',
        },
        line_results=line_results,
        confidence=confidence,
        warnings=bd.warnings,
    )


def evaluate_partitions(
    supplier: Supplier,
    lines: list[BatchLineInput],
    route: FulfillmentRoute,
    partitions: list[list[int]],
    **kwargs,
) -> list[BuyingBatchQuote]:
    """Future API: price each partition as its own combined shipment.

    ``partitions`` is a list of groups of line indexes into ``lines``.
    MVP callers should pass ``[[0, 1, …, n-1]]`` (everything together).
    """
    quotes: list[BuyingBatchQuote] = []
    for group in partitions:
        subset = [lines[i] for i in group if 0 <= i < len(lines)]
        if not subset:
            continue
        key = '+'.join(str(lines[i].line_id or i) for i in group)
        quote = build_batch_route_quote(supplier, subset, route, **kwargs)
        quote.partition_key = key or 'all'
        if key != 'all' and len(partitions) > 1:
            quote.scenario_kind = BuyingBatchQuote.ScenarioKind.PARTITION
        quotes.append(quote)
    return quotes
