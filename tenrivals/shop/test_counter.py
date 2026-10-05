from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from shop.barcodes import lookup_barcode, pairs_from_post, write_barcode_pairs
from shop.models import (
    CounterStore,
    CourtSurface,
    Gender,
    Product,
    ProductBarcode,
    ProductListing,
    ProductListingChannel,
    ProductType,
    SalesOrder,
    Shoe,
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

    def _open_counter(self, name='HQ Warehouse'):
        store = CounterStore.objects.get(name=name)
        self.client.post(reverse('administration:staff_counter'), {'store_id': store.pk})

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
        self.assertIn('HQ Warehouse', order.notes)
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

    def test_shop_is_a_button_and_managed_in_admin(self):
        home = self.client.get(reverse('administration:staff_counter'))
        self.assertContains(home, 'HQ Warehouse')
        self.assertContains(home, 'City Sport Store')
        self.assertNotContains(home, 'name="store_name"')
        typed = self.client.post(reverse('administration:staff_counter'), {'store_name': 'Saburtalo'})
        self.assertEqual(typed.status_code, 200)
        self.assertFalse(CounterStore.objects.filter(name='Saburtalo').exists())

        hidden = CounterStore.objects.get(name='City Sport Store')
        hidden.is_active = False
        hidden.save(update_fields=['is_active'])
        refused = self.client.post(reverse('administration:staff_counter'), {'store_id': hidden.pk})
        self.assertEqual(refused.status_code, 200)
        self.assertNotContains(refused, 'name="store_id" value="{}"'.format(hidden.pk))

        page = self.client.get(reverse('administration:staff_counter_stores'))
        self.assertContains(page, 'HQ Warehouse')
        added = self.client.post(
            reverse('administration:staff_counter_stores'),
            {'action': 'create', 'name': 'Pop-up'},
        )
        self.assertEqual(added.status_code, 302)
        self.assertTrue(CounterStore.objects.filter(name='Pop-up', is_active=True).exists())

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
        self.assertContains(resp, '1234567890123')
        self.assertContains(resp, '3 in stock')
        self.assertContains(resp, 'object-fit:contain')
        self.assertContains(resp, 'width:140px')

    def test_category_filters_brand_and_size(self):
        self._open_counter()
        shoe = Shoe.objects.create(
            type=ProductType.MENS_SHOES,
            name='Gel Resolution',
            brand='Asics',
            initial_price=Decimal('200.00'),
            gender=Gender.MEN,
            surface=CourtSurface.CLAY,
            sizes={'US 10': 2, 'US 9.5': 1, 'US 11': 0},
        )
        ProductListing.objects.create(product=shoe, channel=ProductListingChannel.STOCK, quantity=3)
        ProductBarcode.objects.create(product=shoe, variant_label='US 10', barcode='SHOE10')
        sale = reverse('administration:staff_counter_sale')
        section = self.client.get(sale, {'type': ProductType.MENS_SHOES})
        self.assertContains(section, 'ctr-brands')
        self.assertContains(section, 'Asics')
        self.assertContains(section, 'ctr-size-filters')
        self.assertContains(section, 'US 10')
        self.assertNotContains(section, 'US 11')
        sized = self.client.get(sale, {'type': ProductType.MENS_SHOES, 'brand': 'Asics', 'size': 'US 10'})
        self.assertContains(sized, 'Gel Resolution')
        self.assertContains(sized, 'SHOE10')
        self.assertContains(sized, '2 in stock')
        self.assertNotContains(sized, 'Overgrip')
        picked = self.client.get(sale, {'type': ProductType.MENS_SHOES, 'pick': shoe.pk})
        self.assertContains(picked, 'class="ctr-sizes"')
        self.assertContains(picked, 'US 9.5')
