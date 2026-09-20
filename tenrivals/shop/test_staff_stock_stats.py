from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from shop.models import (
    CourtSurface,
    Gender,
    Product,
    ProductListing,
    ProductListingChannel,
    ProductType,
    Shoe,
)
from shop.staff_stock_stats import build_stock_csv


@override_settings(SECURE_SSL_REDIRECT=False)
class StockCsvTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_superuser(
            email='stock-csv-staff@test.com',
            password='secret-secret',
        )
        self.page_url = reverse('administration:staff_stock_stats')
        self.csv_url = reverse('administration:staff_stock_csv')

    def _listing(self, product, qty):
        ProductListing.objects.create(
            product=product,
            channel=ProductListingChannel.STOCK,
            quantity=qty,
        )

    def test_csv_splits_sizes_and_skips_empty_variants(self):
        shoe = Shoe.objects.create(
            type=ProductType.MENS_SHOES,
            name='Gel Resolution 9',
            brand='Asics',
            color='White/Blue',
            sku='AS-GR9',
            initial_price=Decimal('280.00'),
            actual_price=Decimal('249.00'),
            landed_cost_gel=Decimal('140.00'),
            gender=Gender.MEN,
            surface=CourtSurface.CLAY,
            width='2E',
            sizes={'US 10': 2, 'US 11': 0, 'US 9.5': 1},
        )
        self._listing(shoe, 3)
        grip = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Grip Rx',
            brand='Tourna',
            initial_price=Decimal('40.00'),
            landed_cost_gel=Decimal('18.50'),
        )
        self._listing(grip, 4)
        empty = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Not on shelf',
            initial_price=Decimal('10.00'),
        )
        self._listing(empty, 0)

        filename, body = build_stock_csv(today=date(2026, 9, 20))
        self.assertEqual(filename, 'tenrivals-stock-2026-09-20.csv')
        self.assertIn('category,brand,name,variant,color', body)
        self.assertIn('Gel Resolution 9', body)
        self.assertIn('US 10', body)
        self.assertIn('US 9.5', body)
        self.assertNotIn('US 11', body)
        self.assertNotIn('Not on shelf', body)
        self.assertIn('Grip Rx', body)
        self.assertIn('140.00', body)
        self.assertIn('280.00', body)
        self.assertIn('249.00', body)
        self.assertIn("Men's Shoes", body)
        self.assertIn('Clay', body)

    def test_staff_page_shows_csv_button_and_download_works(self):
        product = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Overgrip',
            brand='Wilson',
            initial_price=Decimal('12.00'),
        )
        self._listing(product, 6)
        self.client.force_login(self.staff)
        page = self.client.get(self.page_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Export for AI')
        self.assertContains(page, 'Export all stock CSV')
        self.assertContains(page, self.csv_url)
        response = self.client.get(self.csv_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('tenrivals-stock-', response['Content-Disposition'])
        body = response.content.decode('utf-8-sig')
        self.assertIn('Overgrip', body)
        self.assertIn('Wilson', body)

    def test_anonymous_csv_redirected(self):
        response = self.client.get(self.csv_url)
        self.assertEqual(response.status_code, 302)
