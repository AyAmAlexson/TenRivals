"""seed_suppliers command tests."""

from django.core.management import call_command
from django.test import TestCase

from buying.models import OnexApplicability, Supplier


class SeedSuppliersTests(TestCase):
    def test_seeds_twenty_suppliers(self):
        call_command('seed_suppliers')
        self.assertEqual(Supplier.objects.count(), 20)
        tp = Supplier.objects.get(code='tennis-point-com')
        self.assertEqual(tp.onex_applicability, OnexApplicability.UNSUPPORTED)
        tw = Supplier.objects.get(code='tennis-warehouse-eu')
        self.assertEqual(tw.onex_applicability, OnexApplicability.SUPPORTED)
        call_command('seed_suppliers')
        self.assertEqual(Supplier.objects.count(), 20)
