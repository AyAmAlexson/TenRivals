"""Redirect www host to the configured apex canonical host (301)."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponsePermanentRedirect


def _host_without_port(host: str) -> str:
    return (host or '').split(':', 1)[0].lower()


class CanonicalHostMiddleware:
    """Permanent redirect www.<CANONICAL_HOST> → <CANONICAL_HOST>."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.canonical_host = _host_without_port(
            getattr(settings, 'CANONICAL_HOST', 'tenrivals.com')
        )
        self.enabled = bool(getattr(settings, 'CANONICAL_REDIRECT_WWW', False))

    def __call__(self, request):
        if self.enabled:
            host = _host_without_port(request.get_host())
            if host == f'www.{self.canonical_host}':
                scheme = 'https' if request.is_secure() else 'http'
                if getattr(settings, 'SECURE_SSL_REDIRECT', False):
                    scheme = 'https'
                target = f'{scheme}://{self.canonical_host}{request.get_full_path()}'
                return HttpResponsePermanentRedirect(target)
        return self.get_response(request)
