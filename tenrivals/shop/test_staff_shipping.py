from datetime import date
from decimal import Decimal
from urllib.parse import quote_plus

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from shop.models import Customer, SalesOrder
from shop.staff_shipping_views import (
    OPEN_DELIVERY_STATUSES,
    contact_telegram_for_order,
    delivery_address_for_order,
    phone_tel_uri,
    telegram_profile_url,
)


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
            phone='+995 591 11 22 33',
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
        self.assertContains(resp, 'tel:+995591112233')
        self.assertContains(resp, '+995 591 11 22 33')
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

    def test_phone_tel_uri_dials_georgian_and_international_numbers(self):
        self.assertEqual(phone_tel_uri('+995 591 11 22 33'), 'tel:+995591112233')
        self.assertEqual(phone_tel_uri('591112233'), 'tel:+995591112233')
        self.assertEqual(phone_tel_uri('0591112233'), 'tel:+995591112233')
        self.assertEqual(phone_tel_uri('995591112233'), 'tel:+995591112233')
        self.assertEqual(phone_tel_uri(''), '')

    def test_telegram_profile_url_opens_a_username(self):
        self.assertEqual(telegram_profile_url('@nino_tennis'), 'https://t.me/nino_tennis')
        self.assertEqual(telegram_profile_url('nino_tennis'), 'https://t.me/nino_tennis')
        self.assertEqual(
            telegram_profile_url('https://t.me/nino_tennis'),
            'https://t.me/nino_tennis',
        )
        self.assertEqual(telegram_profile_url(''), '')
        self.assertEqual(telegram_profile_url('—'), '')
        self.assertEqual(telegram_profile_url('+995591112233'), '')

    def test_telegram_prefers_order_note_over_customer(self):
        self.customer.tg_account = 'old_handle'
        self.customer.save(update_fields=['tg_account'])
        self.open_order.notes = 'Telegram: @nino_tennis\nAddress: Tbilisi. Rustaveli 1'
        self.open_order.save(update_fields=['notes'])
        self.assertEqual(contact_telegram_for_order(self.open_order), '@nino_tennis')

    def test_telegram_falls_back_to_customer_when_note_is_empty(self):
        self.customer.tg_account = 'nino_tennis'
        self.customer.save(update_fields=['tg_account'])
        self.open_order.notes = 'Telegram: —\nAddress: Tbilisi. Rustaveli 1'
        self.open_order.save(update_fields=['notes'])
        self.assertEqual(contact_telegram_for_order(self.open_order), 'nino_tennis')

    def test_page_links_telegram_profile_when_present(self):
        self.customer.tg_account = '@nino_tennis'
        self.customer.save(update_fields=['tg_account'])
        url = reverse('administration:staff_shipping_addresses')
        resp = self.client.get(url)
        self.assertContains(resp, 'https://t.me/nino_tennis')
        self.assertContains(resp, '@nino_tennis')

    def test_page_omits_telegram_link_when_customer_has_none(self):
        url = reverse('administration:staff_shipping_addresses')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'https://t.me/')
        self.assertContains(resp, 'Nino Beridze')

    def test_page_omits_phone_link_when_customer_has_none(self):
        self.customer.phone = ''
        self.customer.save(update_fields=['phone'])
        url = reverse('administration:staff_shipping_addresses')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'tel:')
        self.assertContains(resp, 'Nino Beridze')

    def test_open_statuses_cover_in_progress_orders(self):
        self.assertIn(SalesOrder.Status.SHIPPED, OPEN_DELIVERY_STATUSES)
        self.assertIn(SalesOrder.Status.BUYING, OPEN_DELIVERY_STATUSES)
        self.assertNotIn(SalesOrder.Status.COMPLETED, OPEN_DELIVERY_STATUSES)
        self.assertNotIn(SalesOrder.Status.REFUNDED, OPEN_DELIVERY_STATUSES)
