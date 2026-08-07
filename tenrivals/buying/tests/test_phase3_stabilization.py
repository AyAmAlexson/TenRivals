"""Phase 3 stabilization: Pure Drive weight → pricing, matching, eligibility."""

from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from buying.connectors.base import SearchCandidate
from buying.engine.pricing import build_cost_scenario
from buying.engine.weight import resolve_chargeable_weight
from buying.models import (
    ConfirmationState,
    NormalizedProduct,
    OfferEligibility,
    ProductCategory,
)
from buying.services.eligibility import apply_offer_eligibility
from buying.services.matching import query_from_normalized, score_candidate
from buying.services.offers import rebuild_scenarios_for_offer

from .utils import (
    make_offer,
    make_request,
    make_route,
    make_standard_rules,
    make_superuser,
    make_supplier,
)


class PureDriveWeightEngineTests(TestCase):
    """Regression: missing_weight must not fire when NormalizedProduct has 300 g."""

    def setUp(self):
        self.user = make_superuser()
        self.supplier = make_supplier()
        self.route = make_route()
        make_standard_rules(self.route)
        self.request_obj = make_request(
            self.user, query='Babolat Pure Drive 100, blue, 2025, grip 4'
        )
        self.np = NormalizedProduct.objects.create(
            brand='Babolat',
            model_name='Pure Drive',
            generation='2025',
            category=ProductCategory.RACQUET,
            head_size='100',
            weight_g=300,
            string_pattern='16x19',
            grip_size='L4',
            color='blue',
            color_policy=NormalizedProduct.ColorPolicy.PREFERRED,
            field_sources={'weight_g': 'canonical', 'head_size': 'client'},
            enrichment_snapshot={
                'weight_g_unstrung': 300,
                'head_size_sqin': 100,
                'string_pattern': '16x19',
                'model_family': 'Pure Drive',
                'generation': '2025',
            },
        )
        self.request_obj.normalized_product = self.np
        self.request_obj.save()

    def test_canonical_weight_reaches_pricing_without_offer_weight(self):
        offer = make_offer(
            self.request_obj,
            self.supplier,
            title='Babolat Pure Drive 100 2025',
            weight_g_actual=None,
            match_status='exact',
        )
        chargeable, meta = resolve_chargeable_weight(offer, 'racquet', quantity=1)
        self.assertEqual(meta['actual_source'], 'canonical_product')
        self.assertEqual(meta['product_g'], 300)
        self.assertIsNotNone(chargeable)
        self.assertGreaterEqual(chargeable, 300)

        scenario = build_cost_scenario(offer, self.route)
        self.assertEqual(scenario.status, 'calculated')
        self.assertNotIn('missing_weight', scenario.calculation_details.get('blocking_issues') or [])
        self.assertFalse(any('blocking:missing_weight' in (w or '') for w in scenario.warnings))
        self.assertEqual(scenario.calculation_details['weight']['actual_source'], 'canonical_product')
        self.assertEqual(scenario.chargeable_weight_g, chargeable)

    def test_local_shipping_missing_does_not_block(self):
        offer = make_offer(
            self.request_obj,
            self.supplier,
            weight_g_actual=None,
            local_shipping_cost=None,
            free_shipping_threshold=None,
        )
        scenario = build_cost_scenario(offer, self.route)
        self.assertEqual(scenario.status, 'calculated')
        self.assertTrue(
            any(w.startswith('missing_rule:local_shipping') for w in scenario.warnings)
            or any(c['code'] == 'local_shipping' and c['source'] == 'unknown' for c in scenario.breakdown)
        )
        self.assertNotIn('blocking:missing_rule:local_shipping', scenario.warnings)


class PureDriveMatchingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_racquet_specs', verbosity=0)

    def setUp(self):
        self.np = NormalizedProduct(
            brand='Babolat',
            model_name='Pure Drive',
            generation='2025',
            category='racquet',
            head_size='100',
            weight_g=300,
            string_pattern='16x19',
            grip_size='L4',
            color='blue',
            color_policy=NormalizedProduct.ColorPolicy.PREFERRED,
        )
        self.query = query_from_normalized(self.np)

    def test_title_without_all_specs_still_matches(self):
        cand = SearchCandidate(
            title='Babolat Pure Drive Tennis Racquet 2025',
            url='https://example.com/pd',
            raw_data={'page_text': 'Head size 100 sq in Unstrung weight 300g 16x19'},
        )
        verdict = score_candidate(self.query, cand)
        self.assertIn(verdict.match_status, ('exact', 'alternative_color'))
        self.assertEqual(verdict.details.get('hard_mismatches'), [])

    def test_gen11_300g_title_matches_2025(self):
        cand = SearchCandidate(
            title='Babolat Pure Drive 100 300g Gen11 Unstrung Tennis Racket',
            url='https://example.com/pd2',
        )
        verdict = score_candidate(self.query, cand)
        self.assertNotEqual(verdict.match_status, 'no_match')
        self.assertEqual(verdict.details.get('hard_mismatches'), [])

    def test_rejects_team_lite_junior(self):
        for title in (
            'Babolat Pure Drive Team 2025',
            'Babolat Pure Drive Lite 2025',
            'Babolat Pure Drive Junior 2025',
            'Babolat Pure Drive 107 2025',
        ):
            cand = SearchCandidate(title=title, url='https://example.com/x')
            verdict = score_candidate(self.query, cand)
            self.assertEqual(verdict.match_status, 'no_match', title)

    def test_color_preferred_is_alternative_color(self):
        cand = SearchCandidate(
            title='Babolat Pure Drive 100 2025 300g White',
            url='https://example.com/pd3',
        )
        verdict = score_candidate(self.query, cand)
        self.assertEqual(verdict.match_status, 'alternative_color')


class EligibilityAndRankingTests(TestCase):
    def setUp(self):
        self.user = make_superuser()
        self.supplier = make_supplier()
        self.route = make_route()
        make_standard_rules(self.route)
        self.request_obj = make_request(self.user)
        self.np = NormalizedProduct.objects.create(
            brand='Babolat',
            model_name='Pure Drive',
            generation='2025',
            category=ProductCategory.RACQUET,
            weight_g=300,
            grip_size='L4',
            field_sources={'weight_g': 'canonical'},
            enrichment_snapshot={'weight_g_unstrung': 300},
        )
        self.request_obj.normalized_product = self.np
        self.request_obj.save()

    def test_unverified_grip_is_partial_and_not_ranked(self):
        offer = make_offer(
            self.request_obj,
            self.supplier,
            weight_g_actual=None,
            requested_variant={'grip_size': 'L4'},
            requested_variant_available=None,
            match_status='exact',
            eligibility_status=OfferEligibility.MANUAL_REVIEW,
            requested_variant_confirmed=ConfirmationState.UNKNOWN,
            product_identity_confirmed=ConfirmationState.UNKNOWN,
            purchase_context_confirmed=ConfirmationState.UNKNOWN,
        )
        apply_offer_eligibility(offer)
        offer.save()
        self.assertEqual(offer.requested_variant_confirmed, ConfirmationState.UNKNOWN)
        self.assertEqual(offer.eligibility_status, OfferEligibility.PARTIAL)

        scenarios = rebuild_scenarios_for_offer(offer)
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].status, 'calculated')
        self.assertIsNone(scenarios[0].rank)
        self.assertEqual(scenarios[0].recommendation, 'review')
