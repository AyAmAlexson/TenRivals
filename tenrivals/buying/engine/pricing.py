"""Pricing Engine: SupplierOffer + FulfillmentRoute + CalculationRules = CostScenario.

Deterministic, versioned, LLM-free. Every component records its amount, source,
applied rule (id + version + a params snapshot) and exact/estimated flag in
calculation_details, so staff can audit any number on the scenario detail page
even after rules change.

Phase 2 (Onex MVP) calculation order — every value comes from configured rules,
nothing is hardcoded:

1.  item cost (FX-converted to GEL) and local shipping;
2.  LOCAL COST = product price + local shipping — compared against the import
    threshold from the georgia_vat rule (international shipping never enters
    the threshold);
3.  chargeable weight = max(actual, volumetric) → international delivery;
4.  payment fee (per its rule base — for Onex, % of international delivery);
5.  if the declaration is required: import VAT (base per rule), customs
    declaration fee, forwarder declaration service;
6.  landed cost = sum of the above (+ optional configured add-ons);
7.  selling side: net sales coefficient = 1 − vat/(1+vat) − small business tax
    (from active tax rules); break-even = landed / coefficient; three selling
    prices = break-even × (1 + markup) from the margin rule.

Missing inputs fall into two classes:
- BLOCKING (route, FX rate, international delivery tariff, chargeable weight,
  Georgian tax logic incl. sales taxes, margin rule): the scenario is stored
  with partial numbers but status=calculation_blocked and never enters ranking.
- Non-blocking (local shipping, local tax, payment fee): an estimate with an
  explicit missing_rule warning; confidence degrades to 'estimated'.
Operational add-ons (handling, insurance, risk reserve, FX buffer) without a
rule are treated as intentionally not applicable and noted as such.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from buying.engine.fx import FxRateUnavailable, get_fx_rate_to_gel
from buying.engine.rule_handlers import get_handler
from buying.engine.rules import resolve_rule_checked
from buying.engine.weight import resolve_chargeable_weight
from buying.models import CalculationRule, CostScenario, FulfillmentRoute, SupplierOffer

RuleType = CalculationRule.RuleType

CALCULATION_VERSION = 'phase2-shipping-weight-2026.08'

TWO_PLACES = Decimal('0.01')
COEFFICIENT_PLACES = Decimal('0.000000001')

# Missing rules of these types block a normal calculation (see module docstring).
BLOCKING_RULE_TYPES = frozenset({
    RuleType.INTERNATIONAL_SHIPPING,
    RuleType.GEORGIA_VAT,
    RuleType.CUSTOMS,
    RuleType.DECLARATION_SERVICE,
    RuleType.SALES_VAT,
    RuleType.SMALL_BUSINESS_TAX,
    RuleType.MARGIN,
})


class PricingError(Exception):
    """Scenario cannot be computed at all (e.g. no FX rate for the currency).
    Services persist this as a calculation_blocked scenario."""


def _q2(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _dec(value, default: str = '0') -> Decimal:
    if value in (None, ''):
        return Decimal(default)
    return Decimal(str(value))


def _rule_snapshot(rule: CalculationRule | None) -> dict | None:
    if not rule:
        return None
    return {'id': rule.pk, 'version': rule.version, 'name': rule.name, 'params': dict(rule.params)}


class _Breakdown:
    """Collects per-component provenance while the scenario is being computed."""

    def __init__(self, overrides: dict[str, Decimal] | None):
        self.components: list[dict] = []
        self.warnings: list[str] = []
        self.blocking_issues: list[str] = []
        self.overrides = overrides or {}

    def add(
        self,
        code: str,
        label: str,
        amount_gel: Decimal,
        *,
        source: str,
        exact: bool,
        rule: CalculationRule | None = None,
        amount_original: Decimal | None = None,
        currency: str = 'GEL',
        note: str = '',
    ) -> Decimal:
        auto_amount = None
        if code in self.overrides:
            # Keep the original automatic value next to the manual one.
            auto_amount = _q2(amount_gel)
            amount_gel = _q2(self.overrides[code])
            source, exact = 'manual_override', True
            note = f'Manually overridden (auto: {auto_amount} GEL)'
            rule, amount_original, currency = None, None, 'GEL'
        amount_gel = _q2(amount_gel)
        self.components.append(
            {
                'code': code,
                'label': label,
                'amount_gel': str(amount_gel),
                'auto_amount_gel': str(auto_amount) if auto_amount is not None else None,
                'amount_original': str(_q2(amount_original)) if amount_original is not None else None,
                'currency': currency,
                'source': source,
                'exact': exact,
                # Snapshot the rule params: calculation_details must stay
                # meaningful even after the rule itself changes.
                'rule': _rule_snapshot(rule),
                'note': note,
            }
        )
        return amount_gel

    def warn(self, message: str):
        if message not in self.warnings:
            self.warnings.append(message)

    def block(self, issue: str):
        if issue not in self.blocking_issues:
            self.blocking_issues.append(issue)
        self.warn(f'blocking:{issue}')

    def missing_rule(self, rule_type: str, code: str, label: str) -> Decimal:
        blocking = rule_type in BLOCKING_RULE_TYPES
        if blocking:
            self.block(f'missing_rule:{rule_type}')
            note = f'No "{rule_type}" rule configured — calculation blocked'
            return self.add(
                code, label, Decimal('0'), source='configured_rule', exact=False, note=note
            )
        # Non-blocking: do not pretend configured_rule zero is exact — mark unknown
        self.warn(f'missing_rule:{rule_type}')
        self.warn(f'unknown:{rule_type}')
        note = (
            f'No usable "{rule_type}" value — treated as 0 for maths only; '
            'confidence reduced, review manually'
        )
        return self.add(code, label, Decimal('0'), source='unknown', exact=False, note=note)

    def resolve(self, rule_type: str, scope: dict) -> CalculationRule | None:
        rule, conflict = resolve_rule_checked(rule_type, **scope)
        if conflict:
            self.warn(f'rule_conflict:{rule_type}')
        return rule


def build_cost_scenario(
    offer: SupplierOffer,
    route: FulfillmentRoute,
    *,
    quantity: int = 1,
    scenario_type: str = CostScenario.ScenarioType.CURRENT_SINGLE_ITEM,
    overrides: dict[str, Decimal] | None = None,
) -> CostScenario:
    """Compute an unsaved CostScenario snapshot.

    Raises PricingError when nothing at all can be computed (missing FX rate for
    the offer currency). Other missing blocking inputs produce a scenario with
    status=calculation_blocked; non-blocking gaps degrade to warnings."""
    request = offer.buying_request
    category = (
        request.normalized_product.category
        if request.normalized_product_id and request.normalized_product.category
        else ''
    )
    bd = _Breakdown(overrides)
    scope = {
        'country': offer.supplier.country,
        'supplier': offer.supplier,
        'category': category,
    }
    route_scope = {**scope, 'provider': route.provider, 'warehouse': route.warehouse}

    try:
        manual_fx = None
        if overrides and 'fx_unit_rate_gel' in overrides:
            manual_fx = overrides['fx_unit_rate_gel']
        fx_rate, fx_meta = get_fx_rate_to_gel(offer.currency, manual_unit_rate=manual_fx)
    except FxRateUnavailable as exc:
        raise PricingError(str(exc)) from exc
    if fx_meta.get('stale'):
        bd.warn('stale_fx_rate')
        bd.warn(
            f"stale_fx_rate:{fx_meta.get('currency')}:{fx_meta.get('rate_date') or 'unknown'}"
        )

    def to_gel(amount: Decimal, rate: Decimal = fx_rate) -> Decimal:
        return _q2(amount * rate)

    # --- Item cost -----------------------------------------------------------
    item_original = _dec(offer.current_price) * quantity
    item_cost = bd.add(
        'item_cost', 'Product price', to_gel(item_original),
        source='manual_entry' if offer.is_manual else 'parsed',
        exact=True,
        amount_original=item_original, currency=offer.currency,
        note=f'{quantity} × {offer.current_price} {offer.currency}' if quantity > 1 else '',
    )

    # --- Local shipping to the forwarder warehouse ---------------------------
    threshold = offer.free_shipping_threshold
    local_shipping_status = 'unknown'
    free_ship_meta = {
        'threshold': str(threshold) if threshold is not None else None,
        'threshold_currency': offer.free_shipping_currency or offer.currency,
        'threshold_basis': getattr(offer, 'threshold_basis', None) or 'unknown',
        'free_shipping_status': getattr(offer, 'free_shipping_status', None) or 'threshold_unknown',
        'cart_subtotal': str(item_original),
        'amount_missing': None,
        'source': offer.local_shipping_source or '',
        'reached': False,
    }
    if threshold is not None:
        missing = _dec(threshold) - item_original
        free_ship_meta['amount_missing'] = str(_q2(missing)) if missing > 0 else '0.00'
        free_ship_meta['reached'] = missing <= 0
    if threshold is not None and item_original >= _dec(threshold):
        local_shipping = bd.add(
            'local_shipping', 'Local shipping', Decimal('0'),
            source=offer.local_shipping_source or 'parsed',
            exact=True,
            note=f'Free shipping over {threshold} {offer.currency} (confirmed_zero)',
        )
        local_shipping_status = 'confirmed_zero'
        free_ship_meta['source'] = offer.local_shipping_source or 'parsed_policy'
        free_ship_meta['reached'] = True
        bd.warn('local_shipping:confirmed_zero')
    elif offer.local_shipping_cost is not None:
        exact = offer.local_shipping_source in (
            'parsed', 'checkout_simulation', 'manual_entry', 'manual_override'
        )
        amount = _dec(offer.local_shipping_cost)
        if amount == 0 and exact:
            local_shipping_status = 'confirmed_zero'
        elif exact:
            local_shipping_status = 'confirmed_amount'
        else:
            local_shipping_status = 'estimated_amount'
        local_shipping = bd.add(
            'local_shipping', 'Local shipping', to_gel(amount),
            source=offer.local_shipping_source, exact=exact,
            amount_original=amount, currency=offer.currency,
            note=f'status={local_shipping_status}',
        )
    else:
        rule = bd.resolve(RuleType.LOCAL_SHIPPING, scope)
        if rule:
            handler = get_handler(RuleType.LOCAL_SHIPPING)
            amount = handler.amount_in_currency(rule.params, items_in_currency=item_original)
            currency = str(rule.params.get('currency') or offer.currency)
            try:
                rate, _ = get_fx_rate_to_gel(currency)
            except FxRateUnavailable as exc:
                raise PricingError(str(exc)) from exc
            local_shipping = bd.add(
                'local_shipping', 'Local shipping', to_gel(amount, rate),
                source='configured_rule', exact=False, rule=rule,
                amount_original=amount, currency=currency,
                note='estimated_amount — configured supplier rule',
            )
            local_shipping_status = 'estimated_amount'
            free_ship_meta['source'] = 'configured_supplier_rule'
            bd.warn('estimated:local_shipping')
        else:
            # Unknown: do NOT pretend confirmed free shipping. Keep 0 for maths
            # of optional components only, with explicit unknown provenance.
            local_shipping = bd.add(
                'local_shipping', 'Local shipping', Decimal('0'),
                source='unknown', exact=False,
                note=(
                    'unknown — not confirmed; customer price may increase when '
                    'local shipping is determined'
                ),
            )
            local_shipping_status = 'unknown'
            bd.warn('missing_rule:local_shipping')
            bd.warn('unknown:local_shipping')
            bd.warn('provisional:local_shipping_unknown')

    # --- Local (supplier-country) tax — separate from Georgian VAT -----------
    if offer.supplier_tax_amount is not None:
        exact = offer.supplier_tax_source in ('parsed', 'checkout_simulation', 'manual_entry', 'manual_override')
        local_tax = bd.add(
            'local_tax', 'Local tax', to_gel(_dec(offer.supplier_tax_amount)),
            source=offer.supplier_tax_source, exact=exact,
            amount_original=_dec(offer.supplier_tax_amount), currency=offer.currency,
        )
    elif offer.tax_display_mode in ('prices_include_vat', 'no_local_tax'):
        note = (
            'VAT already included in the product price'
            if offer.tax_display_mode == 'prices_include_vat'
            else 'Supplier charges no local tax'
        )
        local_tax = bd.add('local_tax', 'Local tax', Decimal('0'), source='parsed', exact=True, note=note)
    else:
        rule = bd.resolve(RuleType.LOCAL_TAX, scope)
        if rule:
            handler = get_handler(RuleType.LOCAL_TAX)
            amount = handler.amount(
                rule.params, items_gel=item_cost, local_shipping_gel=local_shipping
            )
            is_none_mode = rule.params.get('mode', 'rate') == 'none'
            local_tax = bd.add(
                'local_tax', 'Local tax', amount,
                source='configured_rule', exact=is_none_mode, rule=rule,
                note='' if is_none_mode else f'{rule.params.get("rate")} on {rule.params.get("base", "items")}',
            )
            if not is_none_mode:
                bd.warn('estimated:local_tax')
        else:
            local_tax = bd.missing_rule(RuleType.LOCAL_TAX, 'local_tax', 'Local tax')

    # --- Local cost (import threshold comparison value) ------------------------
    local_cost = _q2(item_cost + local_shipping)

    # --- Chargeable weight = max(actual, volumetric) ---------------------------
    total_weight_g, weight_meta = resolve_chargeable_weight(offer, category, quantity)
    weight_exact = weight_meta.get('exact', False)
    if total_weight_g is None:
        bd.block('missing_weight')

    # --- International delivery (forwarder) ------------------------------------
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
            'international_shipping', f'{route.provider.name} international delivery',
            to_gel(amount, rate),
            source='configured_rule', exact=weight_exact, rule=intl_rule,
            amount_original=amount, currency=currency,
            note=(
                f'{kg} kg chargeable shipping weight '
                f'(source={weight_meta.get("shipping_source") or weight_meta.get("actual_source") or "unknown"}; '
                f'basis={weight_meta.get("basis") or "unknown"})'
            ),
        )
        if not weight_exact:
            bd.warn('estimated:weight')
    elif intl_rule:
        intl_shipping = bd.add(
            'international_shipping', f'{route.provider.name} international delivery', Decimal('0'),
            source='configured_rule', exact=False, rule=intl_rule,
            note='Weight unknown — delivery not computed',
        )
    else:
        intl_shipping = bd.missing_rule(
            RuleType.INTERNATIONAL_SHIPPING, 'international_shipping', 'International delivery'
        )

    # --- Payment fee (base defined by its rule; Onex: % of intl delivery) ------
    fee_rule = bd.resolve(RuleType.PAYMENT_FEE, route_scope)
    if fee_rule:
        handler = get_handler(RuleType.PAYMENT_FEE)
        payment_fee = bd.add(
            'payment_fee', f'{route.provider.name} payment fee',
            handler.amount(
                fee_rule.params,
                intl_shipping_gel=intl_shipping,
                items_plus_local_gel=item_cost + local_shipping + local_tax,
            ),
            source='configured_rule', exact=weight_exact, rule=fee_rule,
            note=f'base: {handler.base_kind(fee_rule.params)}',
        )
    else:
        payment_fee = bd.missing_rule(RuleType.PAYMENT_FEE, 'payment_fee', 'Payment fee')

    # --- Import threshold and Georgian import VAT.
    # All policy (rate, threshold, bases) comes from the configured rule; the
    # engine hardcodes no production tax logic.
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
                'georgia_vat', 'Import VAT',
                vat_handler.amount(
                    vat_rule.params,
                    items_gel=item_cost,
                    local_shipping_gel=local_shipping,
                    intl_shipping_gel=intl_shipping,
                    payment_fee_gel=payment_fee,
                ),
                source='configured_rule', exact=weight_exact, rule=vat_rule,
                note=f'Local cost {compared} GEL ≥ {threshold_gel} GEL threshold',
            )
        else:
            georgia_vat = bd.add(
                'georgia_vat', 'Import VAT', Decimal('0'),
                source='configured_rule', exact=True, rule=vat_rule,
                note=f'Local cost {compared} GEL below the {threshold_gel} GEL threshold — no declaration',
            )
    else:
        georgia_vat = bd.missing_rule(RuleType.GEORGIA_VAT, 'georgia_vat', 'Import VAT')

    # --- Customs declaration fee ------------------------------------------------
    customs_rule = bd.resolve(RuleType.CUSTOMS, route_scope)
    if customs_rule:
        handler = get_handler(RuleType.CUSTOMS)
        if declaration_required or handler.applies_always(customs_rule.params):
            customs = bd.add(
                'customs', 'Customs declaration fee',
                handler.amount(customs_rule.params, items_gel=item_cost),
                source='configured_rule', exact=True, rule=customs_rule,
            )
        else:
            customs = bd.add(
                'customs', 'Customs declaration fee', Decimal('0'),
                source='configured_rule', exact=True, rule=customs_rule,
                note='No import declaration required',
            )
    else:
        customs = bd.missing_rule(RuleType.CUSTOMS, 'customs', 'Customs declaration fee')

    # --- Forwarder declaration service (only when import is declared) -----------
    declaration_rule = bd.resolve(RuleType.DECLARATION_SERVICE, route_scope)
    if declaration_required and declaration_rule:
        declaration_service = bd.add(
            'declaration_service', f'{route.provider.name} declaration service',
            get_handler(RuleType.DECLARATION_SERVICE).amount(declaration_rule.params),
            source='configured_rule', exact=True, rule=declaration_rule,
        )
    elif declaration_required:
        declaration_service = bd.missing_rule(
            RuleType.DECLARATION_SERVICE, 'declaration_service', 'Declaration service'
        )
    else:
        declaration_service = bd.add(
            'declaration_service', 'Declaration service', Decimal('0'),
            source='configured_rule', exact=True, rule=declaration_rule,
            note='No import declaration required',
        )

    # --- Optional configured add-ons (absent rule = not applicable) -------------
    foreign_base = item_cost + local_shipping + local_tax + payment_fee + intl_shipping

    rule = bd.resolve(RuleType.INSURANCE, route_scope)
    insurance = bd.add(
        'insurance', 'Insurance',
        get_handler(RuleType.INSURANCE).amount(rule.params, base_gel=item_cost) if rule else Decimal('0'),
        source='configured_rule', exact=bool(rule), rule=rule,
        note='' if rule else 'No insurance rule configured — treated as not applicable',
    )

    rule = bd.resolve(RuleType.FX_BUFFER, scope)
    fx_buffer_rate = str(rule.params.get('rate', '')) if rule else None
    fx_buffer = bd.add(
        'fx_buffer', 'FX buffer',
        get_handler(RuleType.FX_BUFFER).amount(rule.params, base_gel=foreign_base) if rule else Decimal('0'),
        source='configured_rule', exact=bool(rule), rule=rule,
        note='' if rule else 'No FX buffer rule configured — treated as not applicable',
    )

    rule = bd.resolve(RuleType.RISK_RESERVE, scope)
    risk_reserve = bd.add(
        'risk_reserve', 'Risk reserve',
        get_handler(RuleType.RISK_RESERVE).amount(rule.params, base_gel=foreign_base) if rule else Decimal('0'),
        source='configured_rule', exact=bool(rule), rule=rule,
        note='' if rule else 'No risk reserve rule configured — treated as not applicable',
    )

    rule = bd.resolve(RuleType.HANDLING, scope)
    handling = bd.add(
        'handling_cost', 'Handling',
        get_handler(RuleType.HANDLING).amount(rule.params) if rule else Decimal('0'),
        source='configured_rule', exact=bool(rule), rule=rule,
        note='' if rule else 'No handling rule configured — treated as not applicable',
    )

    # --- Landed cost -------------------------------------------------------------
    landed_cost = _q2(
        item_cost + local_shipping + local_tax + intl_shipping + payment_fee
        + georgia_vat + customs + declaration_service
        + insurance + fx_buffer + risk_reserve + handling
    )

    # --- Selling side: net sales coefficient, break-even, three prices -----------
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

    # customer_price used for ranking = the standard selling price.
    auto_customer_price = prices.get('standard', landed_cost)
    customer_price = auto_customer_price
    manual_price = 'customer_price' in bd.overrides
    if manual_price:
        customer_price = _q2(bd.overrides['customer_price'])
        bd.warn('override:customer_price')
    margin_amount = _q2(customer_price - landed_cost)

    bd.add(
        'margin', 'Margin (standard price − landed cost)', margin_amount,
        source='manual_override' if manual_price else 'configured_rule',
        exact=bool(margin_rule), rule=margin_rule,
        note=(
            f'markups: +{markups["minimum"]} / +{markups["standard"]} / +{markups["premium"]} on break-even'
            if markups else ''
        ),
    )

    # --- Status and confidence ----------------------------------------------------
    status = (
        CostScenario.Status.CALCULATION_BLOCKED
        if bd.blocking_issues
        else CostScenario.Status.CALCULATED
    )

    estimated_total = sum(
        Decimal(component['amount_gel'])
        for component in bd.components
        if not component['exact'] and component['code'] != 'margin'
    )
    has_missing_rules = any(w.startswith(('missing_rule:', 'blocking:')) for w in bd.warnings)
    if estimated_total == 0 and not bd.warnings:
        confidence = CostScenario.Confidence.EXACT
    elif (
        not has_missing_rules
        and landed_cost > 0
        and estimated_total / landed_cost <= Decimal('0.15')
    ):
        confidence = CostScenario.Confidence.MOSTLY_EXACT
    else:
        confidence = CostScenario.Confidence.ESTIMATED

    return CostScenario(
        buying_request=request,
        supplier_offer=offer,
        fulfillment_route=route,
        scenario_type=scenario_type,
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
        landed_cost=landed_cost,
        minimum_margin=_q2(prices['minimum'] - landed_cost) if prices else Decimal('0'),
        margin_amount=margin_amount,
        margin_rate=markups.get('standard'),
        break_even_price=break_even,
        price_minimum=prices.get('minimum', Decimal('0')),
        price_standard=prices.get('standard', Decimal('0')),
        price_premium=prices.get('premium', Decimal('0')),
        customer_price=customer_price,
        chargeable_weight_g=total_weight_g,
        estimated_min_days=route.estimated_min_days,
        estimated_max_days=route.estimated_max_days,
        calculation_version=CALCULATION_VERSION,
        calculation_details={
            'components': bd.components,
            'fx': {**fx_meta, 'rate_gel': str(fx_rate), 'buffer_rate': fx_buffer_rate},
            'weight': weight_meta,
            'local_shipping_status': local_shipping_status,
            'free_shipping': free_ship_meta,
            'import': {
                'local_cost_gel': str(local_cost),
                'threshold_gel': str(threshold_gel) if threshold_gel is not None else None,
                'declaration_required': declaration_required,
                'rule': _rule_snapshot(vat_rule),
                'local_shipping_status': local_shipping_status,
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
            'quantity': quantity,
            'overrides': {code: str(_q2(value)) for code, value in (overrides or {}).items()},
            'blocking_issues': bd.blocking_issues,
            'auto_customer_price': str(auto_customer_price),
            'manual_price': manual_price,
        },
        confidence=confidence,
        warnings=bd.warnings,
    )
