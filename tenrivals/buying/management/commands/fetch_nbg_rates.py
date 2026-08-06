"""Fetch official NBG FX rates into the FxRate table.

Examples:
  python manage.py fetch_nbg_rates
  python manage.py fetch_nbg_rates --date 2026-08-05
  python manage.py fetch_nbg_rates --force
  python manage.py fetch_nbg_rates --currencies USD,EUR
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from buying.integrations.nbg import NbgApiError
from buying.services.fx_rates import sync_nbg_rates


class Command(BaseCommand):
    help = 'Fetch and store official National Bank of Georgia FX rates.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date', dest='rate_date', default=None,
            help='Rate date YYYY-MM-DD (default: today in Asia/Tbilisi)',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Fetch even when today\'s rates are already stored',
        )
        parser.add_argument(
            '--currencies', default=None,
            help='Comma-separated ISO codes (default: BUYING_NBG_CURRENCIES)',
        )

    def handle(self, *args, **options):
        rate_date = None
        if options['rate_date']:
            try:
                rate_date = date.fromisoformat(options['rate_date'])
            except ValueError as exc:
                raise CommandError(f'Invalid --date: {exc}') from exc
        currencies = None
        if options['currencies']:
            currencies = [c.strip().upper() for c in options['currencies'].split(',') if c.strip()]

        try:
            stats = sync_nbg_rates(
                rate_date=rate_date,
                currencies=currencies,
                force=options['force'],
            )
        except NbgApiError as exc:
            raise CommandError(str(exc)) from exc

        if stats.get('skipped'):
            self.stdout.write(self.style.WARNING(
                f"Skipped — rates for {stats['rate_date']} already present. Use --force to refresh."
            ))
            return
        self.stdout.write(self.style.SUCCESS(
            f"NBG rates stored for {stats['rate_date']}: "
            f"+{stats['created']} created, {stats['updated']} updated, "
            f"{stats['total']} total."
        ))
