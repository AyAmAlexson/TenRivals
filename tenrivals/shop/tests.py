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
class CustomerMergeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_superuser(
            email='staff-merge@test.com',
            password='secret-secret',
        )
        self.client.force_login(self.staff)

    def test_merge_moves_orders_and_fills_empty_fields(self):
        from shop.customer_merge import merge_customers

        survivor = Customer.objects.create(
            first_name='Keep',
            last_name='Me',
            email='survivor@test.com',
            phone='',
        )
        donor = Customer.objects.create(
            first_name='Guest',
            last_name='Buyer',
            email='donor@test.com',
            phone='555123',
            address='Tbilisi',
        )
        order = SalesOrder.objects.create(
            invoice_number='2026-000201',
            customer=donor,
            order_date='2026-08-01',
            status=SalesOrder.Status.COMPLETED,
        )
        merged = merge_customers(survivor, donor, {})
        order.refresh_from_db()
        self.assertEqual(order.customer_id, merged.pk)
        self.assertFalse(Customer.objects.filter(pk=donor.pk).exists())
        self.assertEqual(merged.phone, '555123')
        self.assertEqual(merged.address, 'Tbilisi')
        self.assertEqual(merged.email, 'survivor@test.com')
        self.assertIn(f'Merged from customer #{donor.pk}', merged.comment)
        self.assertIn('Merge alternative', merged.comment)  # email conflict → alternative

    def test_merge_conflict_records_alternative_in_notes(self):
        from shop.customer_merge import FieldResolution, find_field_conflicts, merge_customers

        survivor = Customer.objects.create(
            first_name='Anna',
            last_name='One',
            email='anna@test.com',
            phone='111',
        )
        donor = Customer.objects.create(
            first_name='Anna',
            last_name='Two',
            email='anna-guest@test.com',
            phone='222',
        )
        conflicts = find_field_conflicts(survivor, donor)
        fields = {c.field for c in conflicts}
        self.assertIn('email', fields)
        self.assertIn('phone', fields)
        self.assertIn('last_name', fields)

        merged = merge_customers(
            survivor,
            donor,
            {
                'email': FieldResolution(choice='donor'),
                'phone': FieldResolution(choice='custom', custom_value='999'),
                'last_name': FieldResolution(choice='survivor'),
            },
        )
        self.assertEqual(merged.email, 'anna-guest@test.com')
        self.assertEqual(merged.phone, '999')
        self.assertEqual(merged.last_name, 'One')
        self.assertIn('Merge alternative email anna@test.com', merged.comment)
        self.assertIn('Merge alternative phone 111', merged.comment)
        self.assertIn('Merge alternative phone 222', merged.comment)
        self.assertIn('Merge alternative last_name Two', merged.comment)

    def test_staff_merge_view_post(self):
        survivor = Customer.objects.create(
            first_name='S',
            last_name='V',
            email='s-view@test.com',
        )
        donor = Customer.objects.create(
            first_name='D',
            last_name='N',
            email='d-view@test.com',
            phone='777',
        )
        SalesOrder.objects.create(
            invoice_number='2026-000202',
            customer=donor,
            order_date='2026-08-02',
            status=SalesOrder.Status.COMPLETED,
        )
        url = reverse('administration:staff_customer_merge')
        response = self.client.post(
            url,
            {
                'action': 'merge',
                'survivor_id': survivor.pk,
                'donor_id': donor.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        survivor.refresh_from_db()
        self.assertFalse(Customer.objects.filter(pk=donor.pk).exists())
        self.assertEqual(survivor.sales_orders.count(), 1)
        self.assertEqual(survivor.phone, '777')


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


class LegacySalesImportHelpersTests(TestCase):
    def test_guess_product_type_samples(self):
        from shop.management.commands.import_legacy_sales_orders import (
            guess_product_type,
            parse_discount_percent,
        )

        self.assertEqual(guess_product_type('Yonex Super Grap Overgrip White'), ProductType.GRIPS)
        self.assertEqual(guess_product_type('Wilson Championship Tennis 4 Ball Can'), ProductType.BALLS)
        self.assertEqual(
            guess_product_type("Asics Gel Dedicate 8 AC White/Indigo Women's Shoes US 10.0"),
            ProductType.WOMENS_SHOES,
        )
        self.assertEqual(
            guess_product_type('Wilson Pro Staff 97 v14 Racquet 4 3/8" (#3)'),
            ProductType.RACKET,
        )
        self.assertEqual(
            guess_product_type('Wilson Blade V8/Clash V2 Butt Cap (2)'),
            ProductType.ACCESSORIES,
        )
        self.assertEqual(parse_discount_percent(0.1), Decimal('10.00'))
        self.assertEqual(parse_discount_percent(1), Decimal('100.00'))
        self.assertEqual(parse_discount_percent(0.1429), Decimal('14.29'))

    def test_max_issued_ignores_special_series(self):
        from shop.sales_order_utils import max_issued_invoice_seq_for_year

        cust = Customer.objects.create(first_name='A', last_name='B')
        SalesOrder.objects.create(
            invoice_number='2026-000050',
            customer=cust,
            order_date='2026-03-01',
            status=SalesOrder.Status.COMPLETED,
        )
        SalesOrder.objects.create(
            invoice_number='2026-100007',
            customer=cust,
            order_date='2026-02-10',
            status=SalesOrder.Status.COMPLETED,
        )
        self.assertEqual(max_issued_invoice_seq_for_year(2026), 50)

    def test_allocate_skips_existing_numbers(self):
        from shop.models import SalesInvoiceYearSequence
        from shop.sales_order_utils import allocate_invoice_number

        SalesInvoiceYearSequence.objects.update_or_create(
            year=2026, defaults={'last_seq': 40}
        )
        cust = Customer.objects.create(first_name='A', last_name='B')
        SalesOrder.objects.create(
            invoice_number='2026-000041',
            customer=cust,
            order_date='2026-03-01',
            status=SalesOrder.Status.COMPLETED,
        )
        self.assertEqual(allocate_invoice_number(2026), '2026-000042')

    def test_invoice_number_form_uniqueness(self):
        from shop.staff_sales_forms import SalesOrderForm

        cust = Customer.objects.create(first_name='A', last_name='B')
        existing = SalesOrder.objects.create(
            invoice_number='2026-000060',
            customer=cust,
            order_date='2026-03-01',
            status=SalesOrder.Status.COMPLETED,
        )
        form = SalesOrderForm(
            data={
                'invoice_number': '2026-000060',
                'customer': cust.pk,
                'order_date': '2026-03-02',
                'status': SalesOrder.Status.COMPLETED,
                'delivery_gross': '0',
                'delivery_cost_gel': '0',
                'payment_currency': 'GEL',
                'exchange_rate': '1',
                'notes': '',
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn('invoice_number', form.errors)

        form_ok = SalesOrderForm(
            instance=existing,
            data={
                'invoice_number': '2026-000061',
                'customer': cust.pk,
                'order_date': '2026-03-01',
                'status': SalesOrder.Status.COMPLETED,
                'delivery_gross': '0',
                'delivery_cost_gel': '0',
                'payment_currency': 'GEL',
                'exchange_rate': '1',
                'notes': '',
            },
        )
        self.assertTrue(form_ok.is_valid(), form_ok.errors)
        self.assertEqual(form_ok.cleaned_data['invoice_number'], '2026-000061')


@override_settings(SECURE_SSL_REDIRECT=False)
class StorefrontCheckoutTests(TestCase):
    def setUp(self):
        from shop.cart_session import SESSION_CART_KEY, get_cart, try_add_to_cart
        from shop.site_locale import shop_reverse

        self.shop_reverse = shop_reverse
        self.try_add_to_cart = try_add_to_cart
        self.SESSION_CART_KEY = SESSION_CART_KEY
        self.get_cart = get_cart
        self.product = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Checkout Grip',
            initial_price=Decimal('40.00'),
            actual_price=Decimal('35.00'),
            landed_cost_gel=Decimal('12.50'),
            in_stock=True,
            is_active=True,
        )
        ProductListing.objects.create(
            product=self.product,
            channel=ProductListingChannel.STOCK,
            quantity=5,
        )
        self.checkout_url = shop_reverse('shop:checkout', site_locale='ge_en')

    def _add_to_cart(self):
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        rf = RequestFactory()
        req = rf.get('/')
        req.user = AnonymousUser()
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(req)
        req.session.save()
        ok, msg, _ = self.try_add_to_cart(
            req, product_id=self.product.pk, variant='', qty=1
        )
        self.assertTrue(ok, msg)
        cart = self.get_cart(req)
        session = self.client.session
        session[self.SESSION_CART_KEY] = cart
        session.save()

    def test_checkout_snapshots_landed_cost(self):
        self._add_to_cart()
        response = self.client.post(
            self.checkout_url + '?guest=1',
            {
                'action': 'submit_order',
                'first_name': 'Anna',
                'last_name': 'Buyer',
                'phone': '+995555000111',
                'email': 'anna-checkout@test.com',
                'tg_account': '',
                'delivery_city': 'TBILISI',
                'delivery_city_other': '',
                'delivery_address': 'Rustaveli 1',
                'payment_method': 'COD',
                'comment': '',
            },
        )
        self.assertEqual(response.status_code, 302, getattr(response, 'content', b'')[:800])
        order = SalesOrder.objects.latest('id')
        line = order.lines.get()
        self.assertEqual(line.landed_cost_gel, Decimal('12.50'))
        self.assertIn(f'/checkout/success/{order.pk}/', response['Location'])

    def test_empty_cart_after_success_redirects_to_thank_you(self):
        from datetime import date

        order = SalesOrder.objects.create(
            invoice_number='2026-009999',
            customer=Customer.objects.create(
                first_name='X', email='x-thanks@test.com'
            ),
            order_date=date(2026, 8, 1),
            gross_total=Decimal('10.00'),
            vat_total=Decimal('0.00'),
            net_total=Decimal('10.00'),
            status=SalesOrder.Status.SUBMITTED,
        )
        session = self.client.session
        session['checkout_completed_order_id'] = order.pk
        session.save()
        response = self.client.post(
            self.checkout_url + '?guest=1',
            {
                'action': 'submit_order',
                'first_name': 'X',
                'last_name': 'Y',
                'phone': '1',
                'email': 'x-thanks@test.com',
                'delivery_city': 'TBILISI',
                'delivery_address': 'A',
                'payment_method': 'COD',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/checkout/success/{order.pk}/', response['Location'])


@override_settings(SECURE_SSL_REDIRECT=False)
class SalesOrderPaymentTests(TestCase):
    """Order = sale (order_date); payments = cash received (paid_on)."""

    def setUp(self):
        from datetime import date

        from shop.models import SalesOrderPayment

        self.SalesOrderPayment = SalesOrderPayment
        User = get_user_model()
        self.staff = User.objects.create_superuser(
            email='staff-pay@test.com',
            password='secret-secret',
        )
        self.client.force_login(self.staff)
        self.cust = Customer.objects.create(first_name='Pre', last_name='Payer')
        # 670 ₾ order in September, 335 prepaid in September, 335 in October.
        self.order = SalesOrder.objects.create(
            invoice_number='2026-000900',
            customer=self.cust,
            order_date=date(2026, 9, 20),
            gross_total=Decimal('670.00'),
            vat_total=Decimal('102.20'),
            net_total=Decimal('567.80'),
            status=SalesOrder.Status.CONFIRMED,
        )
        self.p1 = SalesOrderPayment.objects.create(
            order=self.order,
            paid_on=date(2026, 9, 20),
            amount_gross=Decimal('335.00'),
            payment_method='Card',
            fiscal_receipt='R-1001',
        )

    def test_paid_total_balance_and_state(self):
        from datetime import date

        o = self.order
        self.assertEqual(o.paid_total(), Decimal('335.00'))
        self.assertEqual(o.balance_due(), Decimal('335.00'))
        self.assertEqual(o.payment_state(), SalesOrder.PaymentState.PARTIAL)
        self.SalesOrderPayment.objects.create(
            order=o,
            paid_on=date(2026, 10, 3),
            amount_gross=Decimal('335.00'),
            payment_method='Cash',
            fiscal_receipt='R-1002',
        )
        self.assertEqual(o.paid_total(), Decimal('670.00'))
        self.assertEqual(o.balance_due(), Decimal('0.00'))
        self.assertEqual(o.payment_state(), SalesOrder.PaymentState.PAID)
        # Refund → negative payment.
        self.SalesOrderPayment.objects.create(
            order=o,
            paid_on=date(2026, 10, 10),
            amount_gross=Decimal('-100.00'),
            fiscal_receipt='RF-7',
        )
        self.assertEqual(o.paid_total(), Decimal('570.00'))
        self.assertEqual(o.payment_state(), SalesOrder.PaymentState.PARTIAL)

    def test_sync_payment_summary_joins_receipts_and_methods(self):
        from datetime import date

        o = self.order
        o.payment_method = 'Bank transfer (intended)'
        o.save(update_fields=['payment_method'])
        self.SalesOrderPayment.objects.create(
            order=o,
            paid_on=date(2026, 10, 3),
            amount_gross=Decimal('335.00'),
            payment_method='Cash',
            fiscal_receipt='R-1002',
        )
        o.sync_payment_summary()
        o.refresh_from_db()
        self.assertEqual(o.fiscal_receipt, 'R-1001, R-1002')
        self.assertEqual(o.payment_method, 'Card / Cash')
        # No payments: receipts cleared, intended method kept.
        o.payments.all().delete()
        o.payment_method = 'Bank transfer (intended)'
        o.save(update_fields=['payment_method'])
        o.sync_payment_summary()
        o.refresh_from_db()
        self.assertEqual(o.fiscal_receipt, '')
        self.assertEqual(o.payment_method, 'Bank transfer (intended)')

    def test_payment_vat_split_handles_refund(self):
        p = self.SalesOrderPayment(amount_gross=Decimal('-118.00'))
        vat, net = p.vat_net_split()
        self.assertEqual(net, Decimal('-100.00'))
        self.assertEqual(vat, Decimal('-18.00'))
        self.assertTrue(p.is_refund)

    def test_fiscal_month_report_groups_by_paid_on_and_reconciles(self):
        from datetime import date

        self.SalesOrderPayment.objects.create(
            order=self.order,
            paid_on=date(2026, 10, 3),
            amount_gross=Decimal('335.00'),
            payment_method='Cash',
            fiscal_receipt='R-1002',
        )
        # Fully paid October order, so October fiscal total = 335 + 100.
        o2 = SalesOrder.objects.create(
            invoice_number='2026-000901',
            customer=self.cust,
            order_date=date(2026, 10, 5),
            gross_total=Decimal('100.00'),
            vat_total=Decimal('15.25'),
            net_total=Decimal('84.75'),
            status=SalesOrder.Status.COMPLETED,
        )
        self.SalesOrderPayment.objects.create(
            order=o2,
            paid_on=date(2026, 10, 5),
            amount_gross=Decimal('100.00'),
            fiscal_receipt='R-1003',
        )
        url = reverse('administration:staff_sales_payments_month_report')

        sep = self.client.get(url, {'year': 2026, 'month': 9})
        self.assertEqual(sep.status_code, 200)
        self.assertEqual(sep.context['total_amount'], Decimal('335.00'))
        self.assertEqual(sep.context['orders_gross'], Decimal('670.00'))
        self.assertEqual(sep.context['orders_paid_this_month'], Decimal('335.00'))
        self.assertEqual(sep.context['orders_paid_other_months'], Decimal('335.00'))
        self.assertEqual(sep.context['delta'], Decimal('-335.00'))

        octo = self.client.get(url, {'year': 2026, 'month': 10})
        self.assertEqual(octo.status_code, 200)
        self.assertEqual(octo.context['total_amount'], Decimal('435.00'))
        self.assertEqual(octo.context['orders_gross'], Decimal('100.00'))
        self.assertEqual(
            octo.context['other_month_orders_received'], Decimal('335.00')
        )
        self.assertEqual(octo.context['delta'], Decimal('335.00'))
        self.assertContains(octo, 'R-1002')
        self.assertContains(octo, 'R-1003')
        self.assertNotContains(octo, 'R-1001')

    def test_orders_month_report_excludes_cancelled_from_totals(self):
        from datetime import date

        SalesOrder.objects.create(
            invoice_number='2026-000902',
            customer=self.cust,
            order_date=date(2026, 9, 21),
            gross_total=Decimal('50.00'),
            vat_total=Decimal('7.63'),
            net_total=Decimal('42.37'),
            status=SalesOrder.Status.CANCELLED,
        )
        url = reverse('administration:staff_sales_orders_month_report')
        resp = self.client.get(url, {'year': 2026, 'month': 9})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_gross'], Decimal('670.00'))
        self.assertEqual(resp.context['excluded_count'], 1)
        self.assertEqual(resp.context['excluded_gross'], Decimal('50.00'))
        self.assertEqual(resp.context['paid_in_month'], Decimal('335.00'))
        self.assertContains(resp, '2026-000902')

    def _order_post_data(self, product, **overrides):
        data = {
            'invoice_number': '',
            'customer': self.cust.pk,
            'order_date': '2026-09-22',
            'status': SalesOrder.Status.CONFIRMED,
            'delivery_gross': '0',
            'delivery_cost_gel': '0',
            'payment_currency': 'GEL',
            'exchange_rate': '1',
            'notes': '',
            'services_json': '[]',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '1',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-from_stock': 'on',
            'lines-0-product': product.pk,
            'lines-0-custom_label': '',
            'lines-0-product_type': '',
            'lines-0-variant_label': '',
            'lines-0-quantity': '1',
            'lines-0-unit_price_gross': '670.00',
            'lines-0-discount_percent': '0',
            'lines-0-landed_cost_gel': '',
            'lines-0-sale_channel': 'PREORDER',
            'payments-TOTAL_FORMS': '1',
            'payments-INITIAL_FORMS': '0',
            'payments-MIN_NUM_FORMS': '0',
            'payments-MAX_NUM_FORMS': '1000',
            'payments-0-paid_on': '2026-09-22',
            'payments-0-amount_gross': '335.00',
            'payments-0-payment_method': 'Card',
            'payments-0-fiscal_receipt': 'R-2001',
            'payments-0-note': 'prepayment',
        }
        data.update(overrides)
        return data

    def _product(self):
        product = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Prepaid Racket Bag',
            initial_price=Decimal('670.00'),
            actual_price=Decimal('670.00'),
            in_stock=True,
            is_active=True,
        )
        ProductListing.objects.create(
            product=product,
            channel=ProductListingChannel.STOCK,
            quantity=3,
        )
        return product

    def test_staff_form_saves_partial_payment_and_summary(self):
        product = self._product()
        url = reverse('administration:staff_sales_order_new')
        resp = self.client.post(url, self._order_post_data(product))
        self.assertEqual(resp.status_code, 302, getattr(resp, 'content', b'')[:500])
        order = SalesOrder.objects.exclude(pk=self.order.pk).get(customer=self.cust)
        self.assertEqual(order.gross_total, Decimal('670.00'))
        self.assertEqual(order.payments.count(), 1)
        self.assertEqual(order.paid_total(), Decimal('335.00'))
        self.assertEqual(order.payment_state(), SalesOrder.PaymentState.PARTIAL)
        self.assertEqual(order.fiscal_receipt, 'R-2001')
        self.assertEqual(order.payment_method, 'Card')

        # Add the balance payment via edit; existing row keeps its pk.
        p = order.payments.get()
        edit_url = reverse('administration:staff_sales_order_edit', kwargs={'pk': order.pk})
        data = self._order_post_data(
            product,
            invoice_number=order.invoice_number,
            **{
                'payments-TOTAL_FORMS': '2',
                'payments-INITIAL_FORMS': '1',
                'payments-0-id': p.pk,
                'payments-0-order': order.pk,
                'payments-1-paid_on': '2026-10-03',
                'payments-1-amount_gross': '335.00',
                'payments-1-payment_method': 'Cash',
                'payments-1-fiscal_receipt': 'R-2002',
                'payments-1-note': 'balance',
            },
        )
        resp = self.client.post(edit_url, data)
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.payments.count(), 2)
        self.assertEqual(order.payment_state(), SalesOrder.PaymentState.PAID)
        self.assertEqual(order.fiscal_receipt, 'R-2001, R-2002')
        self.assertEqual(order.payment_method, 'Card / Cash')

    def test_staff_form_rejects_zero_amount_payment(self):
        product = self._product()
        url = reverse('administration:staff_sales_order_new')
        resp = self.client.post(
            url, self._order_post_data(product, **{'payments-0-amount_gross': '0'})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            SalesOrder.objects.exclude(pk=self.order.pk).filter(customer=self.cust).exists()
        )
        self.assertContains(resp, 'Amount cannot be zero')

    def test_new_order_form_prefills_one_payment_row(self):
        url = reverse('administration:staff_sales_order_new')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['payments_formset'].total_form_count(), 1)
        self.assertContains(resp, 'is-auto-amount')

    def test_invoice_shows_balance_due_when_partially_paid(self):
        url = reverse('administration:staff_sales_order_invoice', kwargs={'pk': self.order.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['show_payment_schedule'])
        self.assertContains(resp, 'Balance due')
        self.assertContains(resp, 'R-1001')

    def test_orders_list_shows_payment_badge_and_searches_receipts(self):
        url = reverse('administration:staff_sales_orders')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Due 335.00')
        resp = self.client.get(url, {'q': 'R-1001'})
        self.assertContains(resp, '2026-000900')
