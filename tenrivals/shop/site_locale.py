"""
Site storefront locale: URL segment <country>_<lang> (e.g. ge_en) and Django gettext language.
ACTIVE_SHOP_SITE_LOCALES is wired in urlpatterns; model field allows SITE_LOCALE_CHOICES_FULL for rollout.
"""

from __future__ import annotations

from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

# Declared first for urlpatterns regex
ACTIVE_SHOP_SITE_LOCALES = frozenset({'ge_en', 'ge_ru', 'ge_ka'})
ACTIVE_SITE_LOCALE_PATTERN = 'ge_en|ge_ru|ge_ka'

DEFAULT_SHOP_SITE_LOCALE = 'ge_en'

_SESSION_KEY = 'shop_site_locale'


# Labels for Django admin / model choices (compact codes for future locales)
SITE_LOCALE_CHOICES_FULL: tuple[tuple[str, str], ...] = (
    ('ge_en', _('Georgia · English')),
    ('ge_ru', _('Georgia · Russian')),
    ('ge_ka', _('Georgia · Georgian')),
    ('ge_es', _('Georgia · Spanish')),
    ('ge_pt', _('Georgia · Portuguese')),
    ('ge_it', _('Georgia · Italian')),
    ('ge_fr', _('Georgia · French')),
    ('ge_de', _('Georgia · German')),
    ('ge_nl', _('Georgia · Dutch')),
    ('ge_pl', _('Georgia · Polish')),
    ('ge_uk', _('Georgia · Ukrainian')),
    ('ge_tr', _('Georgia · Turkish')),
)


SITE_LOCALE_TO_DJANGO_LANG = {
    'ge_en': 'en',
    'ge_ru': 'ru',
    'ge_ka': 'ka',
    'ge_es': 'es',
    'ge_pt': 'pt',
    'ge_it': 'it',
    'ge_fr': 'fr',
    'ge_de': 'de',
    'ge_nl': 'nl',
    'ge_pl': 'pl',
    'ge_uk': 'uk',
    'ge_tr': 'tr',
}


def normalize_site_locale(value: str | None) -> str:
    v = (value or '').strip()
    if v in ACTIVE_SHOP_SITE_LOCALES:
        return v
    return DEFAULT_SHOP_SITE_LOCALE


def django_lang_for_site_locale(site_locale: str | None) -> str:
    sl = normalize_site_locale(site_locale)
    return SITE_LOCALE_TO_DJANGO_LANG.get(sl, 'en')


def shop_reverse(viewname: str, *args, site_locale: str | None = None, **kwargs):
    """reverse() for shop routes that live under /<site_locale>/shop/...

    Django forbids passing both ``args`` and ``kwargs`` to ``reverse()``.
    Templates may use positional captures (e.g. ``{% shop_url 'shop:product_detail' p.pk %}``);
    those must use ``args`` only, with ``site_locale`` prepended for the parent prefix.
    Keyword-only reverses use ``kwargs`` and include ``site_locale``.
    """

    sl = normalize_site_locale(site_locale)
    if args and kwargs:
        raise ValueError(
            f'shop_reverse({viewname!r}): use either positional URL args or keyword args, not both'
        )
    if kwargs:
        url_kwargs = dict(kwargs)
        url_kwargs['site_locale'] = sl
        return reverse(viewname, kwargs=url_kwargs)
    if args:
        return reverse(viewname, args=(sl,) + tuple(args))
    return reverse(viewname, kwargs={'site_locale': sl})


def safe_shop_reverse(viewname: str, *args, site_locale: str | None = None, **kwargs) -> str:
    try:
        return shop_reverse(viewname, *args, site_locale=site_locale, **kwargs)
    except NoReverseMatch:
        return '/'


def session_site_locale(request) -> str | None:
    raw = request.session.get(_SESSION_KEY)
    if raw in ACTIVE_SHOP_SITE_LOCALES:
        return raw
    return None


def set_session_site_locale(request, site_locale: str) -> None:
    if normalize_site_locale(site_locale) == site_locale and site_locale in ACTIVE_SHOP_SITE_LOCALES:
        request.session[_SESSION_KEY] = site_locale


def preferred_site_locale_from_user(user) -> str | None:
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    raw = getattr(user, 'preferred_site_locale', None) or ''
    if raw in ACTIVE_SHOP_SITE_LOCALES:
        return raw
    return None


def locale_from_accept_language(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(','):
        tag = part.split(';')[0].strip().lower()
        if tag.startswith('ru'):
            return 'ge_ru'
        if tag.startswith('ka'):
            return 'ge_ka'
        if tag.startswith('en'):
            return 'ge_en'
    return None


def resolve_redirect_site_locale(request) -> str:
    """Locale for / and /shop/ → /<locale>/shop/ redirects (no URL prefix yet)."""
    sl = session_site_locale(request)
    if sl:
        return sl
    sl = preferred_site_locale_from_user(getattr(request, 'user', None))
    if sl:
        return sl
    sl = locale_from_accept_language(request.META.get('HTTP_ACCEPT_LANGUAGE'))
    if sl:
        return sl
    return DEFAULT_SHOP_SITE_LOCALE


def replace_shop_path_site_locale(path: str, new_locale: str) -> str:
    """
    /ge_en/shop/foo/ → /ge_ru/shop/foo/ with new_locale (always trailing slash).
    Other paths → /<new_locale>/shop/
    """
    nl = normalize_site_locale(new_locale)
    raw = (path or '').strip()
    if not raw.startswith('/'):
        raw = '/' + raw
    segments = [s for s in raw.rstrip('/').split('/') if s]
    if (
        len(segments) >= 2
        and segments[0] in ACTIVE_SHOP_SITE_LOCALES
        and segments[1] == 'shop'
    ):
        segments[0] = nl
        return '/' + '/'.join(segments) + '/'
    return shop_reverse('shop:index', site_locale=nl)


