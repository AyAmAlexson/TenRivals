"""Custom HTTP error pages (shop-branded, minimal dependencies)."""

from django.shortcuts import render


def page_not_found(request, exception):
    return render(request, 'errors/404.html', status=404)


def server_error(request):
    return render(request, 'errors/500.html', status=500)


def ratelimit_response(request, exception):
    return render(request, 'errors/429.html', status=429)
