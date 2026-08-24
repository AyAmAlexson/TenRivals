"""Buying calculator / batch combined-shipment pricing tests."""

from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from buying.engine.batch_pricing import (
    BatchLineInput,
    allocate_by_value,
    build_batch_route_quote,
)
from buying.engine.pricing import build_cost_scenario
from buying.models import BuyingBatch, BuyingBatchLine, BuyingBatchQuote
from buying.services.batch_calculator import recalculate_batch

from .utils import (
    make_offer,
    make_request,
    make_route,
    make_standard_rules,
    make_superuser,
    make_supplier,
)


class AllocateByValueTests(TestCase):
    def test_sums_to_total(self):
        parts = allocate_by_value(
            [Decimal('100'), Decimal('300')], Decimal('40.00')
        )
        self.assertEqual(parts[0], Decimal('10.00'))
        self.assertEqual(parts[1], Decimal('30.00'))
        self.assertEqual(sum(parts), Decimal('40.00'))


class BatchPricingTests(TestCase):
    def setUp(self):
        self.user = make_superuser()
        self.supplier = make_supplier()
        self.route = make_route()
        make_standard_rules(self.route)

    def _line(self, title, price, weight=1000, qty=1, category='racquet'):
        return BatchLineInput(
            line_id=None,
            title=title,
            unit_price=Decimal(price),
            currency='USD',
            quantity=qty,
            category=category,
            weight_g=weight,
        )

    def test_single_line_matches_offer_scenario_core(self):
        request_obj = make_request(self.user)
        offer = make_offer(request_obj, self.supplier)
        single = build_cost_scenario(offer, self.route)
        batch_quote = build_batch_route_quote(
            self.supplier,
            [self._line('Wilson Blade 100 V10', '100.00')],
            self.route,
            local_shipping_cost=Decimal('12.00'),
            tax_display_mode='prices_include_vat',
        )
        self.assertEqual(batch_quote.status, BuyingBatchQuote.Status.CALCULATED)
        self.assertEqual(batch_quote.item_cost, single.item_cost)
        self.assertEqual(batch_quote.local_shipping, single.local_shipping)
        self.assertEqual(batch_quote.international_shipping, single.international_shipping)
        self.assertEqual(batch_quote.payment_fee, single.payment_fee)
        self.assertEqual(batch_quote.georgia_vat, single.georgia_vat)
        self.assertEqual(batch_quote.landed_cost, single.landed_cost)
        self.assertEqual(batch_quote.customer_price, single.customer_price)

    def test_two_lines_share_intl_cheaper_than_two_singles(self):
        line_a = self._line('A', '100.00', weight=1000)
        line_b = self._line('B', '100.00', weight=1000)
        combined = build_batch_route_quote(
            self.supplier,
            [line_a, line_b],
            self.route,
            local_shipping_cost=Decimal('12.00'),
            tax_display_mode='prices_include_vat',
        )
        solo_a = build_batch_route_quote(
            self.supplier,
            [line_a],
            self.route,
            local_shipping_cost=Decimal('12.00'),
            tax_display_mode='prices_include_vat',
        )
        solo_b = build_batch_route_quote(
            self.supplier,
            [line_b],
            self.route,
            local_shipping_cost=Decimal('12.00'),
            tax_display_mode='prices_include_vat',
        )
        # 2 kg together → 54 GEL intl vs 27+27 if separate (same), but payment/customs
        # /declaration charged once when declaration applies on the combined cart.
        self.assertEqual(combined.international_shipping, Decimal('54.00'))
        self.assertLess(
            combined.landed_cost,
            solo_a.landed_cost + solo_b.landed_cost,
        )
        shares = [Decimal(r['allocated_shared_gel']) for r in combined.line_results]
        self.assertEqual(sum(shares), combined.shared_cost_total)

    def test_allocation_sums_to_shared(self):
        quote = build_batch_route_quote(
            self.supplier,
            [
                self._line('Cheap', '50.00', weight=500),
                self._line('Pricey', '150.00', weight=500),
            ],
            self.route,
            local_shipping_cost=Decimal('12.00'),
            tax_display_mode='prices_include_vat',
        )
        allocated = sum(
            Decimal(r['allocated_shared_gel']) for r in quote.line_results
        )
        self.assertEqual(allocated, quote.shared_cost_total)
        # Pricey should take 75% of shared costs.
        cheap, pricey = quote.line_results
        self.assertEqual(
            Decimal(pricey['allocated_shared_gel']),
            _three_quarters(quote.shared_cost_total),
        )
        self.assertEqual(
            Decimal(cheap['allocated_shared_gel']) + Decimal(pricey['allocated_shared_gel']),
            quote.shared_cost_total,
        )

    @override_settings(BUYING_NBG_LIVE_FETCH_ON_MISS=False)
    def test_missing_weight_blocks(self):
        quote = build_batch_route_quote(
            self.supplier,
            [BatchLineInput(
                line_id=1,
                title='No weight',
                unit_price=Decimal('100'),
                currency='USD',
                quantity=1,
                category='',
                weight_g=None,
            )],
            self.route,
            tax_display_mode='prices_include_vat',
        )
        self.assertEqual(quote.status, BuyingBatchQuote.Status.CALCULATION_BLOCKED)
        self.assertIn('blocking:missing_weight', quote.warnings)

    @override_settings(BUYING_NBG_LIVE_FETCH_ON_MISS=False)
    def test_category_weight_plus_one_box(self):
        from buying.engine.batch_pricing import CATEGORY_ITEM_WEIGHT_G, DEFAULT_BOX_PACKAGING_G

        # No WEIGHT rule for apparel in fixtures → code fallback + default box.
        quote = build_batch_route_quote(
            self.supplier,
            [
                BatchLineInput(
                    line_id=1,
                    title='Shirt',
                    unit_price=Decimal('40'),
                    currency='USD',
                    quantity=2,
                    category='apparel',
                    weight_g=None,
                ),
                BatchLineInput(
                    line_id=2,
                    title='Shorts',
                    unit_price=Decimal('30'),
                    currency='USD',
                    quantity=1,
                    category='apparel',
                    weight_g=None,
                ),
            ],
            self.route,
            tax_display_mode='prices_include_vat',
            local_shipping_cost=Decimal('12.00'),
        )
        self.assertEqual(quote.status, BuyingBatchQuote.Status.CALCULATED)
        item = CATEGORY_ITEM_WEIGHT_G['apparel']
        # Racquet test rule has packaging_g=0; apparel has no rule → default box.
        expected = item * 3 + DEFAULT_BOX_PACKAGING_G
        self.assertEqual(quote.chargeable_weight_g, expected)
        w = quote.calculation_details['weight']
        self.assertEqual(w['products_g'], item * 3)
        self.assertEqual(w['box_g'], DEFAULT_BOX_PACKAGING_G)


