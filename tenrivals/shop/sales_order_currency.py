"""Payment currency helpers for staff sales orders (amounts stored in GEL)."""

from __future__ import annotations

from decimal import Decimal

PAYMENT_CURRENCY_CHOICES: tuple[tuple[str, str], ...] = (
    ('GEL', 'GEL (₾)'),
    ('USD', 'USD ($)'),
    ('EUR', 'EUR (€)'),
    ('GBP', 'GBP (£)'),
    ('CHF', 'CHF'),
    ('AED', 'AED'),
    ('TRY', 'TRY'),
)

# ISO codes for HTML datalist (staff can also type any code up to 8 chars).
PAYMENT_CURRENCY_SUGGESTIONS: tuple[str, ...] = tuple(c[0] for c in PAYMENT_CURRENCY_CHOICES)

CURRENCY_SYMBOLS: dict[str, str] = {
    'GEL': '₾',
    'USD': '$',
    'EUR': '€',
    'GBP': '£',
    'CHF': 'CHF',
    'AED': 'AED',
    'TRY': '₺',
}


def normalize_payment_currency(code: str | None) -> str:
    return ((code or 'GEL').strip().upper() or 'GEL')[:8]


def payment_currency_symbol(code: str | None) -> str:
    cur = normalize_payment_currency(code)
    return CURRENCY_SYMBOLS.get(cur, cur)


def convert_gel_amount(amount_gel: Decimal, exchange_rate: Decimal) -> Decimal:
    return (amount_gel * exchange_rate).quantize(Decimal('0.01'))


def apply_payment_currency_fields(order, gross_total: Decimal) -> None:
    """Set payment_currency, exchange_rate, amount_in_payment_currency from gross (GEL)."""
    cur = normalize_payment_currency(getattr(order, 'payment_currency', None))
    order.payment_currency = cur
    rate = getattr(order, 'exchange_rate', None) or Decimal('1')
    try:
        rate = Decimal(str(rate))
    except Exception:
        rate = Decimal('1')
    if cur == 'GEL':
        order.exchange_rate = Decimal('1')
        order.amount_in_payment_currency = Decimal(str(gross_total)).quantize(Decimal('0.01'))
        return
    if rate <= 0:
        rate = Decimal('1')
    order.exchange_rate = rate.quantize(Decimal('0.000001'))
    order.amount_in_payment_currency = convert_gel_amount(
        Decimal(str(gross_total)),
        order.exchange_rate,
    )


def invoice_uses_foreign_currency(order) -> bool:
    return normalize_payment_currency(getattr(order, 'payment_currency', None)) != 'GEL'
