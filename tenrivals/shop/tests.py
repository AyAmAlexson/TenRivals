from decimal import Decimal
from unittest.mock import MagicMock

from django.test import TestCase

from shop.promo_codes import PromoEvaluation


class PromoEvaluationPropertyTests(TestCase):
    def test_promo_id_none_when_no_promo(self):
        ev = PromoEvaluation(True, Decimal('1.00'), '', None, Decimal('10.00'), [])
        self.assertIsNone(ev.promo_id)

    def test_promo_id_from_promo_instance(self):
        promo = MagicMock()
        promo.pk = 42
        ev = PromoEvaluation(True, Decimal('2.00'), '', promo, Decimal('10.00'), [0])
        self.assertEqual(ev.promo_id, 42)
