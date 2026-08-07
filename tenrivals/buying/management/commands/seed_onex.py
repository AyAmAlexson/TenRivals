"""Seed the Phase 2 Onex fulfillment data and pricing rules.

Idempotent: safe to run repeatedly. Everything created here is plain data —
providers, warehouses, routes and CalculationRules — fully editable afterwards
through the staff UI (nothing is hardcoded in the engine).

FX rates are NOT seeded: add current NBG rates as "FX rate" pricing rules
before calculating scenarios.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from buying.engine.rule_handlers import validate_rule_params
from buying.models import (
    CalculationRule,
    FulfillmentProvider,
    FulfillmentRoute,
    FulfillmentWarehouse,
    FxRate,
)

RuleType = CalculationRule.RuleType

# country ISO, human name, warehouse currency, Onex delivery rate GEL/kg
WAREHOUSES = [
    ('US', 'USA', 'USD', '27'),
    ('DE', 'Germany', 'EUR', '27'),
    ('GB', 'UK', 'GBP', '27'),
    ('CN', 'China', 'USD', '27'),
    ('GR', 'Greece', 'EUR', '11'),
]


class Command(BaseCommand):
    help = 'Create/refresh Onex fulfillment data and Phase 2 pricing rules (idempotent).'

    @transaction.atomic
    def handle(self, *args, **options):
        created_counts = {'warehouses': 0, 'routes': 0, 'rules': 0}

        provider, created = FulfillmentProvider.objects.get_or_create(
            code='onex', defaults={'name': 'Onex', 'website': 'https://onex.ge'}
        )
        if created:
            self.stdout.write('Created FulfillmentProvider Onex')

        for country, label, currency, per_kg in WAREHOUSES:
            warehouse, created = FulfillmentWarehouse.objects.get_or_create(
                provider=provider, country=country, defaults={'currency': currency}
            )
            created_counts['warehouses'] += created

            _, created = FulfillmentRoute.objects.get_or_create(
                provider=provider,
                warehouse=warehouse,
                origin_country=country,
                supplier=None,
                category='',
                defaults={
                    'name': f'{label} shops → Onex → Georgia',
                    'delivery_method': FulfillmentRoute.DeliveryMethod.AIR,
                },
            )
            created_counts['routes'] += created

            created_counts['rules'] += self._rule(
                RuleType.INTERNATIONAL_SHIPPING,
                f'Onex {label} delivery',
                {'currency': 'GEL', 'per_kg': per_kg},
                provider=provider,
                warehouse=warehouse,
            )

        global_rules = [
            (RuleType.PAYMENT_FEE, 'Onex payment fee',
             {'rate': '0.02', 'base': 'international_shipping'}, {'provider': provider}),
            (RuleType.DECLARATION_SERVICE, 'Onex declaration service',
             {'fixed_gel': '15'}, {'provider': provider}),
            (RuleType.GEORGIA_VAT, 'Georgian import VAT',
             {'rate': '0.18', 'threshold_gel': '300', 'threshold_base': 'local_cost',
              'base': 'local_cost_plus_intl_plus_fee'}, {}),
            (RuleType.CUSTOMS, 'Customs declaration fee',
             {'fixed_gel': '20', 'applies': 'when_declared'}, {}),
            (RuleType.SALES_VAT, 'Sales VAT',
             {'rate': '0.18', 'mode': 'included_in_sale_price'}, {}),
            (RuleType.SMALL_BUSINESS_TAX, 'Small business tax',
             {'rate': '0.01', 'mode': 'percentage_of_gross_sale_price'}, {}),
            (RuleType.MARGIN, 'Selling price markups',
             {'minimum': '0.03', 'standard': '0.10', 'premium': '0.25'}, {}),
            (RuleType.VOLUMETRIC_WEIGHT, 'Volumetric weight (standard formula)',
             {'divisor': '6000'}, {}),
            # Shipping Weight Engine: full parcel estimate (never product unstrung grams)
            (RuleType.WEIGHT, 'Racquet shipping weight (parcel)',
             {'default_g': '1000'}, {'category': 'racquet'}),
            (RuleType.WEIGHT, 'Shoes shipping weight (parcel)',
             {'default_g': '1500'}, {'category': 'shoes'}),
        ]
        for rule_type, name, params, scope in global_rules:
            created_counts['rules'] += self._rule(rule_type, name, params, **scope)

        # Disable obsolete packaging-only racquet rule if a parcel default exists
        if CalculationRule.objects.filter(
            rule_type=RuleType.WEIGHT, category='racquet', enabled=True,
            name='Racquet shipping weight (parcel)',
        ).exists():
            disabled = CalculationRule.objects.filter(
                rule_type=RuleType.WEIGHT,
                name='Racquet packaging allowance',
                enabled=True,
            ).update(enabled=False)
            if disabled:
                self.stdout.write('Disabled obsolete rule: Racquet packaging allowance')

        self.stdout.write(self.style.SUCCESS(
            'Onex seed done: +{warehouses} warehouse(s), +{routes} route(s), '
            '+{rules} rule(s) (existing records untouched).'.format(**created_counts)
        ))
        if not CalculationRule.objects.filter(
            rule_type=RuleType.FX_RATE, enabled=True
        ).exists() and not FxRate.objects.exists():
            self.stdout.write(self.style.WARNING(
                'No FX rates yet — run "manage.py fetch_nbg_rates" '
                '(or add a manual "FX rate" pricing rule) before pricing.'
            ))

    def _rule(self, rule_type, name, params, provider=None, warehouse=None, category='') -> bool:
        errors = validate_rule_params(rule_type, params)
        if errors:  # guards against typos in this file, not user input
            raise ValueError(f'Invalid params for {name}: {errors}')
        _, created = CalculationRule.objects.get_or_create(
            rule_type=rule_type,
            name=name,
            provider=provider,
            warehouse=warehouse,
            category=category or '',
            defaults={'params': params},
        )
        if created:
            self.stdout.write(f'Created rule: {name}')
        return created