def _three_quarters(total: Decimal) -> Decimal:
    return (total * Decimal('0.75')).quantize(Decimal('0.01'))


@override_settings(SECURE_SSL_REDIRECT=False)
class BatchCalculatorServiceAndViewsTests(TestCase):
    def setUp(self):
        self.user = make_superuser()
        self.supplier = make_supplier()
        self.route = make_route()
        make_standard_rules(self.route)
        self.client.force_login(self.user)

    def test_recalculate_persists_quotes(self):
        batch = BuyingBatch.objects.create(
            supplier=self.supplier, created_by=self.user, title='Test cart'
        )
        BuyingBatchLine.objects.create(
            batch=batch,
            sort_order=1,
            title='Blade',
            unit_price=Decimal('100.00'),
            currency='USD',
            quantity=1,
            category='racquet',
            weight_g=1000,
        )
        quotes = recalculate_batch(batch)
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].status, BuyingBatchQuote.Status.CALCULATED)
        batch.refresh_from_db()
        self.assertEqual(batch.status, BuyingBatch.Status.CALCULATED)

    def test_calculator_create_and_detail(self):
        url = reverse('administration:buying_calculator')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            url,
            {
                'supplier': self.supplier.pk,
                'title': 'My cart',
                'notes': '',
                'lines-TOTAL_FORMS': '1',
                'lines-INITIAL_FORMS': '0',
                'lines-MIN_NUM_FORMS': '1',
                'lines-MAX_NUM_FORMS': '1000',
                'lines-0-title': 'Wilson Blade',
                'lines-0-unit_price': '100.00',
                'lines-0-currency': 'USD',
                'lines-0-quantity': '1',
                'lines-0-category': 'racquet',
                'lines-0-url': '',
                'lines-0-sku_hint': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        batch = BuyingBatch.objects.get()
        self.assertEqual(batch.lines.count(), 1)
        detail = self.client.get(
            reverse('administration:buying_calculator_detail', args=[batch.pk])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Landed')
        self.assertContains(detail, 'Wilson Blade')

    def test_calculator_rejects_empty_second_line_not_required(self):
        """One filled line is enough — no phantom second row on the form."""
        url = reverse('administration:buying_calculator')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        formset = response.context['formset']
        self.assertEqual(formset.total_form_count(), 1)
