from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from buying.models import BuyingRequest

from .utils import make_superuser


@override_settings(SECURE_SSL_REDIRECT=False)
class BuyingPermissionTests(TestCase):
    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse('administration:buying_requests'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_user_without_permission_gets_403(self):
        get_user_model().objects.create_user(email='u@tenrivals.test', password='x')
        self.client.login(email='u@tenrivals.test', password='x')
        response = self.client.get(reverse('administration:buying_requests'))
        self.assertEqual(response.status_code, 403)

    def test_superuser_has_access(self):
        make_superuser()
        self.client.login(email='boss@tenrivals.test', password='x')
        for url_name in (
            'buying_requests',
            'buying_request_new',
            'buying_suppliers',
            'buying_connectors',
            'buying_ai_diagnostics',
            'buying_mappings',
            'buying_routes',
            'buying_pricing_rules',
            'buying_fx_rates',
            'buying_optimization_rules',
        ):
            response = self.client.get(reverse(f'administration:{url_name}'))
            self.assertEqual(response.status_code, 200, url_name)


@override_settings(SECURE_SSL_REDIRECT=False)
class BuyingRequestFlowTests(TestCase):
    def setUp(self):
        self.user = make_superuser()
        self.client.login(email='boss@tenrivals.test', password='x')

    def test_create_request_and_open_detail(self):
        response = self.client.post(
            reverse('administration:buying_request_new'),
            {'original_query': 'ASICS Gel Resolution X Clay, men, white, EU 44', 'quantity': 1},
        )
        buying_request = BuyingRequest.objects.get()
        self.assertRedirects(
            response,
            reverse('administration:buying_request_detail', args=[buying_request.pk]),
        )
        self.assertEqual(buying_request.status, BuyingRequest.Status.DRAFT)
        self.assertEqual(buying_request.staff_user, self.user)

        detail = self.client.get(
            reverse('administration:buying_request_detail', args=[buying_request.pk])
        )
        self.assertContains(detail, 'ASICS Gel Resolution X Clay')

    def test_manual_normalized_save_sets_status(self):
        buying_request = BuyingRequest.objects.create(
            original_query='Blade 100 V10, grip 3', staff_user=self.user
        )
        response = self.client.post(
            reverse('administration:buying_request_detail', args=[buying_request.pk]),
            {
                'action': 'save_normalized',
                'brand': 'Wilson',
                'model_name': 'Blade 100',
                'generation': 'V10',
                'category': 'racquet',
                'color_policy': 'optional',
                'quantity': 1,
                'required_attributes': '[]',
                'optional_attributes': '[]',
                'aliases': '[]',
            },
        )
        self.assertEqual(response.status_code, 302)
        buying_request.refresh_from_db()
        self.assertEqual(buying_request.status, BuyingRequest.Status.NORMALIZED)
        self.assertTrue(buying_request.normalized_product.edited_by_staff)
        self.assertEqual(buying_request.normalized_product.brand, 'Wilson')
