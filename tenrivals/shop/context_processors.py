from django.db import DatabaseError

from .cart_session import cart_line_count_units, get_cart


def shop_cart(request):
    try:
        return {'shop_cart_count': cart_line_count_units(get_cart(request))}
    except (DatabaseError, TypeError, ValueError, KeyError, AttributeError):
        return {'shop_cart_count': 0}
