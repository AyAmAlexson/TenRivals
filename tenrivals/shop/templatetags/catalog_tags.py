from django import template
from django.urls import reverse

from ..catalog_utils import distinct_brands_for_type, stock_catalog_base_queryset
from ..models import ProductType

register = template.Library()


@register.inclusion_tag('shop/includes/stock_catalog_nav.html')
def stock_catalog_nav():
    stock = stock_catalog_base_queryset()

    def b(tc):
        return distinct_brands_for_type(stock, tc)

    return {
        'catalog_url': reverse('shop:items_list_for_Laen'),
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
        'string_brands': b(ProductType.STRINGS),
        'grip_brands': b(ProductType.GRIPS),
    }
