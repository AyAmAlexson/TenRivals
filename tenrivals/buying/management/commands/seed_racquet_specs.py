"""Seed maintained racquet specifications for Buying enrichment."""

from django.core.management.base import BaseCommand
from django.db import transaction

from buying.models import CanonicalProduct, ProductCategory, RacquetSpecification

# brand, model_family, generation, variant, head, weight, pattern, aliases
SPECS = [
    ('Babolat', 'Pure Drive', '2025', '', 100, 300, '16x19',
     ['Pure Drive 100', 'Pure Drive 100 2025', 'PD 100 2025']),
    ('Babolat', 'Pure Drive', '2025', 'Team', 100, 285, '16x19',
     ['Pure Drive Team', 'Pure Drive Team 2025']),
    ('Babolat', 'Pure Drive', '2025', 'Lite', 100, 270, '16x19',
     ['Pure Drive Lite', 'Pure Drive Lite 2025']),
    ('Babolat', 'Pure Drive', '2025', '', 107, 280, '16x19',
     ['Pure Drive 107', 'Pure Drive 107 2025']),
    ('Babolat', 'Pure Drive', '2025', 'Plus', 100, 300, '16x19',
     ['Pure Drive Plus', 'Pure Drive +']),
    ('Wilson', 'Blade', 'v10', '', 100, 300, '16x19',
     ['Blade 100', 'Blade 100 V10', 'Wilson Blade 100 v10']),
    ('Wilson', 'Blade', 'v9', '', 98, 305, '16x19',
     ['Blade 98', 'Blade 98 16x19', 'Blade 98 16x19 v9', 'Wilson Blade 98 v9']),
    ('Wilson', 'Blade', 'v9', '', 98, 305, '18x20',
     ['Blade 98 18x20', 'Blade 98 18x20 v9']),
    ('Head', 'Speed MP', '2024', '', 100, 300, '16x19',
     ['Speed MP', 'Head Speed MP 2024']),
    ('Yonex', 'EZONE', '2025', '', 98, 305, '16x19',
     ['EZONE 98', 'Yonex EZONE 98']),
    ('Yonex', 'EZONE', '2025', '', 100, 300, '16x19',
     ['EZONE 100', 'Yonex EZONE 100']),
]


class Command(BaseCommand):
    help = 'Seed racquet specifications used by Buying enrichment (idempotent).'

    @transaction.atomic
    def handle(self, *args, **options):
        created = updated = 0
        for brand, family, generation, variant, head, weight, pattern, aliases in SPECS:
            canon, _ = CanonicalProduct.objects.get_or_create(
                brand=brand,
                model_name=family,
                generation=generation,
                category=ProductCategory.RACQUET,
                defaults={'aliases': aliases},
            )
            obj, was_created = RacquetSpecification.objects.update_or_create(
                brand=brand,
                model_family=family,
                generation=generation,
                variant=variant,
                head_size_sqin=head,
                string_pattern=pattern,
                defaults={
                    'weight_g_unstrung': weight,
                    'aliases': aliases,
                    'canonical_product': canon,
                    'enabled': True,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
        self.stdout.write(self.style.SUCCESS(
            f'Racquet specs: {created} created, {updated} updated, {len(SPECS)} catalogue rows.'
        ))
