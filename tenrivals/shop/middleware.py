import logging
import os

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import translation

from .site_locale import (
    ACTIVE_SHOP_SITE_LOCALES,
    django_lang_for_site_locale,
    set_session_site_locale,
)


logger = logging.getLogger(__name__)


# Meta / Facebook crawlers were flooding filter combinations + /shop/set-lang/
# redirects and pushing the single Basic dyno into H12 timeouts.
_ABUSIVE_BOT_MARKERS = (
    'meta-webindexer',
    'meta-externalagent',
    'facebookexternalhit',
    'facebot',
)


def _query_looks_like_filter_spam(qs: str) -> bool:
    """Detect fuzzed catalog filters like cbrand=Waterdrop////////////////."""
    if not qs:
        return False
    low = qs.lower()
    if '//' in qs:
        return True
    # URL-encoded and double-encoded slash runs
    if '%2f%2f' in low or '%252f' in low:
        return True
    # Absurdly long query strings are never legitimate catalog filters.
    if len(qs) > 400:
        return True
    return False


class AbuseShieldMiddleware:
    """Cheap early rejects so crawl spam cannot saturate Gunicorn workers."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        qs = request.META.get('QUERY_STRING') or ''
        if _query_looks_like_filter_spam(qs):
            return HttpResponse(status=404)

        path = getattr(request, 'path_info', '') or ''
        ua = (request.META.get('HTTP_USER_AGENT') or '').lower()
        is_meta_bot = any(m in ua for m in _ABUSIVE_BOT_MARKERS)

        # Locale switcher is linked on every page; bots fan it out into redirect storms.
        if path.startswith('/shop/set-lang/') and is_meta_bot:
            return HttpResponse(status=403)

        # Meta was enumerating every racket/shoe filter combo across locales.
        # Allow clean category URLs; reject query-filter fan-out from these bots.
        if is_meta_bot and qs:
            low = qs.lower()
            if any(
                token in low
                for token in ('cbrand=', 'w=', 'head=', 'pat=', 'surf=', 'type=', 'brand=')
            ):
                return HttpResponse(status=429)

        return self.get_response(request)


class SiteLocaleMiddleware:
    """Parses /<site_locale>/shop/..., syncs session, activates gettext per request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.site_locale = None
        path = getattr(request, 'path_info', '') or ''
        parts = path.strip('/').split('/')
        if (
            len(parts) >= 2
            and parts[1] == 'shop'
            and parts[0] in ACTIVE_SHOP_SITE_LOCALES
        ):
            sl = parts[0]
            request.site_locale = sl
            set_session_site_locale(request, sl)
            lang = django_lang_for_site_locale(sl)
            translation.activate(lang)
            try:
                return self.get_response(request)
            finally:
                translation.deactivate()
        return self.get_response(request)


ALLOWED_PATHS = (
    '/accounts/login/',
    '/accounts/signup/',
    '/accounts/logout/',
    '/admin/',
    '/static/',
    '/media/',
)


class MaintenanceModeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if os.environ.get('MAINTENANCE_MODE', '').lower() == 'true':
            allowed = self._is_allowed(request)
            has_user = hasattr(request, 'user')
            logger.warning(
                'MAINT path=%s has_user=%s user=%s auth=%s staff=%s super=%s allowed=%s',
                request.path,
                has_user,
                getattr(request, 'user', '?'),
                getattr(request.user, 'is_authenticated', '?') if has_user else '?',
                getattr(request.user, 'is_staff', '?') if has_user else '?',
                getattr(request.user, 'is_superuser', '?') if has_user else '?',
                allowed,
            )
            if not allowed:
                html = render_to_string('maintenance.html', request=request)
                return HttpResponse(html, status=503, headers={'Retry-After': '86400'})
        return self.get_response(request)

    def _is_allowed(self, request):
        if hasattr(request, 'user') and request.user.is_authenticated:
            if request.user.is_staff or request.user.is_superuser:
                return True
        return any(request.path.startswith(p) for p in ALLOWED_PATHS)
