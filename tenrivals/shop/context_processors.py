from django.db import DatabaseError

from .cart_session import cart_line_count_units, get_cart


def shop_cart(request):
    try:
        return {'shop_cart_count': cart_line_count_units(get_cart(request))}
    except (DatabaseError, TypeError, ValueError, KeyError, AttributeError):
        return {'shop_cart_count': 0}


def shop_i18n(request):
    from urllib.parse import urlencode

    from .site_locale import (
        ACTIVE_SHOP_SITE_LOCALES,
        django_lang_for_site_locale,
        normalize_site_locale,
    )

    sl = getattr(request, 'site_locale', None)
    eff = normalize_site_locale(sl)
    lang = django_lang_for_site_locale(eff)

    labels = (
        ('ge_en', 'EN', 'English'),
        ('ge_ru', 'RU', 'Русский'),
        ('ge_ka', 'KA', 'ქართული'),
    )
    by_code = {c: (s, nat) for c, s, nat in labels}

    next_q = urlencode({'next': request.get_full_path()})

    opts = []
    for code in sorted(ACTIVE_SHOP_SITE_LOCALES):
        short, native = by_code.get(code, (code.upper().replace('_', '-')[:8], code))
        opts.append(
            {
                'code': code,
                'short_label': short,
                'native_label': native,
                'href': f'/shop/set-lang/{code}/?{next_q}',
                'is_active': eff == code,
            }
        )
    short_self, native_self = by_code.get(eff, (eff, eff))
    return {
        'shop_site_locale_effective': eff,
        'shop_site_lang': lang,
        'shop_html_lang': lang,
        'shop_language_toggle_short': short_self,
        'shop_language_toggle_native': native_self,
        'shop_language_switch_options': opts,
    }
