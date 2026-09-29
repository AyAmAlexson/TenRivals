from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from shop.models import (
    Customer,
    Product,
    ProductListing,
    ProductListingChannel,
    ProductType,
    SalesOrder,
    SalesOrderLine,
    StockUnit,
    StockWriteOff,
)
from shop.sales_order_stock import release_lines_to_stock, take_lines_from_stock
from shop.staff_analytics import build_sales_analytics
from shop.stock_receipts import receive_stock_batch, undo_stock_receipt
from shop.stock_units import (
    assign_received_on,
    at_noon,
    build_shelf_age_report,
    record_write_off,
    sync_on_hand_gaps,
    undo_write_off,
)


@override_settings(SECURE_SSL_REDIRECT=False)
class ShelfStackTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_superuser(email='stack@test.com', password='x-secret-1')
        self.client.force_login(self.staff)
        self.product = Product.objects.create(
            type=ProductType.ACCESSORIES,
            name='Stack Grip',
            brand='Tourna',
            initial_price=Decimal('40.00'),
            landed_cost_gel=Decimal('10.00'),
            is_active=True,
        )
        ProductListing.objects.create(
            product=self.product,
            channel=ProductListingChannel.STOCK,
            quantity=0,
        )
        self.customer = Customer.objects.create(
            first_name='Shelf',
            last_name='Buyer',
            email='shelf-buyer@test.com',
            phone='555',
        )

    def _sell(self, qty, invoice='ST-1', when=None):
        order = SalesOrder.objects.create(
            invoice_number=invoice,
            customer=self.customer,
            order_date=when or date(2026, 9, 25),
            status=SalesOrder.Status.CONFIRMED,
            gross_total=Decimal('40.00'),
            vat_total=Decimal('0'),
            net_total=Decimal('40.00'),
        )
        line = SalesOrderLine.objects.create(
            order=order,
            product=self.product,
            product_type=self.product.type,
            quantity=qty,
            unit_price_gross=Decimal('40.00'),
            line_gross=Decimal('40.00'),
            landed_cost_gel=Decimal('10.00'),
        )
        take_lines_from_stock([line])
        return line

    def test_receipt_mints_units_and_sale_takes_oldest(self):
        receive_stock_batch(
            product=self.product,
            quantity=2,
            unit_landed_cost_gel=Decimal('10.00'),
            add_to_stock=True,
        )
        units = list(StockUnit.objects.filter(product=self.product).order_by('id'))
        self.assertEqual(len(units), 2)
        older = units[0]
        older.received_at = at_noon(date(2026, 8, 1))
        older.save(update_fields=['received_at'])
        units[1].received_at = at_noon(date(2026, 9, 1))
        units[1].save(update_fields=['received_at'])

        self._sell(1)
        older.refresh_from_db()
        units[1].refresh_from_db()
        self.assertEqual(older.status, StockUnit.Status.SOLD)
        self.assertEqual(units[1].status, StockUnit.Status.ON_HAND)
        listing = ProductListing.objects.get(product=self.product, channel=ProductListingChannel.STOCK)
        self.assertEqual(listing.quantity, 1)

    def test_undated_units_are_taken_after_dated_ones(self):
        receive_stock_batch(
            product=self.product,
            quantity=1,
            unit_landed_cost_gel=Decimal('10.00'),
        )
        dated = StockUnit.objects.get(product=self.product)
        dated.received_at = at_noon(date(2026, 9, 1))
        dated.save(update_fields=['received_at'])
        StockUnit.objects.create(
            product=self.product,
            status=StockUnit.Status.ON_HAND,
            received_at=None,
            unit_landed_cost_gel=Decimal('10.00'),
        )
        self._sell(1)
        dated.refresh_from_db()
        undated = StockUnit.objects.get(product=self.product, received_at__isnull=True)
        self.assertEqual(dated.status, StockUnit.Status.SOLD)
        self.assertEqual(undated.status, StockUnit.Status.ON_HAND)

    def test_release_returns_the_same_unit(self):
        receive_stock_batch(
            product=self.product,
            quantity=1,
            unit_landed_cost_gel=Decimal('10.00'),
        )
        unit = StockUnit.objects.get()
        unit.received_at = at_noon(date(2026, 7, 1))
        unit.save(update_fields=['received_at'])
        line = self._sell(1, invoice='ST-REL')
        release_lines_to_stock([line])
        unit.refresh_from_db()
        self.assertEqual(unit.status, StockUnit.Status.ON_HAND)
        self.assertIsNone(unit.sold_at)
        listing = ProductListing.objects.get(product=self.product, channel=ProductListingChannel.STOCK)
        self.assertEqual(listing.quantity, 1)

    def test_write_off_skips_sales_and_can_be_undone(self):
        receive_stock_batch(
            product=self.product,
            quantity=2,
            unit_landed_cost_gel=Decimal('12.50'),
        )
        doc = record_write_off(
            written_on=date(2026, 9, 22),
            reason=StockWriteOff.Reason.BLOGGER,
            note='mailer',
            lines=[{'product': self.product, 'variant_label': '', 'quantity': 1}],
            created_by=self.staff,
        )
        self.assertEqual(SalesOrder.objects.count(), 0)
        listing = ProductListing.objects.get(product=self.product, channel=ProductListingChannel.STOCK)
        self.assertEqual(listing.quantity, 1)
        self.assertEqual(
            StockUnit.objects.filter(status=StockUnit.Status.WRITTEN_OFF).count(),
            1,
        )
        data = build_sales_analytics(date(2026, 9, 1), date(2026, 9, 30), 'month')
        self.assertEqual(data['totals']['orders'], 0)
        self.assertEqual(data['totals']['revenue'], Decimal('0.00'))
        report = build_shelf_age_report(date(2026, 9, 1), date(2026, 9, 30), today=date(2026, 9, 29))
        self.assertEqual(report['write_off_units'], 1)
        self.assertEqual(report['write_off_landed'], Decimal('12.50'))

        undo_write_off(doc)
        listing.refresh_from_db()
        self.assertEqual(listing.quantity, 2)
        self.assertEqual(StockWriteOff.objects.count(), 0)
        self.assertEqual(StockUnit.objects.filter(status=StockUnit.Status.ON_HAND).count(), 2)

    def test_sync_and_assign_dates_existing_stock(self):
        listing = ProductListing.objects.get(product=self.product, channel=ProductListingChannel.STOCK)
        listing.quantity = 3
        listing.save(update_fields=['quantity'])
        created = sync_on_hand_gaps()
        self.assertEqual(created, 3)
        n = assign_received_on(
            product=self.product,
            variant_label='',
            quantity=2,
            received_on=date(2026, 8, 15),
        )
        self.assertEqual(n, 2)
        self.assertEqual(StockUnit.objects.filter(received_at__isnull=True).count(), 1)
        report = build_shelf_age_report(date(2026, 9, 1), date(2026, 9, 30), today=date(2026, 9, 29))
        cat = report['categories'][0]
        self.assertEqual(cat['on_hand'], 3)
        self.assertEqual(cat['dated'], 2)
        self.assertEqual(cat['undated'], 1)
        # 15 Aug → 29 Sep = 45 days
        self.assertEqual(cat['avg_age'], 45)
        self.assertEqual(cat['median_age'], 45)

    def test_undo_receipt_removes_its_units(self):
        receipt = receive_stock_batch(
            product=self.product,
            quantity=2,
            unit_landed_cost_gel=Decimal('10.00'),
        )
        undo_stock_receipt(receipt)
        self.assertEqual(StockUnit.objects.count(), 0)
        listing = ProductListing.objects.get(product=self.product, channel=ProductListingChannel.STOCK)
        self.assertEqual(listing.quantity, 0)

    def test_stack_and_write_off_pages_render(self):
        receive_stock_batch(
            product=self.product,
            quantity=1,
            unit_landed_cost_gel=Decimal('10.00'),
        )
        stack = self.client.get(reverse('administration:staff_stock_stack'))
        self.assertEqual(stack.status_code, 200)
        self.assertContains(stack, 'Stack Grip')
        dates = self.client.get(reverse('administration:staff_stock_arrival_dates'))
        self.assertEqual(dates.status_code, 200)
        self.assertContains(dates, 'Stack Grip')
        self.assertContains(dates, 'Update')
        new_wo = self.client.get(reverse('administration:staff_write_off_new'))
        self.assertEqual(new_wo.status_code, 200)
