from datetime import date
from decimal import Decimal
from urllib.parse import quote_plus

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from shop.models import Customer, SalesOrder
from shop.staff_shipping_views import OPEN_DELIVERY_STATUSES, delivery_address_for_order


@override_settings(SECURE_SSL_REDIRECT=False)
class StaffShippingAddressesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_superuser(
            email='staff-ship@test.com',
            password='secret-secret',
        )
        self.client.force_login(self.staff)
        self.customer = Customer.objects.create(
            first_name='Nino',
            last_name='Beridze',
            address='Old street 1',
        )
        self.open_order = SalesOrder.objects.create(
            invoice_number='2026-000711',
            customer=self.customer,
            order_date=date(2026, 10, 2),
            gross_total=Decimal('120.00'),
            vat_total=Decimal('18.31'),
            net_total=Decimal('101.69'),
            status=SalesOrder.Status.CONFIRMED,
            notes='Checkout source: storefront (guest).\nAddress: Tbilisi. Rustaveli 1\nComment: gate',
        )
        self.completed = SalesOrder.objects.create(
            invoice_number='2026-000710',
            customer=self.customer,
            order_date=date(2026, 10, 1),
            gross_total=Decimal('40.00'),
            vat_total=Decimal('6.10'),
            net_total=Decimal('33.90'),
            status=SalesOrder.Status.COMPLETED,
            notes='Address: Tbilisi. Done street',
        )
        self.cancelled = SalesOrder.objects.create(
            invoice_number='2026-000709',
            customer=self.customer,
            order_date=date(2026, 9, 30),
            gross_total=Decimal('15.00'),
            vat_total=Decimal('2.29'),
            net_total=Decimal('12.71'),
            status=SalesOrder.Status.CANCELLED,
            notes='Address: Tbilisi. Cancelled street',
        )

    def test_address_prefers_order_note_over_customer(self):
        self.assertEqual(
            delivery_address_for_order(self.open_order),
            'Tbilisi. Rustaveli 1',
        )

    def test_address_falls_back_to_customer(self):
        self.open_order.notes = ''
        self.open_order.save(update_fields=['notes'])
        self.assertEqual(delivery_address_for_order(self.open_order), 'Old street 1')

    def test_page_lists_open_orders_with_map_links(self):
        url = reverse('administration:staff_shipping_addresses')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Current addresses')
        self.assertContains(resp, '2026-000711')
        self.assertContains(resp, 'Nino Beridze')
        self.assertContains(resp, '120.00')
        self.assertContains(resp, 'Tbilisi. Rustaveli 1')
        query = quote_plus('Tbilisi. Rustaveli 1')
        self.assertContains(
            resp,
            f'https://www.google.com/maps/search/?api=1&amp;query={query}',
        )
        self.assertContains(resp, f'https://yandex.com/maps/?text={query}')
        self.assertNotContains(resp, '2026-000710')
        self.assertNotContains(resp, '2026-000709')
        self.assertNotContains(resp, '—')

    def test_anonymous_cannot_open_shipping(self):
        self.client.logout()
        url = reverse('administration:staff_shipping_addresses')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_confirm_delivery_marks_completed_and_returns_to_the_list(self):
        url = reverse(
            'administration:staff_shipping_confirm_delivery',
            kwargs={'pk': self.open_order.pk},
        )
        resp = self.client.post(url, follow=True)
        self.assertRedirects(resp, reverse('administration:staff_shipping_addresses'))
        self.open_order.refresh_from_db()
        self.assertEqual(self.open_order.status, SalesOrder.Status.COMPLETED)
        self.assertContains(resp, 'marked completed')
        self.assertNotContains(resp, 'data-invoice="2026-000711"')

    def test_confirm_delivery_rejects_cancelled_order(self):
        url = reverse(
            'administration:staff_shipping_confirm_delivery',
            kwargs={'pk': self.cancelled.pk},
        )
        resp = self.client.post(url, follow=True)
        self.cancelled.refresh_from_db()
        self.assertEqual(self.cancelled.status, SalesOrder.Status.CANCELLED)
        self.assertContains(resp, 'not waiting for delivery')

    def test_open_statuses_cover_in_progress_orders(self):
        self.assertIn(SalesOrder.Status.SHIPPED, OPEN_DELIVERY_STATUSES)
        self.assertNotIn(SalesOrder.Status.COMPLETED, OPEN_DELIVERY_STATUSES)
        self.assertNotIn(SalesOrder.Status.REFUNDED, OPEN_DELIVERY_STATUSES)
