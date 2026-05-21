from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from tenrivals.canonical_host_middleware import CanonicalHostMiddleware


class CanonicalHostMiddlewareTests(SimpleTestCase):
    def _middleware(self):
        return CanonicalHostMiddleware(lambda request: HttpResponse('ok'))

    @override_settings(
        CANONICAL_HOST='tenrivals.com',
        CANONICAL_REDIRECT_WWW=True,
        SECURE_SSL_REDIRECT=True,
    )
    def test_redirects_www_to_apex(self):
        request = RequestFactory().get(
            '/ge_ru/shop/stock/balls/',
            HTTP_HOST='www.tenrivals.com',
            secure=True,
        )
        response = self._middleware()(request)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            'https://tenrivals.com/ge_ru/shop/stock/balls/',
        )

    @override_settings(
        CANONICAL_HOST='tenrivals.com',
        CANONICAL_REDIRECT_WWW=True,
        SECURE_SSL_REDIRECT=True,
    )
    def test_preserves_query_string(self):
        request = RequestFactory().get(
            '/ge_en/shop/stock/?type=BALLS&cbrand=Wilson',
            HTTP_HOST='www.tenrivals.com',
            secure=True,
        )
        response = self._middleware()(request)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            'https://tenrivals.com/ge_en/shop/stock/?type=BALLS&cbrand=Wilson',
        )

    @override_settings(
        CANONICAL_HOST='tenrivals.com',
        CANONICAL_REDIRECT_WWW=True,
        SECURE_SSL_REDIRECT=True,
    )
    def test_apex_host_passes_through(self):
        request = RequestFactory().get(
            '/ge_ru/shop/stock/balls/',
            HTTP_HOST='tenrivals.com',
            secure=True,
        )
        response = self._middleware()(request)
        self.assertEqual(response.status_code, 200)

    @override_settings(
        CANONICAL_HOST='tenrivals.com',
        CANONICAL_REDIRECT_WWW=False,
        SECURE_SSL_REDIRECT=True,
    )
    def test_disabled_when_setting_off(self):
        request = RequestFactory().get(
            '/',
            HTTP_HOST='www.tenrivals.com',
            secure=True,
        )
        response = self._middleware()(request)
        self.assertEqual(response.status_code, 200)
