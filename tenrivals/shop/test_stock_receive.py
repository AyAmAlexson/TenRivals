from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from shop.models import Product, ProductListing, ProductListingChannel, ProductType, StockReceipt


@override_settings(SECURE_SSL_REDIRECT=False)
class StockReceiveIdempotencyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_superuser(
            email='receive-staff@test.com',
            password='secret-secret',
        )
        self.product = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Grip Rx Instant Grip Enhancer',
            brand='Tourna',
            initial_price=Decimal('40.00'),
        )
        ProductListing.objects.create(
            product=self.product,
            channel=ProductListingChannel.STOCK,
            quantity=0,
        )
        self.url = reverse('administration:staff_stock_receive')
        self.client.force_login(self.staff)

    def _payload(self, token: str) -> dict:
        return {
            'receive_token': token,
            'note': 'B61 MIDWEST RAQUET SPORTS',
            'lines-TOTAL_FORMS': '1',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '2',
            'lines-0-unit_cost': '30.17',
            'lines-0-on_hand': '',
            'lines-0-add_to_stock': '1',
            'lines-0-variant': '',
        }

    def test_double_submit_records_batch_once(self):
        token = 'a' * 32
        first = self.client.post(self.url, self._payload(token))
        self.assertEqual(first.status_code, 302)
        second = self.client.post(self.url, self._payload(token))
        self.assertEqual(second.status_code, 302)
        self.assertEqual(StockReceipt.objects.filter(product=self.product).count(), 1)
        listing = ProductListing.objects.get(
            product=self.product, channel=ProductListingChannel.STOCK
        )
        self.assertEqual(listing.quantity, 2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.landed_cost_gel, Decimal('30.17'))
