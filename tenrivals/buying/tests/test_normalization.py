from django.test import TestCase

from buying.ai.base import AIProvider, AIProviderError, AIResult
from buying.models import BuyingRequest
from buying.services.normalization import _parse_weight_g, normalize_buying_request

from .utils import make_request, make_superuser


class FakeProvider(AIProvider):
    name = 'fake'

    def __init__(self, data):
        self._data = data

    def normalize(self, text):
        return AIResult(
            data=self._data,
            model_version='fake-model-1',
            prompt_version='norm-v1',
            raw_response={'echo': text},
        )


FAKE_DATA = {
    'brand': 'Wilson',
    'model': 'Blade 100',
    'generation': 'V10',
    'category': 'racquet',
    'gender': '',
    'court': '',
    'size': '',
    'size_system': '',
    'grip_size': '3',
    'color': '',
    'weight': '300 g',
    'head_size': '100',
    'string_pattern': '16x19',
    'manufacturer_code': '',
    'ean': '',
    'upc': '',
    'quantity': 1,
    'required_attributes': ['brand', 'model', 'generation', 'grip_size'],
    'optional_attributes': [],
    'uncertainties': ['Grip size assumed to be L3'],
    'aliases': ['Blade 100 v10'],
}


class NormalizationServiceTests(TestCase):
    def test_normalization_persists_product_and_versions(self):
        user = make_superuser()
        buying_request = make_request(user)
        product = normalize_buying_request(buying_request, provider=FakeProvider(FAKE_DATA))

        buying_request.refresh_from_db()
        self.assertEqual(buying_request.status, BuyingRequest.Status.NORMALIZED)
        self.assertEqual(buying_request.normalized_product, product)
        self.assertEqual(product.brand, 'Wilson')
        self.assertEqual(product.model_name, 'Blade 100')
        self.assertEqual(product.weight_g, 300)
        self.assertEqual(product.grip_size, '3')
        self.assertEqual(product.prompt_version, 'norm-v1')
        self.assertEqual(product.model_version, 'fake-model-1')
        self.assertEqual(product.uncertainties, ['Grip size assumed to be L3'])
        self.assertEqual(product.raw_ai_output, {'echo': buying_request.original_query})
        self.assertFalse(product.edited_by_staff)

    def test_renormalization_updates_existing_product(self):
        user = make_superuser()
        buying_request = make_request(user)
        first = normalize_buying_request(buying_request, provider=FakeProvider(FAKE_DATA))
        changed = {**FAKE_DATA, 'color': 'black'}
        second = normalize_buying_request(buying_request, provider=FakeProvider(changed))
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.color, 'black')

    def test_provider_error_leaves_request_untouched(self):
        class BrokenProvider(AIProvider):
            def normalize(self, text):
                raise AIProviderError('boom')

        user = make_superuser()
        buying_request = make_request(user)
        with self.assertRaises(AIProviderError):
            normalize_buying_request(buying_request, provider=BrokenProvider())
        buying_request.refresh_from_db()
        self.assertEqual(buying_request.status, BuyingRequest.Status.DRAFT)
        self.assertIsNone(buying_request.normalized_product)


class WeightParsingTests(TestCase):
    def test_parse_weight_variants(self):
        self.assertEqual(_parse_weight_g('300 g'), 300)
        self.assertEqual(_parse_weight_g('300г'), 300)
        self.assertEqual(_parse_weight_g('0.3 kg'), 300)
        self.assertIsNone(_parse_weight_g(''))
        self.assertIsNone(_parse_weight_g('unknown'))
