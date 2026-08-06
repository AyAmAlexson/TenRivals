from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from persons.account_display import account_initials_for_user
from persons.forms import AccountUpdateForm

from shop.catalog_utils import (
    annotate_preorder_listing_quantity,
    stock_catalog_in_stock_queryset,
    stock_catalog_storefront_queryset,
)
from shop.models import (
    Customer,
    Product,
    ProductListing,
    ProductListingChannel,
    ProductType,
    SalesOrder,
)
from shop.attribution import (
    DEFAULT_ORGANIC_SOURCE,
    SESSION_KEY,
    apply_acquisition_source_to_customer,
    format_attribution,
    resolve_acquisition_source,
)
from shop.promo_codes import PromoEvaluation
from shop.sales_order_currency import apply_payment_currency_fields
from shop.staff_sales_forms import CustomerForm


class RetailCustomerGuestLinkTests(TestCase):
    def test_registration_links_existing_guest_customer_by_email(self):
        guest = Customer.objects.create(
            first_name='Guest',
            last_name='Buyer',
            email='merge-test@example.com',
            phone='555',
        )
        self.assertIsNone(guest.user_id)
        User = get_user_model()
        User.objects.create_user(
            email='merge-test@example.com',
            password='secret-secret',
            first_name='Reg',
            last_name='User',
        )
        guest.refresh_from_db()
        self.assertIsNotNone(guest.user_id)
        self.assertEqual(
            Customer.objects.filter(email__iexact='merge-test@example.com').count(),
            1,
        )

    def test_registration_creates_customer_when_no_guest_row(self):
        User = get_user_model()
        User.objects.create_user(
            email='fresh-customer@example.com',
            password='secret-secret',
            first_name='Only',
            last_name='New',
        )
        c = Customer.objects.get(user__email='fresh-customer@example.com')
        self.assertEqual(c.first_name, 'Only')
        self.assertEqual(Customer.objects.filter(email__iexact='fresh-customer@example.com').count(), 1)


