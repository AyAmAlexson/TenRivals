from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from shop.models import (
    Customer,
    ProductType,
    SalesOrder,
    SalesOrderLine,
)
from shop.staff_ai_insights import (
    ANALYTICS_SYSTEM_PROMPT,
    STOCK_SYSTEM_PROMPT,
    build_analytics_insight_payload,
    build_stock_insight_payload,
    validate_analytics_report,
    validate_stock_report,
)


class InsightPromptTests(TestCase):
    def test_analytics_prompt_covers_board_and_tools(self):
        for token in (
            'Tenrivals',
            'clay',
            'coverage',
            'Buying module',
            'this_month',
            'last year',
            'Promo codes',
        ):
            self.assertIn(token, ANALYTICS_SYSTEM_PROMPT)

    def test_stock_prompt_covers_cover_and_buy_plan(self):
        for token in (
            'weeks of cover',
            'do-not-buy',
            'P0',
            'Stock receive',
            'hot size',
            'Working capital',
        ):
            self.assertIn(token, STOCK_SYSTEM_PROMPT)


class InsightValidationTests(TestCase):
    def test_analytics_report_coerces_and_drops_empty(self):
        report = validate_analytics_report(
            {
                'headline': 'Profit is real but coverage is thin.',
                'executive_summary': 'MTD trails last August on volume.',
                'scorecard_narrative': 'ROI looks high because COGS is incomplete.',
                'period_takeaways': [
                    {'period': 'this_month', 'takeaway': 'AOV up, orders down.'},
                    {'period': '', 'takeaway': ''},
                ],
                'what_is_working': [{'title': 'Clay shoes', 'detail': 'Highest category ROI.'}],
                'attention': [
                    {
                        'title': 'Unknown COGS',
                        'severity': 'critical',
                        'detail': '18% of revenue has no landed cost.',
                        'why_it_matters': 'Profit is not decision-grade.',
                    }
                ],
                'recommendations': [
                    {
                        'action': 'Backfill landed costs',
                        'horizon': 'this_week',
                        'how': 'Open recent invoices and receive stock costs.',
                        'tools': 'Stock receive, Sales orders',
                        'expected_impact': 'Coverage above 90%',
                    }
                ],
                'risks': [{'risk': 'Customer concentration', 'mitigation': 'Activate 10 lapsed repeat buyers.'}],
                'data_quality': [{'issue': 'Preorder tag default', 'fix': 'Retag true preorders.'}],
                'kpis_to_watch': [
                    {'kpi': 'Cost coverage', 'reading': '82%', 'watch_for': 'Below 90% keep profit directional'}
                ],
            }
        )
        self.assertEqual(report['headline'][:6], 'Profit')
        self.assertEqual(len(report['period_takeaways']), 1)
        self.assertEqual(report['attention'][0]['severity'], 'high')

    def test_stock_report_requires_substance(self):
        with self.assertRaises(Exception):
            validate_stock_report({'headline': '', 'executive_summary': ''})
        report = validate_stock_report(
            {
                'headline': 'Gappy clay run, capital stuck in slow racquets.',
                'executive_summary': 'Buy sizes not new models.',
                'inventory_health': '16+ weeks cover on 601+ frames.',
                'what_sells': [{'title': 'Men clay 9.5–11', 'detail': 'Sold through in 30d.'}],
                'what_lags': [{'title': 'Premium frames', 'detail': 'High shelf value, low velocity.'}],
                'gaps': [{'area': 'Women clay US 8', 'detail': 'Hot zero', 'priority': 'P0'}],
                'excess': [{'area': '311g+ L5', 'detail': 'No 90d sales', 'action': 'Stop reorder'}],
                'size_and_spec_alerts': [{'alert': 'Men AC 10 empty', 'action': 'Reorder 4 pairs'}],
                'buy_plan_thesis': 'Complete clay size runs before new colorways.',
                'buy_plan': [
                    {
                        'priority': 'P0',
                        'category': "Men's Shoes",
                        'what': 'Clay winners, US 9.5–11',
                        'qty_hint': '8–12 pairs',
                        'why': 'Cover under 3 weeks',
                    }
                ],
                'do_not_buy': [{'item': 'New 311g+ SKUs', 'why': 'Existing high-cover stock'}],
                'operations': [{'action': 'Markdown dead 90d SKUs', 'how': 'Promo code or staff discount'}],
                'data_quality': [{'issue': 'Landed cost missing', 'fix': 'Receive remaining batches'}],
            }
        )
        self.assertEqual(report['buy_plan'][0]['priority'], 'P0')
        self.assertEqual(report['gaps'][0]['priority'], 'P0')


@override_settings(SECURE_SSL_REDIRECT=False)
class InsightPayloadTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            first_name='Ana',
            last_name='Test',
            email='ana-insight@test.com',
            phone='555',
        )

    def _order(self, d: date, gross='200.00', cost='80.00'):
        order = SalesOrder.objects.create(
            customer=self.customer,
            order_date=d,
            status=SalesOrder.Status.COMPLETED,
            payment_method='Card terminal',
            gross_total=Decimal(gross),
            vat_total=Decimal('30.51'),
            net_total=Decimal('169.49'),
            invoice_number=f'INS-{d.isoformat()}',
        )
        SalesOrderLine.objects.create(
            order=order,
            custom_label='Wilson Blade 100',
            product_type=ProductType.RACKET,
            quantity=1,
            unit_price_gross=Decimal(gross),
            line_gross=Decimal(gross),
            line_vat=Decimal('30.51'),
            line_net=Decimal('169.49'),
            landed_cost_gel=Decimal(cost),
            sale_channel=SalesOrderLine.SaleChannel.STOCK,
        )
        return order

    def test_analytics_payload_includes_required_windows(self):
        today = date(2026, 8, 13)
        self._order(date(2025, 8, 10))
        self._order(today)
        payload = build_analytics_insight_payload(today=today)
        labels = [p['label'] for p in payload['periods']]
        self.assertIn('this_month', labels)
        self.assertIn('this_year', labels)
        self.assertIn('all_time', labels)
        self.assertTrue(any(x.startswith('same_month_last_year') for x in labels))
        month = next(p for p in payload['periods'] if p['label'] == 'this_month')
        self.assertEqual(month['orders'], 1)
        self.assertIn('Buying module', payload['business']['tools_available'])

    def test_stock_payload_has_cover_rules_and_kpis(self):
        payload = build_stock_insight_payload(today=date(2026, 8, 13))
        self.assertIn('stock_kpis', payload)
        self.assertEqual(payload['cover_rules']['dead_if_no_sales_days'], 90)
        self.assertIn('category_mix', payload)


@override_settings(SECURE_SSL_REDIRECT=False, BUYING_OPENAI_API_KEY='')
class InsightViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_superuser(
            email='insight-staff@test.com',
            password='secret-secret',
        )
        self.url = reverse('administration:staff_sales_analytics_insights')

    def test_anonymous_redirected(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)

    def test_missing_key_returns_400(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 400)
        self.assertIn('OpenAI', response.json()['error'])

    @override_settings(BUYING_OPENAI_API_KEY='sk-test')
    def test_start_job_returns_pending(self):
        self.client.force_login(self.staff)
        with patch('shop.staff_ai_insights.threading.Thread') as thread_cls:
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'pending')
        thread_cls.assert_called_once()
        thread_cls.return_value.start.assert_called_once()
