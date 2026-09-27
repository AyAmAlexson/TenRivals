from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from shop.models import (
    Customer,
    Product,
    ProductListing,
    ProductListingChannel,
    ProductType,
    SalesOrder,
    SalesOrderLine,
    SalesOrderPayment,
)
from shop.staff_analytics import (
    _finalize_metrics,
    build_sales_analytics,
    compute_order_economics,
    expected_unit_economics,
    order_card_ratio,
)


class ExpectedUnitEconomicsTests(TestCase):
    def test_spec_example(self):
        # 700 ₾ shelf price, 450 ₾ landed: net 593.22, tax 7, profit 136.22
        eco = expected_unit_economics(Decimal('700.00'), Decimal('450.00'))
        self.assertEqual(eco['net'], Decimal('593.22'))
        self.assertEqual(eco['tax'], Decimal('7.00'))
        self.assertEqual(eco['profit'], Decimal('136.22'))
        self.assertEqual(eco['roi_pct'], Decimal('30.27'))
        self.assertEqual(eco['margin_pct'], Decimal('19.46'))

    def test_unknown_or_zero_cost_gives_none(self):
        self.assertIsNone(expected_unit_economics(Decimal('100'), None)['roi_pct'])
        self.assertIsNone(expected_unit_economics(Decimal('100'), Decimal('0'))['roi_pct'])
        self.assertIsNone(expected_unit_economics(None, Decimal('10'))['roi_pct'])

    def test_negative_roi_when_price_below_cost(self):
        eco = expected_unit_economics(Decimal('118.00'), Decimal('120.00'))
        self.assertEqual(eco['profit'], Decimal('-21.18'))
        self.assertLess(eco['roi_pct'], 0)


@override_settings(SECURE_SSL_REDIRECT=False)
class StaffStockListEconomicsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        staff = User.objects.create_superuser(email='stock-eco@test.com', password='x-secret-1')
        self.client.force_login(staff)
        self.product = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='ROI Grip',
            sku='GRIP-ROI',
            initial_price=Decimal('100.00'),
            actual_price=Decimal('80.00'),
            landed_cost_gel=Decimal('40.00'),
            in_stock=True,
            is_active=True,
        )
        ProductListing.objects.create(
            product=self.product, channel=ProductListingChannel.STOCK, quantity=3
        )

    def test_stock_rows_show_cost_prices_and_roi(self):
        resp = self.client.get(reverse('administration:staff_stock'))
        self.assertEqual(resp.status_code, 200)
        row = resp.context['listings'][0]
        # Sells at the discounted 80: net 67.80 − tax 0.80 − cost 40 = 27.00 → 67.5% → 68
        self.assertEqual(row.sell_price, Decimal('80.00'))
        self.assertTrue(row.has_discount)
        self.assertEqual(row.expected_profit, Decimal('27.00'))
        self.assertEqual(row.expected_roi_pct, Decimal('67.50'))
        self.assertEqual(row.expected_roi_int, 68)
        self.assertContains(resp, '68%')
        self.assertNotContains(resp, '67.50%')
        self.assertContains(resp, 'price-struck')
        self.assertContains(resp, 'Price ₾')
        self.assertContains(resp, 'item-sku')
        self.assertContains(resp, 'GRIP-ROI')
        html = resp.content.decode()
        thead = html[html.index('<thead>') : html.index('</thead>')]
        self.assertLess(thead.index('Type'), thead.index('Brand'))
        self.assertNotIn('>SKU<', thead)

    def test_preorder_page_has_no_economics_columns(self):
        ProductListing.objects.create(
            product=self.product, channel=ProductListingChannel.PREORDER, quantity=1
        )
        resp = self.client.get(reverse('administration:staff_preorder'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Price ₾')

    def test_default_sort_qty_desc_and_header_toggles_to_asc(self):
        other = Product.objects.create(
            type=ProductType.BALLS,
            name='Alpha Balls',
            initial_price=Decimal('20.00'),
            landed_cost_gel=Decimal('10.00'),
            in_stock=True,
            is_active=True,
        )
        ProductListing.objects.create(
            product=other, channel=ProductListingChannel.STOCK, quantity=1
        )
        url = reverse('administration:staff_stock')
        default = self.client.get(url)
        self.assertEqual(default.context['sort'], 'qty')
        self.assertEqual(default.context['direction'], 'desc')
        self.assertEqual(
            [row.quantity for row in default.context['listings']],
            [3, 1],
        )
        self.assertIn('dir=asc', default.context['sort_hrefs']['qty'])

        asc = self.client.get(url, {'sort': 'qty', 'dir': 'asc'})
        self.assertEqual(
            [row.quantity for row in asc.context['listings']],
            [1, 3],
        )
        self.assertIn('dir=desc', asc.context['sort_hrefs']['qty'])

    def test_first_click_on_product_sorts_desc(self):
        other = Product.objects.create(
            type=ProductType.BALLS,
            name='Alpha Balls',
            initial_price=Decimal('20.00'),
            landed_cost_gel=Decimal('10.00'),
            in_stock=True,
            is_active=True,
        )
        ProductListing.objects.create(
            product=other, channel=ProductListingChannel.STOCK, quantity=1
        )
        resp = self.client.get(reverse('administration:staff_stock'), {'sort': 'product'})
        names = [row.product.name for row in resp.context['listings']]
        self.assertEqual(names, ['ROI Grip', 'Alpha Balls'])
        self.assertEqual(resp.context['direction'], 'desc')


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