@override_settings(SECURE_SSL_REDIRECT=False)
class CustomerEditPersistTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_superuser(
            email='staff-cust@test.com',
            password='secret-secret',
        )
        self.client.force_login(self.staff)

    def test_staff_customer_edit_persists_fields(self):
        customer = Customer.objects.create(
            first_name='Old',
            last_name='Name',
            phone='111',
            email='old-cust@test.com',
            address='A',
        )
        url = reverse('administration:staff_customer_edit', kwargs={'pk': customer.pk})
        response = self.client.post(
            url,
            {
                'first_name': 'New',
                'last_name': 'Person',
                'name_local': 'ახალი',
                'surname_local': '',
                'phone': '222',
                'email': 'new-cust@test.com',
                'tg_account': '@handle',
                'newsletter_opt_in': 'on',
                'address': 'Tbilisi',
                'source': 'Instagram / social',
                'comment': 'VIP walk-in',
            },
        )
        self.assertEqual(response.status_code, 302)
        customer.refresh_from_db()
        self.assertEqual(customer.first_name, 'New')
        self.assertEqual(customer.last_name, 'Person')
        self.assertEqual(customer.name_local, 'ახალი')
        self.assertEqual(customer.phone, '222')
        self.assertEqual(customer.email, 'new-cust@test.com')
        self.assertEqual(customer.tg_account, 'handle')
        self.assertEqual(customer.address, 'Tbilisi')
        self.assertEqual(customer.source, 'Instagram / social')
        self.assertEqual(customer.comment, 'VIP walk-in')
        self.assertTrue(customer.newsletter_opt_in)

    def test_customer_form_syncs_linked_user(self):
        User = get_user_model()
        user = User.objects.create_user(
            email='linked-cust@test.com',
            password='secret-secret',
            first_name='UserFn',
            last_name='UserLn',
            mobile='111',
        )
        customer = Customer.objects.get(user=user)
        form = CustomerForm(
            {
                'first_name': 'CustFn',
                'last_name': 'CustLn',
                'name_local': '',
                'surname_local': '',
                'phone': '555',
                'email': 'linked-cust@test.com',
                'tg_account': '@newtg',
                'newsletter_opt_in': True,
                'address': 'Addr',
                'source': '',
                'comment': '',
            },
            instance=customer,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'CustFn')
        self.assertEqual(user.last_name, 'CustLn')
        self.assertEqual(user.mobile, '555')
        self.assertEqual(user.telegram, 'newtg')

    def test_account_update_syncs_linked_customer(self):
        User = get_user_model()
        user = User.objects.create_user(
            email='acc-sync@test.com',
            password='secret-secret',
            first_name='Old',
            last_name='Name',
            mobile='111',
        )
        customer = Customer.objects.get(user=user)
        form = AccountUpdateForm(
            {
                'first_name': 'AccFn',
                'last_name': 'AccLn',
                'mobile': '777',
                'telegram': 'acctg',
            },
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        customer.refresh_from_db()
        self.assertEqual(customer.first_name, 'AccFn')
        self.assertEqual(customer.last_name, 'AccLn')
        self.assertEqual(customer.phone, '777')
        self.assertEqual(customer.tg_account, 'acctg')


@override_settings(SECURE_SSL_REDIRECT=False)
@override_settings(SECURE_SSL_REDIRECT=False)
class CustomerDuplicateAndDeleteTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_superuser(
            email='staff-dup@test.com',
            password='secret-secret',
        )
        self.client.force_login(self.staff)

    def test_staff_create_rejects_duplicate_email(self):
        Customer.objects.create(
            first_name='Existing',
            last_name='One',
            email='dup@test.com',
        )
        form = CustomerForm(
            {
                'first_name': 'New',
                'last_name': 'Two',
                'name_local': '',
                'surname_local': '',
                'phone': '',
                'email': 'dup@test.com',
                'tg_account': '',
                'address': '',
                'source': '',
                'comment': '',
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_delete_customer_keeps_linked_user(self):
        User = get_user_model()
        user = User.objects.create_user(
            email='keep-user@test.com',
            password='secret-secret',
            first_name='Keep',
            last_name='Me',
        )
        customer = Customer.objects.get(user=user)
        url = reverse('administration:staff_customer_delete', kwargs={'pk': customer.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Customer.objects.filter(pk=customer.pk).exists())
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        user.refresh_from_db()
        self.assertEqual(user.email, 'keep-user@test.com')


@override_settings(SECURE_SSL_REDIRECT=False)
class CustomerAttributionTests(TestCase):
    def test_format_utm_and_organic_default(self):
        self.assertEqual(
            format_attribution(
                {
                    'utm_source': 'instagram',
                    'utm_medium': 'social',
                    'utm_campaign': 'spring',
                }
            ),
            'instagram / social / spring',
        )
        self.assertEqual(format_attribution({'gclid': 'abc'}), 'Google Ads (gclid)')
        self.assertEqual(resolve_acquisition_source(None), DEFAULT_ORGANIC_SOURCE)

    def test_middleware_stores_first_touch_utm(self):
        response = self.client.get('/accounts/login/?utm_source=ig&utm_medium=bio')
        self.assertIn(response.status_code, (200, 302))
        session = self.client.session
        self.assertEqual(
            session.get(SESSION_KEY),
            {'utm_source': 'ig', 'utm_medium': 'bio'},
        )
        # Second touch must not overwrite first-touch attribution.
        self.client.get('/accounts/login/?utm_source=google&utm_medium=cpc')
        self.assertEqual(
            self.client.session.get(SESSION_KEY),
            {'utm_source': 'ig', 'utm_medium': 'bio'},
        )

    def test_apply_source_only_if_empty(self):
        customer = Customer.objects.create(first_name='A', email='src@test.com')
        request = SimpleNamespace(session={SESSION_KEY: {'utm_source': 'tiktok'}})
        self.assertTrue(apply_acquisition_source_to_customer(customer, request))
        customer.refresh_from_db()
        self.assertEqual(customer.source, 'tiktok')
        request2 = SimpleNamespace(session={SESSION_KEY: {'utm_source': 'other'}})
        self.assertFalse(
            apply_acquisition_source_to_customer(customer, request2, only_if_empty=True)
        )
        customer.refresh_from_db()
        self.assertEqual(customer.source, 'tiktok')


class PromoEvaluationPropertyTests(TestCase):
    def test_promo_id_none_when_no_promo(self):
        ev = PromoEvaluation(True, Decimal('1.00'), '', None, Decimal('10.00'), [])
        self.assertIsNone(ev.promo_id)

    def test_promo_id_from_promo_instance(self):
        promo = MagicMock()
        promo.pk = 42
        ev = PromoEvaluation(True, Decimal('2.00'), '', promo, Decimal('10.00'), [0])
        self.assertEqual(ev.promo_id, 42)


class AccountInitialsTests(TestCase):
    def test_initials_from_email_two_segments(self):
        u = SimpleNamespace(first_name='', last_name='', email='andy.rivals@tenrivals.com')
        self.assertEqual(account_initials_for_user(u), 'AR')

    def test_initials_from_email_no_dot(self):
        u = SimpleNamespace(first_name='', last_name='', email='solo@example.com')
        self.assertEqual(account_initials_for_user(u), 'S')

    def test_initials_from_names(self):
        u = SimpleNamespace(first_name='Ann', last_name='B', email='x@y.com')
        self.assertEqual(account_initials_for_user(u), 'AB')


class AnnotatePreorderListingQuantityTests(TestCase):
    def test_preorder_quantity_zero_annotates_zero(self):
        p = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Vitrine preorder',
            initial_price=Decimal('10.00'),
        )
        ProductListing.objects.create(
            product=p,
            channel=ProductListingChannel.PREORDER,
            quantity=0,
        )
        row = annotate_preorder_listing_quantity(Product.objects.filter(pk=p.pk)).first()
        self.assertEqual(row.preorder_listing_qty, 0)

    def test_preorder_quantity_positive(self):
        p = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Preorder slots',
            initial_price=Decimal('10.00'),
        )
        ProductListing.objects.create(
            product=p,
            channel=ProductListingChannel.PREORDER,
            quantity=4,
        )
        row = annotate_preorder_listing_quantity(Product.objects.filter(pk=p.pk)).first()
        self.assertEqual(row.preorder_listing_qty, 4)


class StockCatalogInStockQuerysetTests(TestCase):
    def test_strict_in_stock_excludes_zero_unless_storefront(self):
        zero = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Zero shelf',
            initial_price=Decimal('10.00'),
            in_stock=False,
        )
        ProductListing.objects.create(
            product=zero,
            channel=ProductListingChannel.STOCK,
            quantity=0,
        )
        vitrine = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Vitrine',
            initial_price=Decimal('9.00'),
            in_stock=True,
        )
        ProductListing.objects.create(
            product=vitrine,
            channel=ProductListingChannel.STOCK,
            quantity=0,
        )
        positive = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='On shelf',
            initial_price=Decimal('11.00'),
        )
        ProductListing.objects.create(
            product=positive,
            channel=ProductListingChannel.STOCK,
            quantity=3,
        )
        strict = set(stock_catalog_in_stock_queryset().values_list('pk', flat=True))
        self.assertNotIn(zero.pk, strict)
        self.assertNotIn(vitrine.pk, strict)
        self.assertIn(positive.pk, strict)

        storefront = set(stock_catalog_storefront_queryset().values_list('pk', flat=True))
        self.assertNotIn(zero.pk, storefront)
        self.assertIn(vitrine.pk, storefront)
        self.assertIn(positive.pk, storefront)


