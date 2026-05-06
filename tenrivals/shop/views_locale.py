"""Redirects and language picker for storefront locale prefix."""

from __future__ import annotations

from django.http import Http404
from django.shortcuts import redirect

from persons.models import CustomUser

from .site_locale import (
    ACTIVE_SHOP_SITE_LOCALES,
    replace_shop_path_site_locale,
    resolve_redirect_site_locale,
    shop_reverse,
    set_session_site_locale,
)


def shop_root_locale_redirect(request):
    loc = resolve_redirect_site_locale(request)
    url = shop_reverse('shop:index', site_locale=loc)
    if request.GET:
        url += '?' + request.GET.urlencode()
    return redirect(url)


def shop_legacy_path_redirect(request, path_rest: str | None = None):
    """Send /shop/... to /<resolved_locale>/shop/... preserving path and GET params."""
    loc = resolve_redirect_site_locale(request)
    tail = (path_rest or '').strip('/')
    if tail:
        target = f'/{loc}/shop/{tail}/'
    else:
        target = shop_reverse('shop:index', site_locale=loc)
    if request.GET:
        sep = '&' if '?' in target else '?'
        target += sep + request.GET.urlencode()
    return redirect(target)


def set_shop_site_locale(request, site_locale: str):
    """GET: set session (+ authenticated user preference) and redirect to same logical shop URL."""
    raw = (site_locale or '').strip()
    if raw not in ACTIVE_SHOP_SITE_LOCALES:
        raise Http404()
    set_session_site_locale(request, raw)
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        CustomUser.objects.filter(pk=user.pk).update(preferred_site_locale=raw)
    nxt = (request.GET.get('next') or '').strip()
    if not nxt.startswith('/') or '//' in nxt:
        target = shop_reverse('shop:index', site_locale=raw)
    else:
        target = replace_shop_path_site_locale(nxt, raw)
    return redirect(target)

