from django.test import SimpleTestCase

from buying.ai.schemas import SchemaValidationError, validate_normalization


def _valid_payload(**overrides):
    payload = {
        'brand': 'New Balance',
        'model': 'CT-Rally',
        'generation': 'V2',
        'category': 'shoes',
        'gender': 'men',
        'court': 'clay',
        'size': '44.5',
        'size_system': 'EU',
        'grip_size': None,
        'color': 'white',
        'weight': None,
        'head_size': None,
        'string_pattern': None,
        'manufacturer_code': None,
        'ean': None,
        'upc': None,
        'quantity': 1,
        'required_attributes': ['brand', 'model', 'generation', 'gender', 'court', 'size'],
        'optional_attributes': ['color'],
        'uncertainties': [],
        'aliases': [],
    }
    payload.update(overrides)
    return payload


class NormalizationSchemaTests(SimpleTestCase):
    def test_valid_payload_passes_and_nulls_become_empty_strings(self):
        clean = validate_normalization(_valid_payload())
        self.assertEqual(clean['brand'], 'New Balance')
        self.assertEqual(clean['grip_size'], '')
        self.assertEqual(clean['quantity'], 1)
        self.assertEqual(clean['optional_attributes'], ['color'])

    def test_rejects_non_dict(self):
        with self.assertRaises(SchemaValidationError):
            validate_normalization(['not', 'a', 'dict'])

    def test_rejects_unknown_fields(self):
        with self.assertRaises(SchemaValidationError):
            validate_normalization(_valid_payload(price='100'))

    def test_rejects_bad_quantity(self):
        with self.assertRaises(SchemaValidationError):
            validate_normalization(_valid_payload(quantity=0))
        with self.assertRaises(SchemaValidationError):
            validate_normalization(_valid_payload(quantity='two'))

    def test_rejects_unknown_category(self):
        with self.assertRaises(SchemaValidationError):
            validate_normalization(_valid_payload(category='boat'))

    def test_rejects_non_string_list_items(self):
        with self.assertRaises(SchemaValidationError):
            validate_normalization(_valid_payload(uncertainties=[42]))
