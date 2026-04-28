"""Custom HTTP error pages (shop-branded, minimal dependencies)."""

from django.shortcuts import render


def page_not_found(request, exception):
    return render(request, 'errors/404.html', status=404)


def server_error(request):
    return render(request, 'errors/500.html', status=500)


def permission_denied(request, exception=None):
    return render(request, 'errors/403.html', status=403)


def csrf_failure(request, reason=''):
    """Branded 403 for CSRF verification failures (see CSRF_FAILURE_VIEW)."""
    return render(request, 'errors/403.html', status=403)


def ratelimit_response(request, exception):
    return render(request, 'errors/429.html', status=429)


def sentry_debug_trigger(request):
    """Intentional error to verify Sentry. Registered only when DEBUG is True."""
    division_by_zero = 1 / 0
