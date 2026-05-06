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
