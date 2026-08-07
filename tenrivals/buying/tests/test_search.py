"""Deterministic matching and search orchestration tests."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from buying.connectors.base import (
    HealthCheckResult,
    NormalizedProductQuery,
    OfferData,
    SearchCandidate,
)
from buying.models import (
    BuyingRequest,
    NormalizedProduct,
    OnexApplicability,
    SupplierOffer,
    SupplierSearchRun,
)
from buying.services.matching import pick_best_candidates, score_candidate
from buying.services.search import execute_supplier_search, start_search
from buying.tests.utils import make_route, make_standard_rules, make_superuser, make_supplier


class MatchingTests(TestCase):
    def test_exact_brand_model(self):
        query = NormalizedProductQuery(brand='Babolat', model_name='Pure Drive')
        cand = SearchCandidate(title='Babolat Pure Drive 2024 Racquet', url='https://x/1')
        verdict = score_candidate(query, cand)
        self.assertIn(verdict.match_status, ('exact', 'manual_review'))
        self.assertGreaterEqual(verdict.match_score, Decimal('0.40'))

    def test_no_match(self):
        query = NormalizedProductQuery(brand='Wilson', model_name='Blade 98')
        cand = SearchCandidate(title='Nike Court Shoes', url='https://x/2')
        self.assertEqual(score_candidate(query, cand).match_status, 'no_match')


@override_settings(BUYING_SEARCH_SYNC_FALLBACK=True, BUYING_SEARCH_SYNC_BLOCKING=True)
class SearchOrchestrationTests(TestCase):
    def setUp(self):
        self.user = make_superuser()
        self.route = make_route(supplier_country='US')
        make_standard_rules(self.route)
        self.supplier = make_supplier(
            code='tennis-warehouse-us',
            connector_class='tennis-warehouse-us',
            country='US',
            onex_applicability=OnexApplicability.SUPPORTED,
        )
        np = NormalizedProduct.objects.create(
            brand='Wilson', model_name='Clash 100', category='racquet', quantity=1
        )
        self.request = BuyingRequest.objects.create(
            original_query='Wilson Clash 100',
            staff_user=self.user,
            normalized_product=np,
            status=BuyingRequest.Status.NORMALIZED,
            quantity=1,
        )

    def test_search_creates_offer_and_skips_unsupported(self):
        unsupported = make_supplier(
            code='tennis-point-com',
            name='TP Int',
            connector_class='tennis-point-com',
            country='DE',
            currency='EUR',
            onex_applicability=OnexApplicability.UNSUPPORTED,
        )
        make_route(supplier_country='DE')

        offer_data = OfferData(
            title='Wilson Clash 100',
            product_url='https://example.com/p',
            original_price=None,
            displayed_price=Decimal('249.00'),
            effective_price=Decimal('249.00'),
            currency='USD',
            displayed_price_tax_mode='sales_tax_at_checkout',
            selected_destination_country='US',
            destination_selection_confirmed=True,
            purchase_context_status='confirmed',
            stock_status='in_stock',
        )

        fake = MagicMock()
        fake.health_check.return_value = HealthCheckResult(
            status='available', response_time_ms=10, http_status=200, checked_url='https://x'
        )
        fake.search.return_value = [
            SearchCandidate(title='Wilson Clash 100 v2', url='https://example.com/p')
        ]
        fake.get_product_details.return_value = offer_data
        fake.parser_version = 'test'

        with patch('buying.services.search.get_connector_class', return_value=object):
            with patch('buying.services.search.get_connector', return_value=fake):
                with patch('buying.services.search.ConnectorHttpClient'):
                    start_search(self.request, force=True)

        self.request.refresh_from_db()
        self.assertIn(
            self.request.status,
            (
                BuyingRequest.Status.COMPLETED,
                BuyingRequest.Status.PARTIALLY_COMPLETED,
            ),
        )
        us_offers = SupplierOffer.objects.filter(
            buying_request=self.request, supplier=self.supplier, superseded_by__isnull=True
        )
        self.assertEqual(us_offers.count(), 1)
        offer = us_offers.get()
        self.assertEqual(offer.current_price, Decimal('249.00'))
        # Unsupported supplier may also get an offer but no cost scenarios
        tp_runs = SupplierSearchRun.objects.filter(
            buying_request=self.request, supplier=unsupported
        )
        self.assertTrue(tp_runs.exists())

    def test_progress_payload_includes_search_summary(self):
        from buying.services.search import progress_payload
        from buying.models import SupplierSearchRun

        SupplierSearchRun.objects.create(
            buying_request=self.request,
            supplier=self.supplier,
            status=SupplierSearchRun.Status.COMPLETED,
            candidates_found=3,
            offers_created=1,
        )
        payload = progress_payload(self.request)
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['checked'], 1)
        self.assertIn('offers_found', payload)
        self.assertIn('summary', payload)
        self.assertIn('Found', payload['summary'])
        self.assertTrue(payload['done'])
