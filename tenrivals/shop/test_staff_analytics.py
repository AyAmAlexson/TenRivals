from datetime import date
from decimal import Decimal

from django.test import TestCase

from shop.models import Customer, ProductType, SalesOrder, SalesOrderLine
from shop.staff_analytics import (
    _finalize_metrics,
    compute_order_economics,
)


class FinancialMetricsTests(TestCase):
    """Canonical formulas from tennis_rivals_financial_metrics.md."""

    def setUp(self):
        self.customer = Customer.objects.create(
            first_name='Nika',
            last_name='Metrics',
            email='metrics@test.com',
            phone='555',
        )

    def _order(self, *, card=True, gross='700.00', vat='107.00', net='593.00', cost='450.00'):
        order = SalesOrder.objects.create(
            customer=self.customer,
            order_date=date(2026, 9, 1),
            status=SalesOrder.Status.COMPLETED,
            payment_method='Card terminal' if card else 'Cash',
            gross_total=Decimal(gross),
            vat_total=Decimal(vat),
            net_total=Decimal(net),
            invoice_number='FIN-700',
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
        self.assertNotIn('income', m)

    def test_cash_payment_has_no_acquiring(self):
        order = self._order(card=False)
        order.invoice_number = 'FIN-CASH'
        order.save(update_fields=['invoice_number'])
        m = _finalize_metrics(compute_order_economics(order))
        self.assertEqual(m['acquiring'], Decimal('0.00'))
        self.assertEqual(m['net_proceeds'], Decimal('586.00'))
        self.assertEqual(m['gross_profit'], Decimal('143.00'))
        self.assertEqual(m['profit'], Decimal('136.00'))
