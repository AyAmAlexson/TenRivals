"""Tests for tennis racquet enrichment and hard matching constraints."""

from django.core.management import call_command
from django.test import TestCase

from buying.ai.base import AIProvider, AIResult
from buying.connectors.base import SearchCandidate
from buying.models import NormalizedProduct, RacquetSpecification
from buying.services.enrichment import (
    EnrichmentInput,
    SOURCE_CANONICAL,
    SOURCE_STAFF,
    enrich_racquet,
    strip_spec_tokens_from_model,
)
from buying.services.matching import query_from_normalized, score_candidate
from buying.services.normalization import mark_staff_field_sources, normalize_buying_request

from .utils import make_request, make_superuser


class FakeProvider(AIProvider):
    name = 'fake'

    def __init__(self, data):
        self._data = data

    def normalize(self, text):
        return AIResult(
            data=self._data,
            model_version='fake-model-1',
            prompt_version='norm-v2',
            raw_response={'echo': text},
        )


def _ai(brand, model, generation='', **extra):
    base = {
        'brand': brand,
        'model': model,
        'generation': generation,
        'category': 'racquet',
        'gender': '',
        'court': '',
        'size': '',
        'size_system': '',
        'grip_size': '',
        'color': '',
        'weight': '',
        'head_size': '',
        'string_pattern': '',
        'manufacturer_code': '',
        'ean': '',
        'upc': '',
        'quantity': 1,
        'required_attributes': ['brand', 'model'],
        'optional_attributes': [],
        'uncertainties': ['Weight unknown', 'Head size uncertain'],
        'aliases': [],
    }
    base.update(extra)
    return base


class RacquetEnrichmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_racquet_specs', verbosity=0)

    def test_pure_drive_100_2025_resolves_standard_specs(self):
        result = enrich_racquet(EnrichmentInput(
            brand='Babolat',
            model_name='Pure Drive 100',
            generation='2025',
            category='racquet',
            uncertainties=['Weight unknown', 'Head size uncertain'],
            original_query='Babolat Pure Drive 100 2025',
        ))
        self.assertTrue(result.resolved)
        self.assertFalse(result.ambiguous)
        self.assertEqual(result.model_name, 'Pure Drive')
        self.assertEqual(result.head_size, '100')
        self.assertEqual(result.weight_g, 300)
        self.assertEqual(result.string_pattern, '16x19')
        self.assertEqual(result.field_sources.get('weight_g'), SOURCE_CANONICAL)
        # Head size was present in the model/query text → client (or ai), then confirmed by KB
        self.assertIn(result.field_sources.get('head_size'), ('client', 'ai', SOURCE_CANONICAL))
        self.assertFalse(any('weight' in (u or '').lower() for u in result.uncertainties))
        self.assertFalse(any('head' in (u or '').lower() for u in result.uncertainties))

    def test_pure_drive_team_not_standard_weight(self):
        result = enrich_racquet(EnrichmentInput(
            brand='Babolat',
            model_name='Pure Drive Team',
            generation='2025',
            category='racquet',
            original_query='Babolat Pure Drive Team 2025',
        ))
        self.assertTrue(result.resolved)
        self.assertEqual(result.variant, 'Team')
        self.assertEqual(result.weight_g, 285)
        self.assertNotEqual(result.weight_g, 300)

    def test_pure_drive_2025_ambiguous_no_auto_weight(self):
        result = enrich_racquet(EnrichmentInput(
            brand='Babolat',
            model_name='Pure Drive',
            generation='2025',
            category='racquet',
            uncertainties=['Weight unknown'],
            original_query='Babolat Pure Drive 2025',
        ))
        self.assertTrue(result.ambiguous)
        self.assertFalse(result.resolved)
        self.assertIsNone(result.weight_g)
        self.assertTrue(any('multiple' in (u or '').lower() for u in result.uncertainties))

    def test_blade_98_tokens_separated(self):
        cleaned, extracted = strip_spec_tokens_from_model('Blade 98 16x19 v9 305g')
        self.assertEqual(cleaned.lower().replace('v9', '').strip(), 'blade')
        self.assertEqual(extracted.get('head_size'), '98')
        self.assertEqual(extracted.get('string_pattern'), '16x19')
        self.assertEqual(extracted.get('weight_g'), 305)

        result = enrich_racquet(EnrichmentInput(
            brand='Wilson',
            model_name='Blade 98 16x19 v9 305g',
            generation='v9',
            category='racquet',
            weight_g=305,
            head_size='98',
            string_pattern='16x19',
            original_query='Wilson Blade 98 16x19 v9 305g L3',
        ))
        self.assertTrue(result.resolved)
        self.assertEqual(result.model_name, 'Blade')
        self.assertEqual(result.head_size, '98')
        self.assertEqual(result.weight_g, 305)
        self.assertEqual(result.string_pattern, '16x19')
        self.assertEqual(result.generation.lower(), 'v9')

    def test_standard_rejects_team_lite_junior_offers(self):
        product = NormalizedProduct(
            brand='Babolat',
            model_name='Pure Drive',
            generation='2025',
            category='racquet',
            head_size='100',
            weight_g=300,
            string_pattern='16x19',
            variant='',
            grip_size='L4',
        )
        query = query_from_normalized(product)

        for title in (
            'Babolat Pure Drive Team 2025 285g',
            'Babolat Pure Drive Lite 2025',
            'Babolat Pure Drive Junior 2025',
        ):
            cand = SearchCandidate(
                title=title,
                url='https://example.com/x',
            )
            verdict = score_candidate(query, cand)
            self.assertEqual(verdict.match_status, 'no_match', title)
            self.assertTrue(verdict.details['hard_mismatches'], title)

        ok = SearchCandidate(
            title='Babolat Pure Drive 100 2025 300g 16x19',
            url='https://example.com/ok',
        )
        ok_verdict = score_candidate(query, ok)
        self.assertNotEqual(ok_verdict.match_status, 'no_match')

    def test_staff_edit_overrides_canonical_source(self):
        user = make_superuser()
        buying_request = make_request(
            user, query='Babolat Pure Drive 100, blue, 2025, grip 4'
        )
        data = _ai(
            'Babolat', 'Pure Drive 100', '2025',
            grip_size='4', color='blue',
            uncertainties=['Weight unknown'],
        )
        product = normalize_buying_request(buying_request, provider=FakeProvider(data))
        self.assertEqual(product.weight_g, 300)
        self.assertEqual(product.field_sources.get('weight_g'), SOURCE_CANONICAL)

        product.weight_g = 310
        mark_staff_field_sources(product, ['weight_g'])
        product.edited_by_staff = True
        product.save()
        product.refresh_from_db()
        self.assertEqual(product.weight_g, 310)
        self.assertEqual(product.field_sources.get('weight_g'), SOURCE_STAFF)

    def test_historical_snapshot_unchanged_when_spec_edited(self):
        user = make_superuser()
        buying_request = make_request(user, query='Babolat Pure Drive 100 2025')
        data = _ai('Babolat', 'Pure Drive 100', '2025', uncertainties=['Weight unknown'])
        product = normalize_buying_request(buying_request, provider=FakeProvider(data))
        snap_weight = product.enrichment_snapshot.get('weight_g_unstrung')
        self.assertEqual(product.weight_g, 300)
        self.assertEqual(snap_weight, 300)

        spec = RacquetSpecification.objects.get(
            brand='Babolat', model_family='Pure Drive', generation='2025',
            variant='', head_size_sqin=100, string_pattern='16x19',
        )
        spec.weight_g_unstrung = 999
        spec.save(update_fields=['weight_g_unstrung', 'updated_at'])

        product.refresh_from_db()
        self.assertEqual(product.weight_g, 300)
        self.assertEqual(product.enrichment_snapshot.get('weight_g_unstrung'), 300)

    def test_full_query_with_color_and_grip(self):
        user = make_superuser()
        buying_request = make_request(
            user, query='Babolat Pure Drive 100, blue, 2025, grip 4'
        )
        data = _ai(
            'Babolat', 'Pure Drive 100', '2025',
            grip_size='4', color='blue',
            uncertainties=['Weight unknown', 'Head size uncertain'],
        )
        product = normalize_buying_request(buying_request, provider=FakeProvider(data))
        self.assertEqual(product.brand, 'Babolat')
        self.assertEqual(product.model_name, 'Pure Drive')
        self.assertEqual(product.generation, '2025')
        self.assertEqual(product.head_size, '100')
        self.assertEqual(product.weight_g, 300)
        self.assertEqual(product.string_pattern, '16x19')
        self.assertEqual(product.grip_size, 'L4')
        self.assertEqual(product.color, 'blue')
        self.assertEqual(product.uncertainties, [])
