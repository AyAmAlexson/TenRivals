"""Phase 2 Onex MVP pricing tests.

All rates/thresholds come from CalculationRule fixtures (make_standard_rules);
nothing in the engine hardcodes production tax or tariff values.
"""

from decimal import Decimal

from django.test import TestCase, override_settings

from buying.engine.pricing import PricingError, build_cost_scenario
from buying.engine.weight import resolve_chargeable_weight
from buying.models import CalculationRule
from buying.services.offers import rebuild_scenarios_for_offer
from buying.services.overrides import override_scenario_component

from .utils import (
    make_offer,
    make_request,
    make_route,
    make_standard_rules,
    make_superuser,
    make_supplier,
)


class PricingEngineTests(TestCase):
    """Default fixture: 100 USD + 12 USD local ship + 1 kg → local cost 302.40 ≥ 300,
    so an import declaration is required."""

    def setUp(self):
        self.user = make_superuser()
        self.supplier = make_supplier()
        self.route = make_route()
        make_standard_rules(self.route)
        self.request_obj = make_request(self.user)
        self.offer = make_offer(self.request_obj, self.supplier)

    def test_full_breakdown_above_threshold(self):
        scenario = build_cost_scenario(self.offer, self.route)
        self.assertEqual(scenario.status, 'calculated')
        # 100 USD × 2.70
        self.assertEqual(scenario.item_cost, Decimal('270.00'))
        # 12 USD × 2.70
        self.assertEqual(scenario.local_shipping, Decimal('32.40'))
        self.assertEqual(scenario.local_tax, Decimal('0.00'))
        # Local cost 302.40 ≥ 300 → declaration required
        self.assertEqual(scenario.calculation_details['import']['local_cost_gel'], '302.40')
        self.assertTrue(scenario.calculation_details['import']['declaration_required'])
        # 1 kg × 27 GEL
        self.assertEqual(scenario.international_shipping, Decimal('27.00'))
        # 2% of international delivery
        self.assertEqual(scenario.payment_fee, Decimal('0.54'))
        # VAT base = 270 + 32.40 + 27 + 0.54 = 329.94 × 0.18
        self.assertEqual(scenario.georgia_vat, Decimal('59.39'))
        self.assertEqual(scenario.customs, Decimal('20.00'))
        self.assertEqual(scenario.declaration_service, Decimal('15.00'))
        # 270 + 32.40 + 27 + 0.54 + 59.39 + 20 + 15
        self.assertEqual(scenario.landed_cost, Decimal('424.33'))

        pricing = scenario.calculation_details['pricing']
        self.assertEqual(pricing['net_sales_coefficient'], '0.837457627')
        self.assertEqual(scenario.break_even_price, Decimal('506.69'))
        self.assertEqual(scenario.price_minimum, Decimal('521.89'))
        self.assertEqual(scenario.price_standard, Decimal('557.36'))
        self.assertEqual(scenario.price_premium, Decimal('633.36'))
        # Ranking uses the standard selling price
        self.assertEqual(scenario.customer_price, Decimal('557.36'))
        self.assertEqual(scenario.chargeable_weight_g, 1000)

    def test_below_threshold_skips_import_charges(self):
        self.offer.current_price = Decimal('80.00')
        self.offer.save()
        scenario = build_cost_scenario(self.offer, self.route)
        # Local cost 216 + 32.40 = 248.40 < 300
        self.assertEqual(scenario.calculation_details['import']['local_cost_gel'], '248.40')
        self.assertFalse(scenario.calculation_details['import']['declaration_required'])
        self.assertEqual(scenario.georgia_vat, Decimal('0.00'))
        self.assertEqual(scenario.customs, Decimal('0.00'))
        self.assertEqual(scenario.declaration_service, Decimal('0.00'))
        # Customer pays: product + local ship + Onex delivery + payment fee
        self.assertEqual(scenario.item_cost, Decimal('216.00'))
        self.assertEqual(scenario.international_shipping, Decimal('27.00'))
        self.assertEqual(scenario.payment_fee, Decimal('0.54'))
        self.assertEqual(scenario.landed_cost, Decimal('275.94'))
        self.assertEqual(scenario.break_even_price, Decimal('329.50'))

    def test_threshold_excludes_international_shipping(self):
        # Local cost just under 300; intl delivery must NOT push it over.
        self.offer.current_price = Decimal('99.00')  # 267.30 + 32.40 = 299.70
        self.offer.save()
        scenario = build_cost_scenario(self.offer, self.route)
        self.assertEqual(scenario.calculation_details['import']['local_cost_gel'], '299.70')
        self.assertFalse(scenario.calculation_details['import']['declaration_required'])
        self.assertEqual(scenario.georgia_vat, Decimal('0.00'))

    def test_free_shipping_threshold_zeroes_local_shipping(self):
        self.offer.free_shipping_threshold = Decimal('90.00')
        self.offer.save()
        scenario = build_cost_scenario(self.offer, self.route)
        self.assertEqual(scenario.local_shipping, Decimal('0.00'))
        # Local cost 270 < 300 → no declaration
        self.assertFalse(scenario.calculation_details['import']['declaration_required'])

    def test_net_coefficient_and_prices_come_from_rules(self):
        # Changing sales tax rules must change the coefficient — never hardcoded.
        CalculationRule.objects.filter(rule_type='sales_vat').update(
            params={'rate': '0.00', 'mode': 'included_in_sale_price'}
        )
        CalculationRule.objects.filter(rule_type='small_business_tax').update(
            params={'rate': '0.00', 'mode': 'percentage_of_gross_sale_price'}
        )
        scenario = build_cost_scenario(self.offer, self.route)
        self.assertEqual(scenario.calculation_details['pricing']['net_sales_coefficient'], '1.000000000')
        self.assertEqual(scenario.break_even_price, scenario.landed_cost)
        self.assertEqual(
            scenario.price_standard,
            (scenario.break_even_price * Decimal('1.10')).quantize(Decimal('0.01')),
        )

    @override_settings(BUYING_NBG_LIVE_FETCH_ON_MISS=False)
    def test_missing_fx_rate_raises(self):
        CalculationRule.objects.filter(rule_type='fx_rate').delete()
        with self.assertRaises(PricingError):
            build_cost_scenario(self.offer, self.route)

    @override_settings(BUYING_NBG_LIVE_FETCH_ON_MISS=False)
    def test_missing_fx_rate_persists_as_blocked_scenario(self):
        CalculationRule.objects.filter(rule_type='fx_rate').delete()
        scenarios = rebuild_scenarios_for_offer(self.offer)
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].status, 'calculation_blocked')
        self.assertIsNone(scenarios[0].rank)

    def test_missing_blocking_rule_blocks_scenario(self):
        CalculationRule.objects.filter(rule_type='georgia_vat').delete()
        scenario = build_cost_scenario(self.offer, self.route)
        self.assertEqual(scenario.status, 'calculation_blocked')
        self.assertIn('blocking:missing_rule:georgia_vat', scenario.warnings)
        self.assertIn('missing_rule:georgia_vat', scenario.calculation_details['blocking_issues'])

    def test_missing_sales_tax_rules_block(self):
        CalculationRule.objects.filter(rule_type='sales_vat').delete()
        scenario = build_cost_scenario(self.offer, self.route)
        self.assertEqual(scenario.status, 'calculation_blocked')
        self.assertIn('blocking:missing_rule:sales_vat', scenario.warnings)

    def test_missing_margin_rule_blocks_scenario(self):
        CalculationRule.objects.filter(rule_type='margin').delete()
        scenario = build_cost_scenario(self.offer, self.route)
        self.assertEqual(scenario.status, 'calculation_blocked')
        self.assertIn('blocking:missing_rule:margin', scenario.warnings)

    def test_missing_non_blocking_rule_degrades_with_warning(self):
        CalculationRule.objects.filter(rule_type='payment_fee').delete()
        scenario = build_cost_scenario(self.offer, self.route)
        self.assertEqual(scenario.status, 'calculated')
        self.assertEqual(scenario.payment_fee, Decimal('0.00'))
        self.assertIn('missing_rule:payment_fee', scenario.warnings)
        self.assertEqual(scenario.confidence, 'estimated')

    def test_blocked_scenarios_are_excluded_from_ranking(self):
        CalculationRule.objects.filter(rule_type='margin').delete()
        scenarios = rebuild_scenarios_for_offer(self.offer)
        self.assertEqual(scenarios[0].status, 'calculation_blocked')
        self.assertIsNone(scenarios[0].rank)
        self.assertEqual(scenarios[0].recommendation, 'review')

    def test_fx_and_coefficient_snapshots_preserved(self):
        scenario = build_cost_scenario(self.offer, self.route)
        fx = scenario.calculation_details['fx']
        self.assertEqual(fx['currency'], 'USD')
        self.assertEqual(fx['rate_gel'], '2.70')
        self.assertFalse(fx['stale'])
        pricing = scenario.calculation_details['pricing']
        self.assertEqual(pricing['net_sales_coefficient'], '0.837457627')
        self.assertEqual(pricing['sales_vat']['params']['rate'], '0.18')
        self.assertEqual(pricing['markups']['standard'], '0.10')

    def test_component_snapshot_keeps_rule_params(self):
        scenario = build_cost_scenario(self.offer, self.route)
        vat = next(c for c in scenario.breakdown if c['code'] == 'georgia_vat')
        self.assertEqual(vat['rule']['params']['rate'], '0.18')
        self.assertEqual(vat['rule']['params']['threshold_base'], 'local_cost')

    def test_weight_falls_back_to_category_rule(self):
        self.offer.weight_g_actual = None
        self.offer.save()
        CalculationRule.objects.create(
            rule_type='weight', name='Racquet shipping parcel',
            category='racquet',
            params={'default_g': '1000'},
        )
        # Offer category comes from normalized product — set request category
        from buying.models import NormalizedProduct, ProductCategory
        np = NormalizedProduct.objects.create(
            brand='Wilson', model_name='Blade', category=ProductCategory.RACQUET,
        )
        self.request_obj.normalized_product = np
        self.request_obj.save()
        scenario = build_cost_scenario(self.offer, self.route)
        self.assertEqual(scenario.chargeable_weight_g, 1000)
        # 1.0 kg × 27 GEL
        self.assertEqual(scenario.international_shipping, Decimal('27.00'))
        self.assertEqual(
            scenario.calculation_details['weight']['shipping_source'], 'configured_rule'
        )

    def test_chargeable_weight_is_max_of_actual_and_volumetric(self):
        self.offer.length_cm = Decimal('40')
        self.offer.width_cm = Decimal('30')
        self.offer.height_cm = Decimal('20')
        self.offer.save()
        # Volumetric = 40×30×20 / 6000 = 4 kg = 4000 g > actual 1000 g
        chargeable, meta = resolve_chargeable_weight(self.offer, '', quantity=1)
        self.assertEqual(chargeable, 4000)
        self.assertEqual(meta['basis'], 'volumetric')
        self.assertEqual(meta['actual_total_g'], 1000)
        self.assertEqual(meta['volumetric_total_g'], 4000)

        scenario = build_cost_scenario(self.offer, self.route)
        self.assertEqual(scenario.chargeable_weight_g, 4000)
        self.assertEqual(scenario.international_shipping, Decimal('108.00'))


