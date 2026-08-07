"""Phase 3 stabilization: Pure Drive weight → pricing, matching, eligibility."""

from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from buying.connectors.base import OfferData, SearchCandidate
from buying.engine.pricing import build_cost_scenario
from buying.engine.weight import resolve_chargeable_weight
from buying.models import (
    ConfirmationState,
    NormalizedProduct,
    OfferEligibility,
    ProductCategory,
)
from buying.services.eligibility import apply_offer_eligibility
from buying.services.matching import MATCHING_RULES_VERSION, query_from_normalized, score_candidate
from buying.services.offers import rebuild_scenarios_for_offer
from buying.services.product_identity import (
    GRIP_STATUS_AVAILABLE,
    GRIP_STATUS_UNKNOWN,
    apply_grip_to_offer,
    cache_logic_compatible,
    logic_versions,
    rescore_with_product_page,
    verify_grip,
)

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
        self.assertEqual(scenario.calculation_details.get('local_shipping_status'), 'unknown')
        self.assertTrue(any('unknown:local_shipping' in (w or '') for w in scenario.warnings))
        self.assertTrue(any('provisional:local_shipping_unknown' in (w or '') for w in scenario.warnings))
        self.assertNotIn('blocking:missing_rule:local_shipping', scenario.warnings)
        # Component must not look like confirmed free shipping
        ship = next(c for c in scenario.breakdown if c['code'] == 'local_shipping')
        self.assertEqual(ship['source'], 'unknown')
        self.assertFalse(ship['exact'])


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
            'Babolat Mini Pure Drive Racquet 2025',
        ):
            cand = SearchCandidate(title=title, url='https://example.com/x')
            verdict = score_candidate(self.query, cand)
            self.assertEqual(verdict.match_status, 'no_match', title)

    def test_mini_pure_drive_never_alternative_color(self):
        """Regression: Mini Pure Drive must be rejected, never Confirmed/alt color."""
        cand = SearchCandidate(
            title='Babolat Mini Pure Drive Racquet 2025',
            url='https://oletennis.com/products/mini-pure-drive',
        )
        verdict = score_candidate(self.query, cand)
        self.assertEqual(verdict.match_status, 'no_match')
        self.assertTrue(
            any('mini' in (m or '').lower() or 'variant' in (m or '').lower()
                for m in verdict.details['hard_mismatches'])
        )

    def test_page_related_junior_does_not_reject_adult_title(self):
        """Related Junior SKUs in page HTML must not kill a standard Pure Drive title."""
        cand = SearchCandidate(
            title='Babolat Pure Drive Tennis Racquet 2025',
            url='https://oletennis.com/products/pure-drive',
            raw_data={
                'page_text': (
                    'Babolat Pure Drive Tennis Racquet 2025 Unstrung Weight: 300g '
                    'Head Size: 100 sq in 16x19 Also see Pure Drive Junior and Mini Pure Drive'
                ),
            },
        )
        verdict = score_candidate(self.query, cand)
        self.assertNotEqual(verdict.match_status, 'no_match')
        self.assertEqual(verdict.details.get('hard_mismatches'), [])

    def test_backpack_title_rejected_for_racquet_request(self):
        cand = SearchCandidate(
            title='Babolat Pure Drive Tennis BackPack 2025',
            url='https://oletennis.com/products/backpack',
        )
        verdict = score_candidate(self.query, cand)
        self.assertEqual(verdict.match_status, 'no_match')
        self.assertTrue(any('product_type' in (m or '') for m in verdict.details['hard_mismatches']))

    def test_unstrung_weight_preferred_over_strung(self):
        cand = SearchCandidate(
            title='Babolat Pure Drive 2025',
            url='https://example.com/pd',
            raw_data={
                'page_text': 'Strung weight 318g Unstrung Weight: 300g Head Size: 100 16x19',
            },
        )
        verdict = score_candidate(self.query, cand)
        self.assertNotEqual(verdict.match_status, 'no_match', verdict.details)
        self.assertEqual(verdict.details.get('hard_mismatches'), [])

    def test_color_preferred_is_alternative_color(self):
        cand = SearchCandidate(
            title='Babolat Pure Drive 100 2025 300g White',
            url='https://example.com/pd3',
        )
        verdict = score_candidate(self.query, cand)
        self.assertEqual(verdict.match_status, 'alternative_color')

    def test_page_specs_confirm_incomplete_title(self):
        cand = SearchCandidate(
            title='Babolat Pure Drive 2025',
            url='https://example.com/pd',
        )
        offer = OfferData(
            title='Babolat Pure Drive 2025',
            product_url=cand.url,
            original_price=None,
            displayed_price=Decimal('200'),
            effective_price=Decimal('200'),
            currency='USD',
            raw_payload={
                'page_text': (
                    'Babolat Pure Drive 2025 Head Size: 100 sq in '
                    'Unstrung Weight: 300g String Pattern: 16x19 Length: 27 in'
                ),
            },
        )
        identity = rescore_with_product_page(self.query, cand, offer)
        self.assertNotEqual(identity.match_status, 'no_match')
        self.assertEqual(identity.specs_confirmed.get('head_size'), 100)
        self.assertEqual(identity.specs_confirmed.get('weight_g'), 300)

    def test_grip_l4_normalization_and_verification(self):
        offer = OfferData(
            title='Pure Drive',
            product_url='https://example.com/x',
            original_price=None,
            displayed_price=Decimal('1'),
            effective_price=Decimal('1'),
            currency='USD',
            available_variants=[
                {'label': 'Grip Size 3'},
                {'label': '4 1/2'},
                {'label': 'L5'},
            ],
        )
        result = verify_grip(offer, 'L4')
        self.assertEqual(result.status, GRIP_STATUS_AVAILABLE)
        apply_grip_to_offer(offer, self.query)
        self.assertTrue(offer.requested_variant_available)

        empty = OfferData(
            title='Pure Drive',
            product_url='https://example.com/y',
            original_price=None,
            displayed_price=Decimal('1'),
            effective_price=Decimal('1'),
            currency='USD',
            available_variants=[],
        )
        self.assertEqual(verify_grip(empty, 'L4').status, GRIP_STATUS_UNKNOWN)


class CacheLogicVersionTests(TestCase):
    def test_cache_invalid_when_matching_rules_change(self):
        stored = logic_versions(parser_version='phase3-3', prompt_version='v1')
        self.assertTrue(
            cache_logic_compatible(stored, parser_version='phase3-3', prompt_version='v1')
        )
        self.assertFalse(
            cache_logic_compatible(
                {**stored, 'matching_rules': 'match-rules-v1'},
                parser_version='phase3-3',
                prompt_version='v1',
            )
        )
        self.assertFalse(
            cache_logic_compatible(stored, parser_version='phase3-4', prompt_version='v1')
        )
        self.assertEqual(MATCHING_RULES_VERSION, 'match-rules-v4')


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

    def test_rejected_mini_creates_no_cost_scenario(self):
        offer = make_offer(
            self.request_obj,
            self.supplier,
            title='Babolat Mini Pure Drive Racquet 2025',
            match_status='no_match',
            eligibility_status=OfferEligibility.REJECTED,
            product_identity_confirmed=ConfirmationState.UNAVAILABLE,
        )
        scenarios = rebuild_scenarios_for_offer(offer)
        self.assertEqual(scenarios, [])
        offer.refresh_from_db()
        self.assertEqual(offer.eligibility_status, OfferEligibility.REJECTED)