class ProductListingInStockSyncTests(TestCase):
    def test_stock_quantity_zero_does_not_clear_in_stock_checkbox(self):
        p = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Test item',
            initial_price=Decimal('10.00'),
            in_stock=True,
        )
        ProductListing.objects.create(
            product=p,
            channel=ProductListingChannel.STOCK,
            quantity=0,
        )
        p.refresh_from_db()
        self.assertTrue(p.in_stock)

    def test_stock_quantity_positive_sets_in_stock_true(self):
        p = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Test item 2',
            initial_price=Decimal('10.00'),
            in_stock=False,
        )
        row = ProductListing.objects.create(
            product=p,
            channel=ProductListingChannel.STOCK,
            quantity=2,
        )
        p.refresh_from_db()
        self.assertTrue(p.in_stock)
        row.quantity = 0
        row.save(update_fields=['quantity'])
        p.refresh_from_db()
        self.assertTrue(p.in_stock)

    def test_no_stock_listing_sets_in_stock_false(self):
        p = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='No listing',
            initial_price=Decimal('10.00'),
            in_stock=True,
        )
        row = ProductListing.objects.create(
            product=p,
            channel=ProductListingChannel.STOCK,
            quantity=1,
        )
        p.refresh_from_db()
        self.assertTrue(p.in_stock)
        row.delete()
        p.refresh_from_db()
        self.assertFalse(p.in_stock)


