from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import TestCase

from persons.account_display import account_initials_for_user

from shop.models import Product, ProductListing, ProductListingChannel, ProductType
from shop.promo_codes import PromoEvaluation


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


class ProductListingInStockSyncTests(TestCase):
    def test_stock_quantity_zero_sets_in_stock_false(self):
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
        self.assertFalse(p.in_stock)

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
        self.assertFalse(p.in_stock)
