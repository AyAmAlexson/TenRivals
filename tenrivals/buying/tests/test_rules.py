from django.test import TestCase

from buying.engine.rule_handlers import validate_rule_params
from buying.engine.rules import find_conflicting_rules, resolve_rule, resolve_rule_checked
from buying.forms import CalculationRuleForm
from buying.models import CalculationRule

from .utils import make_supplier


class RuleResolutionTests(TestCase):
    def test_most_specific_rule_wins(self):
        supplier = make_supplier(country='DE', currency='EUR', code='tp', name='Tennis Point')
        generic = CalculationRule.objects.create(
            rule_type='local_tax', name='Global fallback', params={'mode': 'rate', 'rate': '0.10'}
        )
        by_country = CalculationRule.objects.create(
            rule_type='local_tax', name='DE VAT', country='DE',
            params={'mode': 'rate', 'rate': '0.19'},
        )
        by_supplier = CalculationRule.objects.create(
            rule_type='local_tax', name='TP special', country='DE', supplier=supplier,
            params={'mode': 'rate', 'rate': '0.16'},
        )

        self.assertEqual(resolve_rule('local_tax'), generic)
        self.assertEqual(resolve_rule('local_tax', country='DE'), by_country)
        self.assertEqual(resolve_rule('local_tax', country='DE', supplier=supplier), by_supplier)

    def test_disabled_and_expired_rules_are_skipped(self):
        import datetime

        from django.utils import timezone

        CalculationRule.objects.create(
            rule_type='margin', name='Disabled', enabled=False,
            params={'minimum': '0.5', 'standard': '0.5', 'premium': '0.5'},
        )
        CalculationRule.objects.create(
            rule_type='margin', name='Expired',
            active_to=timezone.now() - datetime.timedelta(days=1),
            params={'minimum': '0.5', 'standard': '0.5', 'premium': '0.5'},
        )
        self.assertIsNone(resolve_rule('margin'))

    def test_priority_breaks_ties(self):
        low = CalculationRule.objects.create(
            rule_type='margin', name='Low', priority=10,
            params={'minimum': '0.03', 'standard': '0.10', 'premium': '0.25'},
        )
        high = CalculationRule.objects.create(
            rule_type='margin', name='High', priority=50,
            params={'minimum': '0.05', 'standard': '0.12', 'premium': '0.30'},
        )
        self.assertEqual(resolve_rule('margin'), high)
        high.enabled = False
        high.save()
        self.assertEqual(resolve_rule('margin'), low)

    def test_full_tie_is_broken_deterministically_and_reported(self):
        first = CalculationRule.objects.create(
            rule_type='margin', name='A', priority=100,
            params={'minimum': '0.03', 'standard': '0.10', 'premium': '0.25'},
        )
        second = CalculationRule.objects.create(
            rule_type='margin', name='B', priority=100,
            params={'minimum': '0.05', 'standard': '0.12', 'premium': '0.30'},
        )
        # Deterministic tie-breaker: newest version, then newest row — never
        # database order.
        rule, conflict = resolve_rule_checked('margin')
        self.assertEqual(rule, second)
        self.assertTrue(conflict)
        first.version = 5
        first.save()
        rule, conflict = resolve_rule_checked('margin')
        self.assertEqual(rule, first)


