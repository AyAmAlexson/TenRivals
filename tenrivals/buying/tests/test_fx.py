"""NBG FX rate fetch, storage and resolution tests (mocked network)."""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from buying.engine.fx import FxRateUnavailable, get_fx_rate_to_gel
from buying.engine.pricing import build_cost_scenario
from buying.integrations.nbg import FxRateData, NbgApiError, NbgRateProvider
from buying.models import CalculationRule, FxRate
from buying.services.fx_rates import georgia_today, store_fx_rates, sync_nbg_rates
from buying.services.offers import rebuild_scenarios_for_offer

from .utils import (
    make_offer,
    make_request,
    make_route,
    make_standard_rules,
    make_superuser,
    make_supplier,
)

SAMPLE_USD = {
    'code': 'USD',
    'quantity': 1,
    'rateFormated': '2.7000',
    'rate': 2.7,
    'date': '2026-08-05T17:01:00.000Z',
    'validFromDate': '2026-08-06T00:00:00.000Z',
    'name': 'US Dollar',
}
SAMPLE_CNY = {
    'code': 'CNY',
    'quantity': 10,
    'rateFormated': '3.8854',
    'rate': 3.8854,
    'date': '2026-08-05T17:01:00.000Z',
    'validFromDate': '2026-08-06T00:00:00.000Z',
    'name': 'China Renminbi',
}


def _payload(*entries, day='2026-08-06T00:00:00.000Z'):
    return [{'date': day, 'currencies': list(entries)}]


class NbgProviderTests(TestCase):
    def test_successful_parse_and_unit_rate_with_quantity(self):
        provider = NbgRateProvider()
        rows = provider._parse_payload(_payload(SAMPLE_USD, SAMPLE_CNY), requested_date=date(2026, 8, 6))
        by_code = {r.currency: r for r in rows}
        self.assertEqual(by_code['USD'].rate_gel, Decimal('2.7000'))
        self.assertEqual(by_code['USD'].quantity, 1)
        self.assertEqual(by_code['CNY'].quantity, 10)
        self.assertEqual(by_code['CNY'].rate_gel, Decimal('3.8854'))
        stored = store_fx_rates(rows)
        self.assertEqual(stored['created'], 2)
        cny = FxRate.objects.get(currency='CNY')
        self.assertEqual(cny.unit_rate_gel, Decimal('0.38854000'))

    def test_repeat_fetch_does_not_duplicate(self):
        rows = NbgRateProvider()._parse_payload(_payload(SAMPLE_USD), requested_date=date(2026, 8, 6))
        store_fx_rates(rows)
        store_fx_rates(rows)
        self.assertEqual(FxRate.objects.filter(currency='USD').count(), 1)

    @patch('buying.integrations.nbg.httpx.get')
    def test_http_error_raises_without_deleting_old_rates(self, mock_get):
        store_fx_rates(
            NbgRateProvider()._parse_payload(_payload(SAMPLE_USD), requested_date=date(2026, 8, 5))
        )
        mock_get.side_effect = NbgApiError('boom')
        # Simulate httpx raising via provider wrapper
        with patch.object(NbgRateProvider, 'fetch_rates', side_effect=NbgApiError('timeout')):
            with self.assertRaises(NbgApiError):
                sync_nbg_rates(force=True, currencies=['USD'])
        self.assertEqual(FxRate.objects.filter(currency='USD').count(), 1)

    def test_empty_payload_raises(self):
        with self.assertRaises(NbgApiError):
            NbgRateProvider()._parse_payload([], requested_date=None)

    @patch('buying.integrations.nbg.httpx.get')
    def test_fetch_filters_currencies_client_side_without_api_param(self, mock_get):
        """NBG returns [] for currencies= query — never send it; filter locally."""
        mock_resp = mock_get.return_value
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = _payload(SAMPLE_USD, SAMPLE_CNY)
        rows = NbgRateProvider().fetch_rates(
            rate_date=date(2026, 8, 6),
            currencies=['USD'],
        )
        self.assertEqual([r.currency for r in rows], ['USD'])
        _args, kwargs = mock_get.call_args
        self.assertEqual(kwargs['params'], {'date': '2026-08-06'})
        self.assertNotIn('currencies', kwargs['params'])


