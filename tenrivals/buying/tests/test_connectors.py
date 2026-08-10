"""Connector fixture tests (no live network)."""

from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase, TestCase, override_settings

from buying.connectors.base import NormalizedProductQuery, SearchCandidate
from buying.connectors.http import sanitize_for_storage
from buying.connectors.jsonld import parse_product_json_ld
from buying.connectors.registry import get_connector, list_connector_codes
from buying.connectors.stores.tennis_warehouse_eu import TennisWarehouseEuropeConnector
from buying.engine.routes import applicable_routes
from buying.models import OnexApplicability, Supplier
from buying.tests.utils import make_route, make_supplier

FIXTURES = Path(__file__).parent / 'fixtures'


def _load(code: str, name: str) -> str:
    return (FIXTURES / code / name).read_text(encoding='utf-8')


class RegistryTests(SimpleTestCase):
    def test_all_phase3_codes_registered(self):
        codes = set(list_connector_codes())
        expected = {
            'tennis-warehouse-eu', 'tennis-warehouse-us', 'midwest-racquet-sports',
            'tennis-express', 'mister-tennis', 'direct-tennis', 'tennis-point-de',
            'tennis-point-com', 'itf-tennis-point', 'ole-tennis', 'holabird-sports',
            'saburi-sports', 'smashinn', 'tennispro-eu', 'passa-sports',
            'extreme-tennis', 'm1-tennis', 'central-tennis', 'tennis-nuts',
            'prodirect-sport',
        }
        self.assertTrue(expected.issubset(codes), expected - codes)


class JsonLdAndSanitizeTests(SimpleTestCase):
    def test_json_ld_product(self):
        html = _load('tennis-warehouse-eu', 'product.html')
        parsed = parse_product_json_ld(html)
        self.assertEqual(parsed['price'], Decimal('229.90'))
        self.assertEqual(parsed['currency'], 'EUR')

    def test_sanitize_redacts_password(self):
        cleaned = sanitize_for_storage({'password': 'secret', 'title': 'ok'})
        self.assertEqual(cleaned['password'], '[redacted]')
        self.assertEqual(cleaned['title'], 'ok')

    def test_sanitize_decimal_for_json(self):
        cleaned = sanitize_for_storage({
            'variants': [{'label': 'L3', 'price': Decimal('224.95'), 'available': True}],
        })
        self.assertEqual(cleaned['variants'][0]['price'], '224.95')
        # Must be JSON-serializable (Django JSONField path)
        import json
        json.dumps(cleaned)

    def test_detect_aws_waf_challenge_header(self):
        from buying.connectors.exceptions import CaptchaDetected
        from buying.connectors.http import detect_block_page

        with self.assertRaises(CaptchaDetected):
            detect_block_page(
                '',
                202,
                headers={
                    'server': 'CloudFront',
                    'x-amzn-waf-action': 'challenge',
                },
            )

    def test_detect_aws_waf_challenge_body(self):
        from buying.connectors.exceptions import CaptchaDetected
        from buying.connectors.http import detect_block_page

        html = '<script>window.awsWafCookieDomainList = ["midwestracquetsports.com"];</script>'
        with self.assertRaises(CaptchaDetected):
            detect_block_page(html, 202, headers={'server': 'CloudFront'})

    def test_localize_shopify_handle_compounds(self):
        from buying.connectors.shopify import localize_shopify_handle

        handles = localize_shopify_handle(
            'wilson-blade-100-v10-turnierschlaeger-unbesaitet-00707604278000'
        )
        self.assertIn(
            'wilson-blade-100-v10-tour-racket-unstrung-00707604278000',
            handles,
        )
        # Must not mangle testschlaeger → testracket via bare "schlaeger"
        pro = localize_shopify_handle(
            'wilson-blade-100-pro-v10-turnierschlaeger-testschlaeger-00707604286800'
        )
        self.assertTrue(any('test-racket' in h for h in pro))
        self.assertFalse(any('testracket' in h for h in pro))

    def test_shopify_offer_from_js_payload(self):
        from buying.connectors.shopify import offer_from_shopify_product

        product = {
            'title': 'Blade 100 V10 Tour racket unstrung',
            'handle': 'wilson-blade-100-v10-tour-racket-unstrung-00707604278000',
            'description': '<p>Wilson Blade 100 V10</p>',
            'variants': [
                {
                    'id': 1,
                    'title': 'unstrung / 3',
                    'option1': 'unstrung',
                    'option2': '3',
                    'price': '22495',
                    'available': True,
                    'sku': '0070760427800003',
                    'barcode': '',
                },
                {
                    'id': 2,
                    'title': 'unstrung / 4',
                    'option1': 'unstrung',
                    'option2': '4',
                    'price': '22495',
                    'available': False,
                    'sku': '0070760427800004',
                },
            ],
            'options': [
                {'name': 'Variant', 'values': ['unstrung']},
                {'name': 'Grip size', 'values': ['1', '2', '3', '4']},
            ],
        }
        offer = offer_from_shopify_product(
            product,
            page_url='https://www.tennis-point.com/products/x',
            default_currency='EUR',
            default_tax_mode='vat_included',
            default_destination_country='DE',
            parser_version='test',
            wanted_grip='L3',
        )
        self.assertEqual(offer.effective_price, Decimal('224.95'))
        self.assertEqual(offer.requested_variant_available, True)
        self.assertEqual(offer.supplier_sku, '0070760427800003')

    def test_twe_title_from_url(self):
        title = TennisWarehouseEuropeConnector._title_from_twe_url(
            'https://www.tenniswarehouse-europe.com/Wilson_Blade_100_v10_Racket/descpageRCWILSON-WB1001-EN.html'
        )
        self.assertIn('Blade 100', title)


