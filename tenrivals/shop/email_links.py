"""Build absolute http(s) URLs for HTML emails (many clients ignore relative src)."""

from __future__ import annotations

from django.conf import settings


def absolute_url_for_email(path_or_url: str, request=None) -> str:
    s = (path_or_url or '').strip()
    if not s:
        return ''
    low = s.lower()
    if low.startswith(('http://', 'https://')):
        return s
    if s.startswith('//'):
        scheme = getattr(request, 'scheme', None) or 'https'
        return f'{scheme}:{s}'
    website = (getattr(settings, 'WEBSITE_URL', '') or '').rstrip('/')
    if s.startswith('/') and website:
        return f'{website}{s}'
    if request is not None and s.startswith('/'):
        return request.build_absolute_uri(s)
    if s.startswith('/'):
        try:
            from django.contrib.sites.models import Site

            dom = Site.objects.get_current().domain.split(':')[0]
            return f'https://{dom}{s}'
        except Exception:
            return s
    return s