class SalesOrderPaymentCurrencyTests(TestCase):
    def test_gel_normalizes_rate_and_amount(self):
        order = SalesOrder(
            invoice_number='2026-000099',
            customer=Customer.objects.create(first_name='A', last_name='B', email='fx@test.com'),
            order_date='2026-01-01',
        )
        order.payment_currency = 'GEL'
        order.exchange_rate = Decimal('2.5')
        apply_payment_currency_fields(order, Decimal('100.00'))
        self.assertEqual(order.payment_currency, 'GEL')
        self.assertEqual(order.exchange_rate, Decimal('1'))
        self.assertEqual(order.amount_in_payment_currency, Decimal('100.00'))

    def test_usd_amount_is_gross_divided_by_rate(self):
        order = SalesOrder(
            invoice_number='2026-000098',
            customer=Customer.objects.create(first_name='C', last_name='D', email='fx2@test.com'),
            order_date='2026-01-01',
        )
        order.payment_currency = 'USD'
        order.exchange_rate = Decimal('2.70')
        apply_payment_currency_fields(order, Decimal('100.00'))
        self.assertEqual(order.amount_in_payment_currency, Decimal('37.04'))


class CatalogSeoTextsTests(TestCase):
    def _seo(self, browse_mode, type_code, **kwargs):
        from shop.seo_catalog import catalog_seo_texts

        defaults = {
            'racket_brand': 'all',
            'shoe_brand': 'all',
            'surface_active': 'all',
            'gender_filter': 'all',
            'product_count': 4,
        }
        defaults.update(kwargs)
        return catalog_seo_texts(browse_mode, type_code=type_code, **defaults)

    def test_stock_balls_title_and_h1(self):
        seo = self._seo('stock', ProductType.BALLS)
        self.assertEqual(
            seo['seo_page_title'],
            'Tennis Balls | To Buy in Tbilisi | Tennis Rivals',
        )
        self.assertEqual(
            seo['seo_page_h1'],
            'Tennis Balls • In Stock • Tbilisi, Georgia',
        )

    def test_stock_rackets_with_brand(self):
        seo = self._seo('stock', ProductType.RACKET, racket_brand='Wilson')
        self.assertEqual(
            seo['seo_page_title'],
            'Wilson Tennis Rackets | To Buy in Tbilisi | Tennis Rivals',
        )
        self.assertEqual(
            seo['seo_page_h1'],
            'Wilson Tennis Rackets • In Stock • Tbilisi, Georgia',
        )

    def test_stock_mens_shoes_clay_surface(self):
        seo = self._seo(
            'stock',
            ProductType.MENS_SHOES,
            shoe_brand='ASICS',
            surface_active='clay',
        )
        self.assertEqual(
            seo['seo_page_title'],
            "ASICS Men's Clay Court Tennis Shoes | To Buy in Tbilisi | Tennis Rivals",
        )
        self.assertEqual(
            seo['seo_page_h1'],
            "ASICS Men's Clay Court Tennis Shoes • In Stock • Tbilisi, Georgia",
        )

    def test_preorder_all_catalog(self):
        seo = self._seo('preorder', None)
        self.assertEqual(
            seo['seo_page_title'],
            'Tennis Equipment | Preorder to Georgia | Tennis Rivals',
        )
        self.assertEqual(
            seo['seo_page_h1'],
            'Tennis Equipment • Preorder • Tbilisi, Georgia',
        )