class TennisWarehouseEuFixtureTests(SimpleTestCase):
    def test_search_and_product_parse(self):
        connector = TennisWarehouseEuropeConnector()
        query = NormalizedProductQuery(brand='Babolat', model_name='Pure Drive', grip_size='L3')
        candidates = connector.parse_search_html(
            _load('tennis-warehouse-eu', 'search.html'),
            page_url='https://www.tenniswarehouse-europe.com/search',
            query=query,
        )
        self.assertEqual(len(candidates), 1)
        self.assertIn('descpage', candidates[0].url)
        offer = connector.parse_product_html(
            _load('tennis-warehouse-eu', 'product.html'),
            page_url='https://www.tenniswarehouse-europe.com/descpage.html',
            candidate=candidates[0],
            query=query,
        )
        self.assertEqual(offer.effective_price, Decimal('229.90'))
        self.assertEqual(offer.currency, 'EUR')
        self.assertTrue(offer.destination_selection_confirmed)
        self.assertEqual(offer.selected_destination_country, 'DE')
        self.assertTrue(offer.requested_variant_available)


class GenericStoreFixtureTests(SimpleTestCase):
    def test_us_and_uk_json_ld(self):
        for code, fixture, currency in (
            ('tennis-warehouse-us', 'tennis-warehouse-us', 'USD'),
            ('direct-tennis', 'direct-tennis', 'GBP'),
            ('itf-tennis-point', 'itf-tennis-point', 'EUR'),
        ):
            connector = get_connector(code)
            html = _load(fixture, 'product.html')
            offer = connector.parse_product_html(
                html,
                page_url=connector.base_url + 'product',
                candidate=SearchCandidate(title='x', url=connector.base_url),
                query=NormalizedProductQuery(brand='Test', model_name='Model'),
            )
            self.assertEqual(offer.currency, currency)
            self.assertGreater(offer.effective_price, 0)


class OnexApplicabilityTests(TestCase):
    def test_tennis_point_com_has_no_routes(self):
        make_route(supplier_country='DE')
        supplier = make_supplier(
            code='tennis-point-com',
            country='DE',
            currency='EUR',
            onex_applicability=OnexApplicability.UNSUPPORTED,
        )
        self.assertEqual(applicable_routes(supplier), [])

    def test_supported_supplier_gets_routes(self):
        make_route(supplier_country='US')
        supplier = make_supplier(
            code='tennis-warehouse-us',
            country='US',
            onex_applicability=OnexApplicability.SUPPORTED,
        )
        self.assertTrue(applicable_routes(supplier))
