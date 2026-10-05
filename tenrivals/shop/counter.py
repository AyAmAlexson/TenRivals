"""Offline counter: one cart becomes one sales order for the walk-in customer."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import Customer, Product, SalesOrder, SalesOrderLine, SalesOrderPayment
from .sales_order_currency import apply_payment_currency_fields
from .sales_order_stock import (
    release_lines_to_stock,
    snapshot_old_lines,
    take_lines_from_stock,
    validate_order_line_demands,
)
from .sales_order_utils import allocate_invoice_number, line_amounts, product_unit_gross_price

WALK_IN_EMAIL = 'walk-in@counter.tenrivals'
COUNTER_NOTE_HEADER = 'Counter sale'

PAY_CASH = 'cash'
PAY_CARD = 'card'
PAY_TRANSFER = 'transfer'
PAY_SPLIT = 'split'
PAY_METHODS = {
    PAY_CASH: 'Cash',
    PAY_CARD: 'Card terminal',
    PAY_TRANSFER: 'Transfer',
    PAY_SPLIT: 'Split',
}


def walk_in_customer() -> Customer:
    customer, _created = Customer.objects.get_or_create(
        email=WALK_IN_EMAIL,
        defaults={'first_name': 'Walk-in', 'last_name': 'Customer'},
    )
    return customer


def store_note(store_name: str) -> str:
    return f'{COUNTER_NOTE_HEADER}\nStore: {(store_name or "").strip()}'


def _q2(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal('0.01'))


def _specs(cart_rows: list[dict]) -> list[dict]:
    specs = []
    for row in cart_rows:
        qty = int(row['qty'])
        if qty < 1:
            raise ValueError('Quantity must be at least 1.')
        price = _q2(row['unit_price'])
        if price < 0:
            raise ValueError('Price cannot be negative.')
        product = row['product']
        variant = (row.get('variant') or '').strip()[:48]
        lg, lv, ln = line_amounts(qty, price, Decimal('0'))
        specs.append(
            {
                'product': product,
                'variant': variant,
                'qty': qty,
                'price': price,
                'lg': lg,
                'lv': lv,
                'ln': ln,
            }
        )
    if not specs:
        raise ValueError('Add at least one product.')
    return specs


def _demands(specs: list[dict]):
    return [(spec['product'], spec['variant'], spec['qty']) for spec in specs]


def _write_lines(order: SalesOrder, specs: list[dict]) -> None:
    for spec in specs:
        product = spec['product']
        SalesOrderLine.objects.create(
            order=order,
            product=product,
            product_type=product.type or '',
            variant_label=spec['variant'],
            quantity=spec['qty'],
            unit_price_gross=spec['price'],
            discount_percent=Decimal('0'),
            landed_cost_gel=product.landed_cost_gel,
            sale_channel=SalesOrderLine.SaleChannel.STOCK,
            line_gross=spec['lg'],
            line_vat=spec['lv'],
            line_net=spec['ln'],
        )


def _apply_totals(order: SalesOrder, specs: list[dict]) -> None:
    gross = sum((spec['lg'] for spec in specs), Decimal('0.00'))
    vat = sum((spec['lv'] for spec in specs), Decimal('0.00'))
    net = sum((spec['ln'] for spec in specs), Decimal('0.00'))
    order.gross_total = _q2(gross)
    order.vat_total = _q2(vat)
    order.net_total = _q2(net)
    apply_payment_currency_fields(order, order.gross_total)


def create_counter_order(*, store_name: str, cart_rows: list[dict]) -> SalesOrder:
    specs = _specs(cart_rows)
    errors = validate_order_line_demands(
        _demands(specs),
        order_pk=None,
        old_status=None,
        old_lines=[],
    )
    if errors:
        raise ValueError(' '.join(errors))
    with transaction.atomic():
        order = SalesOrder(
            customer=walk_in_customer(),
            order_date=timezone.localdate(),
            status=SalesOrder.Status.AWAITING_PAYMENT,
            notes=store_note(store_name),
            invoice_number=allocate_invoice_number(timezone.localdate().year),
        )
        _apply_totals(order, specs)
        order.save()
        _write_lines(order, specs)
        take_lines_from_stock(snapshot_old_lines(order))
        return order


def replace_counter_lines(order: SalesOrder, cart_rows: list[dict]) -> SalesOrder:
    if order.status != SalesOrder.Status.AWAITING_PAYMENT:
        raise ValueError('This sale is already closed. Edit it from Orders.')
    specs = _specs(cart_rows)
    with transaction.atomic():
        locked = SalesOrder.objects.select_for_update().get(pk=order.pk)
        if locked.status != SalesOrder.Status.AWAITING_PAYMENT:
            raise ValueError('This sale is already closed. Edit it from Orders.')
        old_lines = snapshot_old_lines(locked)
        release_lines_to_stock(old_lines)
        errors = validate_order_line_demands(
            _demands(specs),
            order_pk=None,
            old_status=None,
            old_lines=[],
        )
        if errors:
            raise ValueError(' '.join(errors))
        locked.lines.all().delete()
        _apply_totals(locked, specs)
        locked.save()
        _write_lines(locked, specs)
        take_lines_from_stock(snapshot_old_lines(locked))
        return locked


def cancel_counter_order(order: SalesOrder) -> None:
    if order.status != SalesOrder.Status.AWAITING_PAYMENT:
        raise ValueError('Only an unpaid counter sale can be cancelled here.')
    with transaction.atomic():
        locked = SalesOrder.objects.select_for_update().get(pk=order.pk)
        if locked.status != SalesOrder.Status.AWAITING_PAYMENT:
            raise ValueError('Only an unpaid counter sale can be cancelled here.')
        release_lines_to_stock(snapshot_old_lines(locked))
        locked.status = SalesOrder.Status.CANCELLED
        locked.save(update_fields=['status', 'updated_at'])


def pay_counter_order(
    order: SalesOrder,
    *,
    method: str,
    fiscal_receipt: str,
    cash_amount: Decimal | None = None,
    card_amount: Decimal | None = None,
) -> SalesOrder:
    receipt = (fiscal_receipt or '').strip()
    if not receipt:
        raise ValueError('Enter the fiscal receipt number.')
    if method not in PAY_METHODS:
        raise ValueError('Choose a payment method.')
    if order.status != SalesOrder.Status.AWAITING_PAYMENT:
        raise ValueError('This sale is already closed.')
    gross = _q2(order.gross_total)
    if method == PAY_CASH:
        parts = [('Cash', gross)]
    elif method == PAY_CARD:
        parts = [('Card terminal', gross)]
    elif method == PAY_TRANSFER:
        parts = [('Transfer', gross)]
    else:
        cash = _q2(cash_amount or 0)
        card = _q2(card_amount or 0)
        if cash < 0 or card < 0:
            raise ValueError('Amounts cannot be negative.')
        if cash + card != gross:
            raise ValueError(f'Cash plus card must equal {gross} ₾.')
        if cash == 0 or card == 0:
            raise ValueError('Enter both a cash amount and a card amount.')
        parts = [('Cash', cash), ('Card terminal', card)]
    with transaction.atomic():
        locked = SalesOrder.objects.select_for_update().get(pk=order.pk)
        if locked.status != SalesOrder.Status.AWAITING_PAYMENT:
            raise ValueError('This sale is already closed.')
        for label, amount in parts:
            SalesOrderPayment.objects.create(
                order=locked,
                paid_on=locked.order_date,
                amount_gross=amount,
                payment_method=label,
                fiscal_receipt=receipt[:64],
            )
        locked.sync_payment_summary()
        locked.status = SalesOrder.Status.COMPLETED
        locked.save(update_fields=['status', 'updated_at'])
        return locked


def shelf_price(product: Product) -> Decimal:
    return product_unit_gross_price(product)
