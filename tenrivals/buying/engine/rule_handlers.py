"""Per-rule-type handlers: params validation + interpretation.

Keeps the single CalculationRule table from becoming a God Object in code:
the pricing engine never parses rule params inline — it asks the handler
registered for the rule type. Params are validated at save time (forms call
validate_params), not discovered broken during a calculation.
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from buying.models import CalculationRule

RuleType = CalculationRule.RuleType


class RuleParamsError(Exception):
    pass


def _dec(params: dict, key: str, default: str = '0') -> Decimal:
    value = params.get(key)
    if value in (None, ''):
        return Decimal(default)
    return Decimal(str(value))


def _check_decimal(params, key, errors, *, required=False, minimum=None, below_one=False):
    value = params.get(key)
    if value in (None, ''):
        if required:
            errors.append(f'"{key}" is required')
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        errors.append(f'"{key}" must be a decimal number')
        return None
    if minimum is not None and number < minimum:
        errors.append(f'"{key}" must be >= {minimum}')
    if below_one and number >= 1:
        errors.append(f'"{key}" must be below 1 (a fraction, e.g. 0.18 for 18%)')
    return number


class BaseHandler:
    rule_type: str = ''

    def validate_params(self, params: dict) -> list[str]:
        """Return a list of human-readable errors; empty list means valid."""
        if not isinstance(params, dict):
            return ['params must be a JSON object']
        return self._validate(params)

    def _validate(self, params: dict) -> list[str]:
        return []


class FxRateHandler(BaseHandler):
    rule_type = RuleType.FX_RATE

    def _validate(self, params):
        errors = []
        currency = params.get('currency')
        if not currency or not isinstance(currency, str) or len(currency) != 3:
            errors.append('"currency" must be a 3-letter ISO code, e.g. "USD"')
        _check_decimal(params, 'rate_gel', errors, required=True, minimum=Decimal('0.000001'))
        return errors

    def rate_gel(self, params) -> Decimal:
        return _dec(params, 'rate_gel')


class LocalTaxHandler(BaseHandler):
    rule_type = RuleType.LOCAL_TAX
    BASES = ('items', 'items_plus_local_shipping')

    def _validate(self, params):
        errors = []
        mode = params.get('mode', 'rate')
        if mode not in ('rate', 'none'):
            errors.append('"mode" must be "rate" or "none"')
        if mode == 'rate':
            _check_decimal(params, 'rate', errors, required=True, minimum=Decimal('0'), below_one=True)
            if params.get('base', 'items') not in self.BASES:
                errors.append(f'"base" must be one of {self.BASES}')
        return errors

    def amount(self, params, *, items_gel: Decimal, local_shipping_gel: Decimal) -> Decimal:
        if params.get('mode', 'rate') == 'none':
            return Decimal('0')
        base = items_gel
        if params.get('base', 'items') == 'items_plus_local_shipping':
            base += local_shipping_gel
        return base * _dec(params, 'rate')


class LocalShippingHandler(BaseHandler):
    rule_type = RuleType.LOCAL_SHIPPING

    def _validate(self, params):
        errors = []
        _check_decimal(params, 'amount', errors, required=True, minimum=Decimal('0'))
        _check_decimal(params, 'free_over', errors, minimum=Decimal('0'))
        currency = params.get('currency')
        if currency and (not isinstance(currency, str) or len(currency) != 3):
            errors.append('"currency" must be a 3-letter ISO code')
        return errors

    def amount_in_currency(self, params, *, items_in_currency: Decimal) -> Decimal:
        free_over = params.get('free_over')
        if free_over not in (None, '') and items_in_currency >= Decimal(str(free_over)):
            return Decimal('0')
        return _dec(params, 'amount')


class PaymentFeeHandler(BaseHandler):
    rule_type = RuleType.PAYMENT_FEE
    BASES = ('international_shipping', 'items_plus_local')

    def _validate(self, params):
        errors = []
        rate = _check_decimal(params, 'rate', errors, minimum=Decimal('0'), below_one=True)
        fixed = _check_decimal(params, 'fixed_gel', errors, minimum=Decimal('0'))
        if rate is None and fixed is None:
            errors.append('Provide "rate" and/or "fixed_gel"')
        if params.get('base', 'international_shipping') not in self.BASES:
            errors.append(f'"base" must be one of {self.BASES}')
        return errors

    def base_kind(self, params) -> str:
        return str(params.get('base', 'international_shipping'))

    def amount(self, params, *, intl_shipping_gel: Decimal, items_plus_local_gel: Decimal) -> Decimal:
        base = (
            intl_shipping_gel
            if self.base_kind(params) == 'international_shipping'
            else items_plus_local_gel
        )
        return base * _dec(params, 'rate') + _dec(params, 'fixed_gel')


class InternationalShippingHandler(BaseHandler):
    rule_type = RuleType.INTERNATIONAL_SHIPPING

    def _validate(self, params):
        errors = []
        currency = params.get('currency')
        if not currency or not isinstance(currency, str) or len(currency) != 3:
            errors.append('"currency" must be a 3-letter ISO code')
        _check_decimal(params, 'per_kg', errors, required=True, minimum=Decimal('0'))
        _check_decimal(params, 'min_kg', errors, minimum=Decimal('0'))
        _check_decimal(params, 'step_kg', errors, minimum=Decimal('0'))
        _check_decimal(params, 'min_charge', errors, minimum=Decimal('0'))
        return errors

    def currency(self, params) -> str:
        return str(params.get('currency') or 'USD')

    def amount_in_currency(self, params, *, weight_g: int) -> tuple[Decimal, Decimal]:
        """Return (amount in tariff currency, chargeable kg)."""
        kg = Decimal(weight_g) / Decimal('1000')
        step_kg = _dec(params, 'step_kg')
        if step_kg > 0:
            kg = Decimal(math.ceil(kg / step_kg)) * step_kg
        min_kg = _dec(params, 'min_kg')
        if min_kg > 0:
            kg = max(kg, min_kg)
        amount = kg * _dec(params, 'per_kg')
        min_charge = _dec(params, 'min_charge')
        if min_charge > 0:
            amount = max(amount, min_charge)
        return amount, kg


class GeorgiaVatHandler(BaseHandler):
    """Georgian import VAT. The rule owns all policy: the rate, the threshold,
    what the threshold is compared against (local cost = items + local
    shipping) and what the VAT base is. The engine hardcodes nothing."""

    rule_type = RuleType.GEORGIA_VAT
    THRESHOLD_BASES = ('local_cost', 'items')
    BASES = ('items', 'items_plus_intl', 'local_cost_plus_intl_plus_fee')

    def _validate(self, params):
        errors = []
        _check_decimal(params, 'rate', errors, required=True, minimum=Decimal('0'), below_one=True)
        _check_decimal(params, 'threshold_gel', errors, minimum=Decimal('0'))
        if params.get('threshold_base', 'local_cost') not in self.THRESHOLD_BASES:
            errors.append(f'"threshold_base" must be one of {self.THRESHOLD_BASES}')
        if params.get('base', 'local_cost_plus_intl_plus_fee') not in self.BASES:
            errors.append(f'"base" must be one of {self.BASES}')
        return errors

    def declaration_required(
        self, params, *, items_gel: Decimal, local_shipping_gel: Decimal
    ) -> tuple[bool, Decimal, Decimal]:
        """Return (required, threshold, compared amount). International shipping
        never enters the threshold comparison."""
        threshold = _dec(params, 'threshold_gel')
        compared = items_gel
        if params.get('threshold_base', 'local_cost') == 'local_cost':
            compared += local_shipping_gel
        if threshold <= 0:
            return True, threshold, compared
        return compared >= threshold, threshold, compared

    def amount(
        self,
        params,
        *,
        items_gel: Decimal,
        local_shipping_gel: Decimal,
        intl_shipping_gel: Decimal,
        payment_fee_gel: Decimal,
    ) -> Decimal:
        base_kind = params.get('base', 'local_cost_plus_intl_plus_fee')
        if base_kind == 'items':
            base = items_gel
        elif base_kind == 'items_plus_intl':
            base = items_gel + intl_shipping_gel
        else:
            base = items_gel + local_shipping_gel + intl_shipping_gel + payment_fee_gel
        return base * _dec(params, 'rate')


class CustomsHandler(BaseHandler):
    rule_type = RuleType.CUSTOMS
    APPLIES = ('when_declared', 'always')

    def _validate(self, params):
        errors = []
        fixed = _check_decimal(params, 'fixed_gel', errors, minimum=Decimal('0'))
        rate = _check_decimal(params, 'rate', errors, minimum=Decimal('0'), below_one=True)
        if fixed is None and rate is None:
            errors.append('Provide "fixed_gel" and/or "rate" (use "fixed_gel": "0" for no customs)')
        if params.get('applies', 'when_declared') not in self.APPLIES:
            errors.append(f'"applies" must be one of {self.APPLIES}')
        return errors

    def applies_always(self, params) -> bool:
        return params.get('applies', 'when_declared') == 'always'

    def amount(self, params, *, items_gel: Decimal) -> Decimal:
        return _dec(params, 'fixed_gel') + items_gel * _dec(params, 'rate')


class DeclarationServiceHandler(BaseHandler):
    """Forwarder's declaration service fee (e.g. Onex), charged only when an
    import declaration is required."""

    rule_type = RuleType.DECLARATION_SERVICE

    def _validate(self, params):
        errors = []
        _check_decimal(params, 'fixed_gel', errors, required=True, minimum=Decimal('0'))
        return errors

    def amount(self, params) -> Decimal:
        return _dec(params, 'fixed_gel')


class SalesVatHandler(BaseHandler):
    """VAT paid by Tennis Rivals when selling; included in the sale price."""

    rule_type = RuleType.SALES_VAT
    MODES = ('included_in_sale_price',)

    def _validate(self, params):
        errors = []
        _check_decimal(params, 'rate', errors, required=True, minimum=Decimal('0'), below_one=True)
        if params.get('mode', 'included_in_sale_price') not in self.MODES:
            errors.append(f'"mode" must be one of {self.MODES}')
        return errors

    def rate(self, params) -> Decimal:
        return _dec(params, 'rate')

    def coefficient_share(self, params) -> Decimal:
        """Share of the gross sale price this tax takes: r / (1 + r)."""
        rate = _dec(params, 'rate')
        return rate / (Decimal('1') + rate)


class SmallBusinessTaxHandler(BaseHandler):
    rule_type = RuleType.SMALL_BUSINESS_TAX
    MODES = ('percentage_of_gross_sale_price',)

    def _validate(self, params):
        errors = []
        _check_decimal(params, 'rate', errors, required=True, minimum=Decimal('0'), below_one=True)
        if params.get('mode', 'percentage_of_gross_sale_price') not in self.MODES:
            errors.append(f'"mode" must be one of {self.MODES}')
        return errors

    def rate(self, params) -> Decimal:
        return _dec(params, 'rate')

    def coefficient_share(self, params) -> Decimal:
        return _dec(params, 'rate')


class RateOrFixedHandler(BaseHandler):
    """Shared shape: optional "rate" on a base + optional "fixed_gel"."""

    def _validate(self, params):
        errors = []
        rate = _check_decimal(params, 'rate', errors, minimum=Decimal('0'), below_one=True)
        fixed = _check_decimal(params, 'fixed_gel', errors, minimum=Decimal('0'))
        if rate is None and fixed is None:
            errors.append('Provide "rate" and/or "fixed_gel"')
        return errors

    def amount(self, params, *, base_gel: Decimal) -> Decimal:
        return base_gel * _dec(params, 'rate') + _dec(params, 'fixed_gel')


class FxBufferHandler(RateOrFixedHandler):
    rule_type = RuleType.FX_BUFFER


class RiskReserveHandler(RateOrFixedHandler):
    rule_type = RuleType.RISK_RESERVE


class InsuranceHandler(RateOrFixedHandler):
    rule_type = RuleType.INSURANCE


class HandlingHandler(BaseHandler):
    rule_type = RuleType.HANDLING

    def _validate(self, params):
        errors = []
        _check_decimal(params, 'fixed_gel', errors, required=True, minimum=Decimal('0'))
        return errors

    def amount(self, params) -> Decimal:
        return _dec(params, 'fixed_gel')


class MarginHandler(BaseHandler):
    """Selling price markups applied to the BREAK-EVEN price (not landed cost):
    params {"minimum": "0.03", "standard": "0.10", "premium": "0.25"}."""

    rule_type = RuleType.MARGIN
    LEVELS = ('minimum', 'standard', 'premium')

    def _validate(self, params):
        errors = []
        for level in self.LEVELS:
            _check_decimal(params, level, errors, required=True, minimum=Decimal('0'))
        return errors

    def markups(self, params) -> dict[str, Decimal]:
        return {level: _dec(params, level) for level in self.LEVELS}


class WeightHandler(BaseHandler):
    rule_type = RuleType.WEIGHT

    def _validate(self, params):
        errors = []
        default = _check_decimal(params, 'default_g', errors, minimum=Decimal('1'))
        _check_decimal(params, 'packaging_g', errors, minimum=Decimal('0'))
        if default is None and params.get('packaging_g') in (None, ''):
            errors.append(
                'Provide "default_g" (category parcel/shipping weight in grams) '
                'and/or "packaging_g"'
            )
        return errors


class VolumetricWeightHandler(BaseHandler):
    """Volumetric weight: the standard formula L×W×H (cm) / divisor = kg when
    package dimensions are known, or a per-category fallback norm."""

    rule_type = RuleType.VOLUMETRIC_WEIGHT

    def _validate(self, params):
        errors = []
        divisor = _check_decimal(params, 'divisor', errors, minimum=Decimal('1'))
        default = _check_decimal(params, 'default_volumetric_g', errors, minimum=Decimal('1'))
        if divisor is None and default is None:
            errors.append('Provide "divisor" (standard formula) and/or "default_volumetric_g"')
        return errors

    def divisor(self, params) -> Decimal | None:
        value = params.get('divisor')
        return Decimal(str(value)) if value not in (None, '') else None


class RoundingHandler(BaseHandler):
    rule_type = RuleType.ROUNDING

    def _validate(self, params):
        errors = []
        _check_decimal(params, 'step_gel', errors, required=True, minimum=Decimal('0.01'))
        return errors

    def step_gel(self, params) -> Decimal:
        return _dec(params, 'step_gel', '1')


class AllocationHandler(BaseHandler):
    rule_type = RuleType.ALLOCATION
    METHODS = ('by_value', 'by_weight', 'by_volumetric_weight', 'hybrid', 'manual')

    def _validate(self, params):
        if params.get('method') not in self.METHODS:
            return [f'"method" must be one of {self.METHODS}']
        return []


HANDLERS: dict[str, BaseHandler] = {
    handler.rule_type: handler
    for handler in (
        FxRateHandler(),
        LocalTaxHandler(),
        LocalShippingHandler(),
        PaymentFeeHandler(),
        InternationalShippingHandler(),
        GeorgiaVatHandler(),
        CustomsHandler(),
        DeclarationServiceHandler(),
        SalesVatHandler(),
        SmallBusinessTaxHandler(),
        FxBufferHandler(),
        RiskReserveHandler(),
        InsuranceHandler(),
        HandlingHandler(),
        MarginHandler(),
        WeightHandler(),
        VolumetricWeightHandler(),
        RoundingHandler(),
        AllocationHandler(),
    )
}


def get_handler(rule_type: str) -> BaseHandler:
    try:
        return HANDLERS[rule_type]
    except KeyError as exc:
        raise RuleParamsError(f'No handler registered for rule type "{rule_type}"') from exc


def validate_rule_params(rule_type: str, params: dict) -> list[str]:
    return get_handler(rule_type).validate_params(params)
