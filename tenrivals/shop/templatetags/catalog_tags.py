from django import template
from django.templatetags.static import static as static_url
from django.urls import reverse

from persons.account_display import account_initials_for_user

from ..catalog_utils import distinct_brands_for_type, stock_catalog_storefront_queryset
from ..models import ProductType

register = template.Library()


@register.simple_tag
def absolute_static_uri(request, relative_static_path: str) -> str:
    """Absolute URL to a file under STATIC_URL (for og:image, etc.)."""
    rel = (relative_static_path or '').strip().lstrip('/')
    path = static_url(rel)
    return request.build_absolute_uri(path)


@register.filter(name='dict_get')
def dict_get(mapping, key):
    if not mapping:
        return ''
    return mapping.get(str(key), '')


@register.filter(name='account_initials')
def account_initials(user):
    if not user:
        return '?'
    if getattr(user, 'is_authenticated', False) is not True:
        return '?'
    return account_initials_for_user(user)


@register.filter(name='abs_site_href')
def abs_site_href(url):
    """Ensure internal paths are root-absolute so they work from any page (e.g. /shop/ → not /shop/shop/...)."""
    u = (url or '').strip()
    if not u:
        return u
    low = u.lower()
    if low.startswith(('http://', 'https://', '//')):
        return u
    return u if u.startswith('/') else f'/{u}'


@register.inclusion_tag('shop/includes/stock_catalog_nav.html')
def stock_catalog_nav():
    stock = stock_catalog_storefront_queryset()

    def b(tc):
        return distinct_brands_for_type(stock, tc)

    return {
        'catalog_url': reverse('shop:stock'),
        'preorder_url': reverse('shop:preorder'),
        'index_url': reverse('shop:index'),
        'racket_brands': b(ProductType.RACKET),
        'bag_brands': b(ProductType.BAGS),
        'ball_brands': b(ProductType.BALLS),
        'm_app_brands': b(ProductType.MENS_APPAREL),
        'w_app_brands': b(ProductType.WOMENS_APPAREL),
        'j_app_brands': b(ProductType.JUNIOR_APPAREL),
        'm_shoe_brands': b(ProductType.MENS_SHOES),
        'w_shoe_brands': b(ProductType.WOMENS_SHOES),
        'j_shoe_brands': b(ProductType.JUNIOR_SHOES),
        'acc_brands': b(ProductType.ACCESSORIES),
        'damp_brands': b(ProductType.DAMPENERS),
        'string_brands': b(ProductType.STRINGS),
        'grip_brands': b(ProductType.GRIPS),
    }
