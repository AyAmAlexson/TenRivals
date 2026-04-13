from .cart_session import cart_line_count_units, get_cart


def shop_cart(request):
    return {'shop_cart_count': cart_line_count_units(get_cart(request))}
