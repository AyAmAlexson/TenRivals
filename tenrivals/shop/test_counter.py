from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from shop.barcodes import lookup_barcode, pairs_from_post, write_barcode_pairs
from shop.models import (
    CounterStore,
    Product,
    ProductBarcode,
    ProductListing,
    ProductListingChannel,
    ProductType,
    SalesOrder,
)
from shop.sales_order_utils import stock_listing_quantity


@override_settings(SECURE_SSL_REDIRECT=False)
class CounterSaleTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_superuser(email='counter@test.com', password='x-secret-1')
        self.client.force_login(self.staff)
        self.product = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Overgrip',
            brand='Tourna',
            initial_price=Decimal('15.00'),
            landed_cost_gel=Decimal('6.00'),
            is_active=True,
        )
        ProductListing.objects.create(
            product=self.product,
            channel=ProductListingChannel.STOCK,
            quantity=3,
        )
        ProductBarcode.objects.create(product=self.product, variant_label='', barcode='1234567890123')
        self.sized = Product.objects.create(
            type=ProductType.BALLS,
            name='Other',
            initial_price=Decimal('10.00'),
            is_active=True,
        )

    def _open_counter(self):
        self.client.post(reverse('administration:staff_counter'), {'store_name': 'Saburtalo'})

    def test_barcode_is_per_variant_and_unique(self):
        pairs = pairs_from_post({'bc-0': '111', 'bc-1': '222'}, [('L2', 1), ('L3', 0)])
        write_barcode_pairs(self.sized, pairs)
        found = lookup_barcode(' 222 ')
        self.assertEqual(found.product_id, self.sized.pk)
        self.assertEqual(found.variant_label, 'L3')
        self.assertIsNone(lookup_barcode('999'))

    def test_scan_checkout_pay_and_cancel(self):
        self._open_counter()
        sale = reverse('administration:staff_counter_sale')
        added = self.client.post(sale, {'action': 'add_barcode', 'barcode': '1234567890123'})
        self.assertEqual(added.status_code, 302)
        checked = self.client.post(sale, {'action': 'checkout'})
        order = SalesOrder.objects.get()
        self.assertEqual(checked.status_code, 302)
        self.assertIn(f'/review/', checked['Location'])
        self.assertEqual(order.status, SalesOrder.Status.AWAITING_PAYMENT)
        self.assertIn('Saburtalo', order.notes)
        self.assertEqual(order.customer.email, 'walk-in@counter.tenrivals')
        self.assertEqual(stock_listing_quantity(self.product.pk), 2)

        review = reverse('administration:staff_counter_review', args=[order.pk])
        missing = self.client.post(review, {'action': 'pay', 'method': 'cash', 'fiscal_receipt': ''})
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.AWAITING_PAYMENT)
        self.assertEqual(missing.status_code, 302)

        paid = self.client.post(
            review,
            {'action': 'pay', 'method': 'card', 'fiscal_receipt': 'F-100'},
        )
        self.assertEqual(paid.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.COMPLETED)
        payment = order.payments.get()
        self.assertEqual(payment.fiscal_receipt, 'F-100')
        self.assertEqual(payment.payment_method, 'Card terminal')
        self.assertEqual(payment.amount_gross, Decimal('15.00'))

    def test_cancel_returns_stock(self):
        self._open_counter()
        sale = reverse('administration:staff_counter_sale')
        self.client.post(sale, {'action': 'add_product', 'product_id': self.product.pk})
        self.client.post(sale, {'action': 'set_qty', 'index': '0', 'qty': '2'})
        self.client.post(sale, {'action': 'set_price', 'index': '0', 'unit_price': '12.50'})
        self.client.post(sale, {'action': 'checkout'})
        order = SalesOrder.objects.get()
        self.assertEqual(order.gross_total, Decimal('25.00'))
        self.assertEqual(stock_listing_quantity(self.product.pk), 1)
        self.client.post(
            reverse('administration:staff_counter_review', args=[order.pk]),
            {'action': 'cancel'},
        )
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.CANCELLED)
        self.assertEqual(stock_listing_quantity(self.product.pk), 3)

    def test_split_payment_must_match_total(self):
        self._open_counter()
        sale = reverse('administration:staff_counter_sale')
        self.client.post(sale, {'action': 'add_barcode', 'barcode': '1234567890123'})
        self.client.post(sale, {'action': 'checkout'})
        order = SalesOrder.objects.get()
        review = reverse('administration:staff_counter_review', args=[order.pk])
        self.client.post(
            review,
            {
                'action': 'pay',
                'method': 'split',
                'fiscal_receipt': 'F-2',
                'cash_amount': '5.00',
                'card_amount': '5.00',
            },
        )
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.AWAITING_PAYMENT)
        self.client.post(
            review,
            {
                'action': 'pay',
                'method': 'split',
                'fiscal_receipt': 'F-2',
                'cash_amount': '5.00',
                'card_amount': '10.00',
            },
        )
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.COMPLETED)
        self.assertEqual(order.payments.count(), 2)
        self.assertEqual(CounterStore.objects.get().name, 'Saburtalo')

    def test_lookup_endpoint(self):
        self._open_counter()
        resp = self.client.get(
            reverse('administration:staff_barcode_lookup'),
            {'barcode': '1234567890123'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.assertEqual(resp.json()['product_id'], self.product.pk)
        missing = self.client.get(reverse('administration:staff_barcode_lookup'), {'barcode': 'nope'})
        self.assertFalse(missing.json()['ok'])

    def test_sale_screen_renders_product_card(self):
        self._open_counter()
        resp = self.client.get(reverse('administration:staff_counter_sale'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Overgrip')
        self.assertContains(resp, 'Scan barcode')
