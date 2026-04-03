import os

from django.http import HttpResponse
from django.template.loader import render_to_string


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
            if not self._is_allowed(request):
                html = render_to_string('maintenance.html', request=request)
                return HttpResponse(html, status=503, headers={'Retry-After': '86400'})
        return self.get_response(request)

    def _is_allowed(self, request):
        if hasattr(request, 'user') and request.user.is_authenticated:
            if request.user.is_staff or request.user.is_superuser:
                return True
        return any(request.path.startswith(p) for p in ALLOWED_PATHS)
