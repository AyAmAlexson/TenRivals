"""Seed the Phase 3 supplier registry (20 confirmed tennis stores).

Idempotent. Sets Onex warehouse country on Supplier.country and onex_applicability
per the Phase 3 plan.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from buying.models import OnexApplicability, Supplier, TaxDisplayMode

# code, name, base_url, country, currency, tax, onex_applicability, notes
SUPPLIERS = [
    (
        'tennis-warehouse-eu',
        'Tennis Warehouse Europe',
        'https://www.tenniswarehouse-europe.com/',
        'DE', 'EUR', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.SUPPORTED,
        'HQ Europe; Onex DE warehouse',
    ),
    (
        'tennis-point-de',
        'Tennis-Point Germany',
        'https://www.tennis-point.de/',
        'DE', 'EUR', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.SUPPORTED,
        'German storefront; Onex DE',
    ),
    (
        'tennis-point-com',
        'Tennis-Point International',
        'https://www.tennis-point.com/',
        'DE', 'EUR', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.UNSUPPORTED,
        'International delivery only — not applicable to Onex local-delivery model',
    ),
    (
        'itf-tennis-point',
        'ITF Tennis Point',
        'https://www.itf-tennis-point.com/itf/',
        'DE', 'EUR', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.SUPPORTED,
        'Authenticated pricing; set BUYING_ITF_TENNIS_POINT_USERNAME/PASSWORD',
    ),
    (
        'tennis-warehouse-us',
        'Tennis Warehouse US',
        'https://www.tennis-warehouse.com/',
        'US', 'USD', TaxDisplayMode.SALES_TAX_AT_CHECKOUT, OnexApplicability.SUPPORTED,
        'Onex US warehouse',
    ),
    (
        'midwest-racquet-sports',
        'Midwest Racquet Sports',
        'https://www.midwestracquetsports.com/',
        'US', 'USD', TaxDisplayMode.SALES_TAX_AT_CHECKOUT, OnexApplicability.SUPPORTED,
        '',
    ),
    (
        'tennis-express',
        'Tennis Express',
        'https://tennisexpress.com/',
        'US', 'USD', TaxDisplayMode.SALES_TAX_AT_CHECKOUT, OnexApplicability.SUPPORTED,
        '',
    ),
    (
        'ole-tennis',
        'Ole Tennis',
        'https://oletennis.com/',
        'US', 'USD', TaxDisplayMode.SALES_TAX_AT_CHECKOUT, OnexApplicability.SUPPORTED,
        '',
    ),
    (
        'holabird-sports',
        'Holabird Sports',
        'https://www.holabirdsports.com/',
        'US', 'USD', TaxDisplayMode.SALES_TAX_AT_CHECKOUT, OnexApplicability.SUPPORTED,
        '',
    ),
    (
        'saburi-sports',
        'Saburi Sports',
        'https://saburisports.com/',
        'US', 'USD', TaxDisplayMode.SALES_TAX_AT_CHECKOUT, OnexApplicability.SUPPORTED,
        '',
    ),
    (
        'smashinn',
        'Smashinn',
        'https://www.tradeinn.com/smashinn/',
        'DE', 'EUR', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.MANUAL_REVIEW,
        'Tradeinn network — confirm Onex receiving country before enabling scenarios',
    ),
    (
        'tennispro-eu',
        'TennisPro EU',
        'https://www.tennispro.eu/',
        'DE', 'EUR', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.MANUAL_REVIEW,
        'Confirm local delivery country for Onex',
    ),
    (
        'mister-tennis',
        'Mister Tennis',
        'https://www.mistertennis.com/',
        'DE', 'EUR', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.MANUAL_REVIEW,
        'Confirm local delivery country for Onex',
    ),
    (
        'passa-sports',
        'Passa Sports',
        'https://www.passasports.com/',
        'DE', 'EUR', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.MANUAL_REVIEW,
        'Confirm local delivery country for Onex',
    ),
    (
        'extreme-tennis',
        'Extreme Tennis',
        'https://www.extreme-tennis.eu/',
        'DE', 'EUR', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.MANUAL_REVIEW,
        'Confirm local delivery country for Onex',
    ),
    (
        'm1-tennis',
        'M1 Tennis',
        'https://www.m1tennis.com/',
        'DE', 'EUR', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.MANUAL_REVIEW,
        'Confirm local delivery country for Onex',
    ),
    (
        'direct-tennis',
        'Direct Tennis',
        'https://www.directtennis.co.uk/',
        'GB', 'GBP', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.SUPPORTED,
        'Onex UK warehouse',
    ),
    (
        'central-tennis',
        'Central Tennis',
        'https://centraltennis.co.uk/',
        'GB', 'GBP', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.SUPPORTED,
        'Authenticated pricing; set BUYING_CENTRAL_TENNIS_USERNAME/PASSWORD',
    ),
    (
        'tennis-nuts',
        'Tennis Nuts',
        'https://www.tennisnuts.com/',
        'GB', 'GBP', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.SUPPORTED,
        '',
    ),
    (
        'prodirect-sport',
        'Pro:Direct Sport',
        'https://www.prodirectsport.com/',
        'GB', 'GBP', TaxDisplayMode.VAT_INCLUDED, OnexApplicability.SUPPORTED,
        '',
    ),
]


class Command(BaseCommand):
    help = 'Create/refresh the Phase 3 supplier registry (idempotent).'

    @transaction.atomic
    def handle(self, *args, **options):
        created = updated = 0
        for code, name, url, country, currency, tax, onex, notes in SUPPLIERS:
            obj, was_created = Supplier.objects.update_or_create(
                code=code,
                defaults={
                    'name': name,
                    'base_url': url,
                    'country': country,
                    'currency': currency,
                    'connector_class': code,
                    'enabled': True,
                    'tax_display_mode': tax,
                    'onex_applicability': onex,
                    'default_destination_country': country,
                    'notes': notes,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
        self.stdout.write(self.style.SUCCESS(
            f'Seed suppliers done: {created} created, {updated} updated, '
            f'{len(SUPPLIERS)} total in catalogue.'
        ))