@override_settings(BUYING_NBG_LIVE_FETCH_ON_MISS=False, BUYING_FX_TIMEZONE='Asia/Tbilisi')
class FxResolutionTests(TestCase):
    def setUp(self):
        self.today = date(2026, 8, 6)
        self.yesterday = date(2026, 8, 5)

    def _store(self, rate_date, rate='2.7000', currency='USD', quantity=1):
        return FxRate.objects.create(
            currency=currency,
            rate_gel=Decimal(rate),
            quantity=quantity,
            rate_date=rate_date,
            fetched_at=timezone.now(),
            source=FxRate.Source.NBG,
            published_at=timezone.now(),
        )

    def test_today_rate_is_fresh_nbg_official(self):
        self._store(self.today, '2.6239')
        with patch('buying.engine.fx.georgia_today', return_value=self.today):
            rate, meta = get_fx_rate_to_gel('USD')
        self.assertEqual(rate, Decimal('2.62390000'))
        self.assertEqual(meta['source'], 'nbg_official')
        self.assertFalse(meta['stale'])
        self.assertEqual(meta['rate_date'], '2026-08-06')
        self.assertEqual(meta['unit_rate_gel'], '2.62390000')

    def test_missing_today_uses_stale_latest(self):
        self._store(self.yesterday, '2.6500')
        with patch('buying.engine.fx.georgia_today', return_value=self.today):
            rate, meta = get_fx_rate_to_gel('USD')
        self.assertEqual(rate, Decimal('2.65000000'))
        self.assertEqual(meta['source'], 'nbg_stale')
        self.assertTrue(meta['stale'])
        self.assertEqual(meta['rate_date'], '2026-08-05')

    def test_no_rates_blocks(self):
        with patch('buying.engine.fx.georgia_today', return_value=self.today):
            with self.assertRaises(FxRateUnavailable):
                get_fx_rate_to_gel('USD')

    def test_configured_rule_beats_nbg(self):
        self._store(self.today, '2.6239')
        CalculationRule.objects.create(
            rule_type='fx_rate', name='Manual USD',
            params={'currency': 'USD', 'rate_gel': '2.70'},
        )
        with patch('buying.engine.fx.georgia_today', return_value=self.today):
            rate, meta = get_fx_rate_to_gel('USD')
        self.assertEqual(rate, Decimal('2.70'))
        self.assertEqual(meta['source'], 'configured_rule')

    def test_manual_override_beats_rule_and_nbg(self):
        self._store(self.today, '2.6239')
        CalculationRule.objects.create(
            rule_type='fx_rate', name='Manual USD',
            params={'currency': 'USD', 'rate_gel': '2.70'},
        )
        rate, meta = get_fx_rate_to_gel('USD', manual_unit_rate=Decimal('2.80'))
        self.assertEqual(rate, Decimal('2.80'))
        self.assertEqual(meta['source'], 'manual_override')

    def test_quantity_unit_rate_in_snapshot(self):
        self._store(self.today, '3.8854', currency='CNY', quantity=10)
        with patch('buying.engine.fx.georgia_today', return_value=self.today):
            rate, meta = get_fx_rate_to_gel('CNY')
        self.assertEqual(rate, Decimal('0.38854000'))
        self.assertEqual(meta['quantity'], '10')
        self.assertEqual(meta['official_rate'], '3.88540000')

    def test_georgia_today_uses_tbilisi_not_utc(self):
        # 2026-08-05 22:30 UTC == 2026-08-06 02:30 in Tbilisi (UTC+4)
        utc_evening = datetime(2026, 8, 5, 22, 30, tzinfo=dt_timezone.utc)
        self.assertEqual(georgia_today(utc_evening), date(2026, 8, 6))


@override_settings(BUYING_NBG_LIVE_FETCH_ON_MISS=False, BUYING_FX_TIMEZONE='Asia/Tbilisi')
class FxPricingIntegrationTests(TestCase):
    def setUp(self):
        self.user = make_superuser()
        self.supplier = make_supplier()
        self.route = make_route()
        make_standard_rules(self.route)
        # Drop the fixture FX rule so NBG rates drive the calculation.
        CalculationRule.objects.filter(rule_type='fx_rate').delete()
        self.request_obj = make_request(self.user)
        self.offer = make_offer(self.request_obj, self.supplier)
        self.today = date(2026, 8, 6)

    def _nbg(self, rate_date, rate='2.70'):
        FxRate.objects.create(
            currency='USD',
            rate_gel=Decimal(rate),
            quantity=1,
            rate_date=rate_date,
            fetched_at=timezone.now(),
            source=FxRate.Source.NBG,
        )

    def test_scenario_uses_nbg_without_fx_rule(self):
        self._nbg(self.today, '2.70')
        with patch('buying.engine.fx.georgia_today', return_value=self.today):
            scenario = build_cost_scenario(self.offer, self.route)
        self.assertEqual(scenario.status, 'calculated')
        self.assertEqual(scenario.item_cost, Decimal('270.00'))
        self.assertEqual(scenario.calculation_details['fx']['source'], 'nbg_official')
        self.assertFalse(scenario.calculation_details['fx']['stale'])

    def test_stale_rate_warns_but_calculates(self):
        self._nbg(self.today - timedelta(days=1), '2.70')
        with patch('buying.engine.fx.georgia_today', return_value=self.today):
            scenario = build_cost_scenario(self.offer, self.route)
        self.assertEqual(scenario.status, 'calculated')
        self.assertEqual(scenario.calculation_details['fx']['source'], 'nbg_stale')
        self.assertTrue(scenario.calculation_details['fx']['stale'])
        self.assertIn('stale_fx_rate', scenario.warnings)

    def test_historical_scenario_immutable_after_new_rate(self):
        self._nbg(self.today, '2.70')
        with patch('buying.engine.fx.georgia_today', return_value=self.today):
            first = rebuild_scenarios_for_offer(self.offer)[0]
        first_fx = first.calculation_details['fx']['unit_rate_gel']
        first_price = first.customer_price

        # A newer official rate appears — must not rewrite the old scenario.
        FxRate.objects.filter(currency='USD', rate_date=self.today).update(
            rate_gel=Decimal('3.00')
        )
        first.refresh_from_db()
        self.assertEqual(first.calculation_details['fx']['unit_rate_gel'], first_fx)
        self.assertEqual(first.customer_price, first_price)

        with patch('buying.engine.fx.georgia_today', return_value=self.today):
            second = rebuild_scenarios_for_offer(self.offer)[0]
        first.refresh_from_db()
        self.assertEqual(first.status, 'superseded')
        self.assertEqual(second.calculation_details['fx']['unit_rate_gel'], '3.00000000')
        self.assertNotEqual(second.customer_price, first_price)

    def test_sync_skips_when_present_unless_forced(self):
        self._nbg(self.today, '2.70')
        with patch('buying.services.fx_rates.georgia_today', return_value=self.today):
            with patch('buying.services.fx_rates.NbgRateProvider') as provider_cls:
                stats = sync_nbg_rates(currencies=['USD'], force=False)
                provider_cls.assert_not_called()
        self.assertTrue(stats['skipped'])
