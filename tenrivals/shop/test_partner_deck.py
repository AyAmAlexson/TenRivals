from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from shop.models import Customer, Product, ProductListing, ProductListingChannel, ProductType, SalesOrder
from shop.partner_deck import partner_public_facts


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class PartnerDeckTests(TestCase):
    def setUp(self):
        cache.clear()
        self.customer = Customer.objects.create(
            first_name='Secret',
            last_name='Partnername',
            email='secret-partner@test.com',
        )
        self.order = SalesOrder.objects.create(
            invoice_number='2026-000811',
            customer=self.customer,
            order_date=date(2026, 6, 2),
            gross_total=Decimal('200.00'),
            vat_total=Decimal('30.51'),
            net_total=Decimal('169.49'),
            status=SalesOrder.Status.COMPLETED,
        )
        SalesOrder.objects.create(
            invoice_number='2026-000812',
            customer=self.customer,
            order_date=date(2026, 9, 2),
            gross_total=Decimal('260.00'),
            vat_total=Decimal('39.66'),
            net_total=Decimal('220.34'),
            status=SalesOrder.Status.COMPLETED,
        )
        cancelled_customer = Customer.objects.create(
            first_name='Hidden',
            last_name='Cancelled',
            email='hidden-cancelled@test.com',
        )
        SalesOrder.objects.create(
            invoice_number='2026-000810',
            customer=cancelled_customer,
            order_date=date(2026, 8, 2),
            gross_total=Decimal('80.00'),
            vat_total=Decimal('12.20'),
            net_total=Decimal('67.80'),
            status=SalesOrder.Status.CANCELLED,
        )
        product = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Partner Grip',
            brand='Tourna',
            initial_price=Decimal('10.00'),
            actual_price=Decimal('10.00'),
            in_stock=True,
            is_active=True,
        )
        ProductListing.objects.create(
            product=product,
            channel=ProductListingChannel.STOCK,
            quantity=4,
        )

    def test_public_page_hides_money_and_names(self):
        url = reverse('shop:partners', kwargs={'site_locale': 'ge_en'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Official for Tourna and Diadem')
        self.assertContains(resp, 'For partners')
        self.assertContains(resp, 'Open to collaboration')
        self.assertContains(resp, '<svg')
        self.assertNotContains(resp, 'Secret')
        self.assertNotContains(resp, 'Partnername')
        self.assertNotContains(resp, 'Hidden')
        self.assertNotContains(resp, '₾')
        self.assertNotContains(resp, '200.00')
        self.assertNotContains(resp, '260.00')
        self.assertNotContains(resp, '—')

    def test_facts_count_clients_stock_and_skip_cancelled(self):
        facts = partner_public_facts()
        self.assertEqual(facts['clients'], 1)
        self.assertEqual(facts['orders'], 2)
        self.assertEqual(facts['repeat_clients'], 1)
        self.assertEqual(facts['units_in_stock'], 4)
        self.assertEqual(facts['skus'], 1)
        self.assertGreaterEqual(facts['brands'], 1)
        self.assertTrue(facts['revenue_points'])
        self.assertNotIn('revenue', facts)
        self.assertIsInstance(facts['growth_pct'], int)

    def test_footer_links_to_the_deck(self):
        url = reverse('shop:brands', kwargs={'site_locale': 'ge_en'})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse('shop:partners', kwargs={'site_locale': 'ge_en'}))

    def test_anonymous_user_is_enough(self):
        User = get_user_model()
        self.assertFalse(User.objects.filter(is_superuser=True).exists())
        url = reverse('shop:partners', kwargs={'site_locale': 'ge_en'})
        self.assertEqual(self.client.get(url).status_code, 200)