class OfferScenarioLifecycleTests(TestCase):
    def setUp(self):
        self.user = make_superuser()
        self.supplier = make_supplier()
        self.route = make_route()
        make_standard_rules(self.route)
        self.request_obj = make_request(self.user)
        self.offer = make_offer(self.request_obj, self.supplier)

    def test_rebuild_creates_ranked_scenarios_and_supersedes_old(self):
        first = rebuild_scenarios_for_offer(self.offer)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].rank, 1)
        self.assertIn('Lowest price', first[0].rank_labels)

        second = rebuild_scenarios_for_offer(self.offer)
        first[0].refresh_from_db()
        self.assertEqual(first[0].status, 'superseded')
        self.assertEqual(first[0].superseded_by_id, second[0].pk)
        self.assertIsNone(first[0].rank)
        self.assertEqual(second[0].status, 'calculated')

    def test_no_matching_route_returns_empty(self):
        other = make_offer(
            self.request_obj,
            make_supplier(name='Tennis Point', code='tp', country='DE', currency='EUR'),
            currency='EUR',
        )
        self.assertEqual(rebuild_scenarios_for_offer(other), [])

    def test_manual_override_creates_new_version_and_audits(self):
        scenario = rebuild_scenarios_for_offer(self.offer)[0]
        updated = override_scenario_component(
            scenario, 'international_shipping', Decimal('50.00'), self.user, 'Onex quote'
        )
        self.assertNotEqual(updated.pk, scenario.pk)
        scenario.refresh_from_db()
        self.assertEqual(scenario.status, 'superseded')
        self.assertEqual(scenario.superseded_by_id, updated.pk)
        self.assertEqual(scenario.international_shipping, Decimal('27.00'))

        self.assertEqual(updated.international_shipping, Decimal('50.00'))
        # Changing an input component correctly recalculates dependents per active rules.
        # Payment fee = 2% of the (overridden) international delivery.
        self.assertEqual(updated.payment_fee, Decimal('1.00'))
        # VAT base = 270 + 32.40 + 50 + 1.00 = 353.40 × 0.18
        self.assertEqual(updated.georgia_vat, Decimal('63.61'))
        # 270 + 32.40 + 50 + 1.00 + 63.61 + 20 + 15
        self.assertEqual(updated.landed_cost, Decimal('452.01'))
        component = next(
            c for c in updated.breakdown if c['code'] == 'international_shipping'
        )
        self.assertEqual(component['source'], 'manual_override')
        self.assertEqual(component['auto_amount_gel'], '27.00')

        from buying.models import ManualOverride

        audit = ManualOverride.objects.get()
        self.assertEqual(audit.kind, 'component')
        self.assertEqual(audit.field, 'international_shipping')
        self.assertEqual(audit.old_value, '27.00')
        self.assertEqual(audit.new_value, '50.00')

        rebuilt = rebuild_scenarios_for_offer(self.offer)[0]
        self.assertEqual(rebuilt.international_shipping, Decimal('50.00'))

    def test_remove_override_restores_automatic_logic(self):
        from buying.models import ManualOverride
        from buying.services.overrides import remove_scenario_override

        scenario = rebuild_scenarios_for_offer(self.offer)[0]
        overridden = override_scenario_component(
            scenario, 'international_shipping', Decimal('50.00'), self.user, 'Onex quote'
        )
        restored = remove_scenario_override(
            overridden, 'international_shipping', self.user, 'Quote was wrong'
        )
        self.assertNotEqual(restored.pk, overridden.pk)
        self.assertEqual(restored.international_shipping, Decimal('27.00'))
        self.assertEqual(restored.calculation_details['overrides'], {})
        overridden.refresh_from_db()
        self.assertEqual(overridden.status, 'superseded')
        removal = ManualOverride.objects.filter(kind='removal').get()
        self.assertEqual(removal.field, 'international_shipping')
        self.assertIsNone(removal.new_value)

    def test_final_price_override_marks_scenario_as_manual(self):
        scenario = rebuild_scenarios_for_offer(self.offer)[0]
        updated = override_scenario_component(
            scenario, 'customer_price', Decimal('600.00'), self.user, 'Client agreement'
        )
        self.assertEqual(updated.customer_price, Decimal('600.00'))
        details = updated.calculation_details
        self.assertTrue(details['manual_price'])
        self.assertEqual(details['auto_customer_price'], '557.36')
        self.assertIn('Manual price', updated.rank_labels)

        from buying.models import ManualOverride

        audit = ManualOverride.objects.get()
        self.assertEqual(audit.kind, 'final_price')

    def test_revise_offer_creates_new_immutable_version(self):
        from buying.models import SupplierOffer
        from buying.services.offers import revise_offer

        old_scenario = rebuild_scenarios_for_offer(self.offer)[0]
        override_scenario_component(
            old_scenario, 'local_shipping', Decimal('20.00'), self.user, 'Checkout check'
        )

        edited = SupplierOffer.objects.get(pk=self.offer.pk)
        edited.current_price = Decimal('120.00')
        new_offer = revise_offer(SupplierOffer.objects.get(pk=self.offer.pk), edited)

        self.assertNotEqual(new_offer.pk, self.offer.pk)
        self.assertEqual(new_offer.version, 2)
        old = SupplierOffer.objects.get(pk=self.offer.pk)
        self.assertEqual(old.current_price, Decimal('100.00'))
        self.assertEqual(old.superseded_by_id, new_offer.pk)
        self.assertFalse(
            old.cost_scenarios.exclude(status='superseded').exists()
        )
        fresh = new_offer.cost_scenarios.get(status='calculated')
        self.assertEqual(fresh.item_cost, Decimal('324.00'))
        self.assertEqual(fresh.local_shipping, Decimal('20.00'))

    def test_ranking_separates_price_and_recommendation(self):
        cheap_offer = self.offer
        cheap_offer.requested_variant_available = False
        cheap_offer.match_status = 'exact'
        cheap_offer.save()
        pricier = make_offer(
            self.request_obj, self.supplier,
            current_price=Decimal('101.00'),
            requested_variant_available=True,
            match_status='exact',
        )
        rebuild_scenarios_for_offer(cheap_offer)
        scenarios = {
            s.supplier_offer_id: s
            for s in rebuild_scenarios_for_offer(pricier)[0].buying_request.cost_scenarios.filter(
                status='calculated'
            )
        }
        cheap = scenarios[cheap_offer.pk]
        cheap.refresh_from_db()
        expensive = scenarios[pricier.pk]
        # Unavailable / non-verified offers are shown but excluded from price ranking
        self.assertIsNone(cheap.rank)
        self.assertEqual(cheap.recommendation, 'not_recommended')
        self.assertTrue(
            any('variant' in (r or '').lower() or 'unavailable' in (r or '').lower()
                for r in cheap.recommendation_reasons)
        )
        self.assertEqual(expensive.rank, 1)
        self.assertEqual(expensive.recommendation, 'recommended')


class SeedOnexCommandTests(TestCase):
    def test_seed_creates_warehouses_routes_and_editable_rules(self):
        from django.core.management import call_command

        call_command('seed_onex')
        from buying.models import FulfillmentProvider, FulfillmentRoute, FulfillmentWarehouse

        provider = FulfillmentProvider.objects.get(code='onex')
        self.assertEqual(FulfillmentWarehouse.objects.filter(provider=provider).count(), 5)
        self.assertEqual(FulfillmentRoute.objects.filter(provider=provider).count(), 5)
        greece = FulfillmentWarehouse.objects.get(provider=provider, country='GR')
        tariff = CalculationRule.objects.get(
            rule_type='international_shipping', warehouse=greece
        )
        self.assertEqual(tariff.params['per_kg'], '11')
        usa = FulfillmentWarehouse.objects.get(provider=provider, country='US')
        self.assertEqual(
            CalculationRule.objects.get(
                rule_type='international_shipping', warehouse=usa
            ).params['per_kg'],
            '27',
        )
        # Idempotent
        call_command('seed_onex')
        self.assertEqual(FulfillmentWarehouse.objects.filter(provider=provider).count(), 5)
