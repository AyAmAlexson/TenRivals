from django.test import TestCase, override_settings


@override_settings(
    DEBUG=False,
    SECURE_SSL_REDIRECT=False,
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class CompanyStoryTests(TestCase):
    def test_page_is_public_and_closed_to_crawlers(self):
        response = self.client.get('/ge_en/shop/pilot/story/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow, noarchive')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertContains(response, 'name="robots" content="noindex, nofollow, noarchive"')
        self.assertContains(response, 'The line for your growth')
        self.assertContains(response, 'Four lines we do not cross')
        self.assertContains(response, 'Why buy at Tennis Rivals')
        self.assertNotContains(response, 'rel="canonical"')

    def test_robots_txt_disallows_the_story(self):
        response = self.client.get('/robots.txt')
        self.assertContains(response, 'Disallow: /ge_en/shop/pilot/')
        self.assertContains(response, 'Disallow: /ge_ru/shop/pilot/')
        self.assertContains(response, 'Disallow: /ge_ka/shop/pilot/')

    def test_sitemap_does_not_list_the_story(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'pilot/story')
