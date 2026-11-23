from urllib.parse import unquote

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


@override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False)
class HomeReactPreviewTests(TestCase):
    def test_anonymous_is_sent_to_login(self):
        response = self.client.get('/ge_en/shop/pilot/home/')
        self.assertEqual(response.status_code, 302)
        location = unquote(response['Location'])
        self.assertIn('login', location)
        self.assertIn('/ge_en/shop/pilot/home/', location)

    def test_logged_in_page_is_closed_to_crawlers(self):
        user = get_user_model().objects.create_user(
            email='home-preview@example.com',
            password='secret-secret',
            first_name='Preview',
            last_name='User',
        )
        self.client.force_login(user)
        response = self.client.get('/ge_en/shop/pilot/home/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow, noarchive')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertContains(response, 'name="robots" content="noindex, nofollow, noarchive"')
        self.assertContains(response, 'id="home-preview-root"')
        self.assertContains(response, 'id="home-preview-data"')
        self.assertContains(response, 'Pre-order gear from the US and EU')
        self.assertContains(response, 'Order for me')
        self.assertContains(response, 'Delivery and payment')
        self.assertNotContains(response, 'Browse preorder')
        self.assertNotContains(response, 'rel="canonical"')

    def test_robots_txt_disallows_the_preview(self):
        response = self.client.get('/robots.txt')
        self.assertContains(response, 'Disallow: /ge_en/shop/pilot/')
        self.assertContains(response, 'Disallow: /ge_ru/shop/pilot/')
        self.assertContains(response, 'Disallow: /ge_ka/shop/pilot/')
