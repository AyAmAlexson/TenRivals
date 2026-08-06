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
