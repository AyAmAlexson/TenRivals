from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from shop.ai_listing import (
    ListingFillError,
    extract_page_text,
    get_listing_fill_job,
    start_listing_fill_job,
    system_prompt_for_type,
    validate_listing_payload,
    validate_source_url,
)
from shop.models import ProductType
from shop.site_locale import shop_reverse


class ValidateSourceUrlTests(TestCase):
    def test_rejects_non_http(self):
        with self.assertRaises(ListingFillError):
            validate_source_url('ftp://example.com/p', resolve=False)
        with self.assertRaises(ListingFillError):
            validate_source_url('file:///etc/passwd', resolve=False)

    def test_rejects_credentials_and_localhost(self):
        with self.assertRaises(ListingFillError):
            validate_source_url('https://user:pass@example.com/p', resolve=False)
        with self.assertRaises(ListingFillError):
            validate_source_url('http://localhost/product', resolve=False)
        with self.assertRaises(ListingFillError):
            validate_source_url('https://shop.internal/p', resolve=False)

    def test_accepts_https_without_resolve(self):
        self.assertEqual(
            validate_source_url('https://example.com/wilson-blade', resolve=False),
            'https://example.com/wilson-blade',
        )

    def test_rejects_private_resolved_ip(self):
        with patch('shop.ai_listing.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 0))]):
            with self.assertRaises(ListingFillError):
                validate_source_url('https://evil.example/p', resolve=True)


class PromptAndPayloadTests(TestCase):
    def test_racket_prompt_covers_dedicated_fields(self):
        prompt = system_prompt_for_type(ProductType.RACKET)
        for token in (
            'weight_grams',
            'head_size_sq_in',
            'balance_mm',
            'swingweight',
            'string_pattern',
            'is_strung',
            'Beam',
            'Playing style',
        ):
            self.assertIn(token, prompt)

    def test_shoe_prompt_covers_gender_surface_width(self):
        prompt = system_prompt_for_type(ProductType.MENS_SHOES)
        for token in ('gender', 'surface', 'width', 'Upper', 'Outsole', 'Court type'):
            self.assertIn(token, prompt)

    def test_string_prompt_covers_material_and_gauges(self):
        prompt = system_prompt_for_type(ProductType.STRINGS)
        self.assertIn('length_m', prompt)
        self.assertIn('Gauge', prompt)

    def test_grip_prompt_covers_pack_size(self):
        prompt = system_prompt_for_type(ProductType.GRIPS)
        self.assertIn('Pack size', prompt)
        self.assertIn('Thickness', prompt)

    def test_validate_payload_maps_racket_and_scrubs_retailer(self):
        data = validate_listing_payload(
            {
                'brand': 'Wilson',
                'name': 'Blade 100 V9',
                'color': 'Black/Green',
                'sku': 'WBL100V9BK',
                'short_description': 'Control-oriented 100 sq in frame with a stable, connected feel.',
                'description': 'According to Tennis Warehouse this Blade rewards precision.',
                'initial_price': 249.99,
                'price_currency': 'EUR',
                'gender': 'M',
                'surface': 'AC',
                'width': None,
                'weight_grams': 305,
                'head_size_sq_in': 100,
                'length_in': 27,
                'balance_mm': 320,
                'swingweight': 324,
                'string_pattern': '16x19',
                'is_strung': False,
                'material': 'graphite',
                'length_m': 12,
                'capacity_rackets': 6,
                'balls_per_can': 3,
                'attributes': [
                    {'parameter': 'Color', 'value': 'ignore me'},
                    {'parameter': 'Beam', 'value': '21.5 mm'},
                    {'parameter': 'Main benefits', 'value': 'Control, feel'},
                ],
                'notes_for_staff': None,
            },
            product_type=ProductType.RACKET,
        )
        self.assertEqual(data['brand'], 'Wilson')
        self.assertEqual(data['weight_grams'], 305)
        self.assertEqual(data['string_pattern'], '16x19')
        self.assertIs(data['is_strung'], False)
        self.assertEqual(data['gender'], '')
        self.assertIsNone(data['length_m'])
        self.assertIsNone(data['capacity_rackets'])
        self.assertNotIn('Color', data['attributes'])
        self.assertEqual(data['attributes']['Beam'], '21.5 mm')
        self.assertNotIn('Tennis Warehouse', data['description'])
        self.assertEqual(data['sku'], 'WBL100V9BK')
        self.assertIsNone(data['initial_price'])
        self.assertEqual(data['price_currency'], '')

    def test_extract_json_ld_and_title(self):
        html = """
        <html><head><title>Ignore</title>
        <script type="application/ld+json">
        {"@type":"Product","name":"Pure Aero 98","brand":{"name":"Babolat"},"offers":{"price":"279.99","priceCurrency":"EUR"}}
        </script>
        </head><body><h1>Babolat Pure Aero 98</h1><p>Spin-friendly frame.</p></body></html>
        """
        text, json_ld = extract_page_text(html)
        self.assertIn('Babolat Pure Aero 98', text)
        self.assertIn('Pure Aero 98', json_ld)
        self.assertIn('EUR', json_ld)


@override_settings(SECURE_SSL_REDIRECT=False, BUYING_OPENAI_API_KEY='')
class ProductAiFillViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_superuser(
            email='ai-fill-staff@test.com',
            password='secret-secret',
        )
        self.url = shop_reverse('shop:product_ai_fill', site_locale='ge_en')
        cache.clear()

    def test_anonymous_is_redirected(self):
        response = self.client.post(self.url, {'url': 'https://example.com/p', 'product_type': 'RACKET'})
        self.assertEqual(response.status_code, 302)

    def test_missing_key_returns_400(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            self.url,
            {'url': 'https://example.com/wilson-blade', 'product_type': 'RACKET'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('OpenAI', response.json()['error'])

    def test_unknown_type_returns_400(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            self.url,
            {'url': 'https://example.com/p', 'product_type': 'NOT_A_TYPE'},
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(BUYING_OPENAI_API_KEY='sk-test')
    def test_start_job_returns_pending(self):
        self.client.force_login(self.staff)
        with patch('shop.ai_listing.threading.Thread') as thread_cls:
            response = self.client.post(
                self.url,
                {'url': 'https://example.com/wilson-blade', 'product_type': 'RACKET'},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'pending')
        self.assertTrue(payload['job_id'])
        thread_cls.assert_called_once()
        thread_cls.return_value.start.assert_called_once()

    def test_poll_returns_cached_result(self):
        self.client.force_login(self.staff)
        job_id = 'ab' * 16
        cache.set(
            f'shop_ai_listing:{job_id}',
            {'ok': True, 'status': 'ok', 'fields': {'name': 'Blade 100 V9', 'brand': 'Wilson'}},
            60,
        )
        response = self.client.get(self.url, {'job_id': job_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fields']['name'], 'Blade 100 V9')

    def test_get_listing_fill_job_rejects_junk_id(self):
        self.assertIsNone(get_listing_fill_job('../etc/passwd'))
        self.assertIsNone(get_listing_fill_job(''))

    @override_settings(BUYING_OPENAI_API_KEY='sk-test')
    def test_start_listing_fill_job_rejects_unknown_type(self):
        with self.assertRaises(ListingFillError):
            start_listing_fill_job(url='https://example.com/p', product_type='NOPE')
