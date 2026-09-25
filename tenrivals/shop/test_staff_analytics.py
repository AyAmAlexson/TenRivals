from datetime import date
from decimal import Decimal

from django.test import TestCase

from shop.models import (
    Customer,
    ProductType,
    SalesOrder,
    SalesOrderLine,
    SalesOrderPayment,
)
from shop.staff_analytics import (
    _finalize_metrics,
    build_sales_analytics,
    compute_order_economics,
    order_card_ratio,
)


class FinancialMetricsTests(TestCase):
    """Canonical formulas from tennis_rivals_financial_metrics.md.

    Acquiring is charged on money actually received by card, per payment.
    """

    def setUp(self):
        self.customer = Customer.objects.create(
            first_name='Nika',
            last_name='Metrics',
            email='metrics@test.com',
            phone='555',
        )

    def _order(
        self,
        *,
        payments=(('Card terminal', '700.00'),),
        gross='700.00',
        vat='107.00',
        net='593.00',
        cost='450.00',
        invoice='FIN-700',
    ):
        order = SalesOrder.objects.create(
            customer=self.customer,
            order_date=date(2026, 9, 1),
            status=SalesOrder.Status.COMPLETED,
            gross_total=Decimal(gross),
            vat_total=Decimal(vat),
            net_total=Decimal(net),
            invoice_number=invoice,
        )
        SalesOrderLine.objects.create(
            order=order,
            custom_label='Yonex EZONE 100',
            product_type=ProductType.RACKET,
            quantity=1,
            unit_price_gross=Decimal(gross),
            line_gross=Decimal(gross),
            line_vat=Decimal(vat),
            line_net=Decimal(net),
            landed_cost_gel=Decimal(cost),
            sale_channel=SalesOrderLine.SaleChannel.STOCK,
        )
        for i, (method, amount) in enumerate(payments):
            SalesOrderPayment.objects.create(
                order=order,
                paid_on=date(2026, 9, 1 + i),
                amount_gross=Decimal(amount),
                payment_method=method,
                fiscal_receipt=f'{invoice}-{i + 1}',
            )
        order.sync_payment_summary()
        return order

    def test_spec_example_card_payment(self):
        order = self._order()
        m = _finalize_metrics(compute_order_economics(order))
        self.assertEqual(m['revenue'], Decimal('700.00'))
        self.assertEqual(m['vat'], Decimal('107.00'))
        self.assertEqual(m['tax'], Decimal('7.00'))
        self.assertEqual(m['acquiring'], Decimal('14.00'))
        self.assertEqual(m['cogs'], Decimal('450.00'))
        self.assertEqual(m['net'], Decimal('593.00'))
        self.assertEqual(m['net_proceeds'], Decimal('572.00'))
        self.assertEqual(m['gross_profit'], Decimal('143.00'))
        self.assertEqual(m['profit'], Decimal('122.00'))
        self.assertEqual(m['net_proceeds'], m['cogs'] + m['profit'])
        self.assertEqual(m['card_revenue'], Decimal('700.00'))
        self.assertEqual(m['card_share_pct'], Decimal('100.00'))
        self.assertNotIn('income', m)

    def test_cash_payment_has_no_acquiring(self):
        order = self._order(payments=(('Cash', '700.00'),), invoice='FIN-CASH')
        m = _finalize_metrics(compute_order_economics(order))
        self.assertEqual(m['acquiring'], Decimal('0.00'))
        self.assertEqual(m['card_revenue'], Decimal('0.00'))
        self.assertEqual(m['net_proceeds'], Decimal('586.00'))
        self.assertEqual(m['gross_profit'], Decimal('143.00'))
        self.assertEqual(m['profit'], Decimal('136.00'))

    def test_mixed_payments_charge_acquiring_only_on_card_part(self):
        # 300 by card (prepayment) + 400 cash on delivery.
        order = self._order(
            payments=(('Card', '300.00'), ('Cash', '400.00')), invoice='FIN-MIX'
        )
        self.assertEqual(order.payment_method, 'Card / Cash')
        raw = compute_order_economics(order)
        m = _finalize_metrics(dict(raw))
        self.assertEqual(m['card_revenue'], Decimal('300.00'))
        self.assertEqual(m['acquiring'], Decimal('6.00'))
        self.assertEqual(m['profit'], Decimal('130.00'))
        self.assertEqual(order_card_ratio(raw), Decimal('300.00') / Decimal('700.00'))

    def test_unpaid_order_has_no_acquiring_yet(self):
        order = self._order(payments=(), invoice='FIN-UNPAID')
        m = _finalize_metrics(compute_order_economics(order))
        self.assertEqual(m['acquiring'], Decimal('0.00'))
        self.assertEqual(m['card_revenue'], Decimal('0.00'))
        self.assertEqual(m['revenue'], Decimal('700.00'))

    def test_card_refund_reduces_acquiring(self):
        order = self._order(
            payments=(('Card', '700.00'), ('Card refund', '-200.00')),
            invoice='FIN-REF',
        )
        m = _finalize_metrics(compute_order_economics(order))
        self.assertEqual(m['card_revenue'], Decimal('500.00'))
        self.assertEqual(m['acquiring'], Decimal('10.00'))

    def test_analytics_line_acquiring_uses_card_share(self):
        self._order(payments=(('Card', '350.00'), ('Cash', '350.00')), invoice='FIN-HALF')
        data = build_sales_analytics(date(2026, 9, 1), date(2026, 9, 30), 'month')
        self.assertEqual(data['totals']['acquiring'], Decimal('7.00'))
        prow = next(r for r in data['top_products'] if r['label'] == 'Yonex EZONE 100')
        # gross profit 143 − tax 7 − acquiring 700×2%×50% = 7 → 129
        self.assertEqual(prow['profit'], Decimal('129.00'))