class RuleConflictValidationTests(TestCase):
    def test_find_conflicting_rules(self):
        existing = CalculationRule.objects.create(
            rule_type='margin', name='Existing', priority=100,
            params={'minimum': '0.03', 'standard': '0.10', 'premium': '0.25'},
        )
        candidate = CalculationRule(
            rule_type='margin', name='New', priority=100,
            params={'minimum': '0.05', 'standard': '0.12', 'premium': '0.30'},
            enabled=True,
        )
        self.assertIn(existing, find_conflicting_rules(candidate))
        candidate.priority = 50
        self.assertNotIn(existing, find_conflicting_rules(candidate))

    def test_form_refuses_conflicting_active_rule(self):
        CalculationRule.objects.create(
            rule_type='margin', name='Existing', priority=100,
            params={'minimum': '0.03', 'standard': '0.10', 'premium': '0.25'},
        )
        form = CalculationRuleForm(data={
            'rule_type': 'margin', 'name': 'Duplicate', 'priority': 100,
            'params': '{"minimum": "0.05", "standard": "0.12", "premium": "0.30"}',
            'conditions': '{}',
            'version': 1, 'enabled': True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('Conflicts with active rule', str(form.errors))

    def test_form_allows_same_scope_with_different_priority(self):
        CalculationRule.objects.create(
            rule_type='margin', name='Existing', priority=100,
            params={'minimum': '0.03', 'standard': '0.10', 'premium': '0.25'},
        )
        form = CalculationRuleForm(data={
            'rule_type': 'margin', 'name': 'Fallback', 'priority': 50,
            'params': '{"minimum": "0.05", "standard": "0.12", "premium": "0.30"}',
            'conditions': '{}',
            'version': 1, 'enabled': True,
        })
        self.assertTrue(form.is_valid(), form.errors)

    def test_fx_rules_conflict_only_within_same_currency(self):
        CalculationRule.objects.create(
            rule_type='fx_rate', name='USD', priority=100,
            params={'currency': 'USD', 'rate_gel': '2.70'},
        )
        eur_form = CalculationRuleForm(data={
            'rule_type': 'fx_rate', 'name': 'EUR', 'priority': 100,
            'params': '{"currency": "EUR", "rate_gel": "2.95"}', 'conditions': '{}',
            'version': 1, 'enabled': True,
        })
        self.assertTrue(eur_form.is_valid(), eur_form.errors)
        usd_form = CalculationRuleForm(data={
            'rule_type': 'fx_rate', 'name': 'USD again', 'priority': 100,
            'params': '{"currency": "USD", "rate_gel": "2.80"}', 'conditions': '{}',
            'version': 1, 'enabled': True,
        })
        self.assertFalse(usd_form.is_valid())


class RuleParamsValidationTests(TestCase):
    def test_valid_params_pass(self):
        self.assertEqual(
            validate_rule_params('fx_rate', {'currency': 'USD', 'rate_gel': '2.70'}), []
        )
        self.assertEqual(
            validate_rule_params(
                'margin', {'minimum': '0.03', 'standard': '0.10', 'premium': '0.25'}
            ),
            [],
        )
        self.assertEqual(
            validate_rule_params(
                'international_shipping',
                {'currency': 'USD', 'per_kg': '8.5', 'min_kg': '0.5', 'step_kg': '0.5'},
            ),
            [],
        )

    def test_invalid_params_are_rejected_at_validation_time(self):
        self.assertTrue(validate_rule_params('fx_rate', {'currency': 'DOLLARS'}))
        self.assertTrue(validate_rule_params('fx_rate', {'currency': 'USD', 'rate_gel': 'abc'}))
        self.assertTrue(validate_rule_params('margin', {}))
        self.assertTrue(validate_rule_params('margin', {'minimum': '0.03'}))
        self.assertTrue(validate_rule_params('georgia_vat', {'rate': '0.18', 'base': 'nonsense'}))
        self.assertTrue(validate_rule_params('sales_vat', {'rate': '0.18', 'mode': 'wrong'}))
        self.assertTrue(validate_rule_params('local_tax', {'mode': 'wat'}))
        self.assertTrue(validate_rule_params('rounding', {}))

    def test_form_rejects_invalid_params(self):
        form = CalculationRuleForm(data={
            'rule_type': 'fx_rate', 'name': 'Broken', 'priority': 100,
            'params': '{"currency": "USD"}', 'conditions': '{}',
            'version': 1, 'enabled': True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('rate_gel', str(form.errors['params']))
