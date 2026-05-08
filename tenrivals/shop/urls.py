from functools import wraps

from django.urls import path, re_path
from django.views.generic import RedirectView

from . import views


def strip_site_locale(view_func):
    """Parent URL captures ``site_locale``; shop FBVs only need ``request`` + path kwargs."""

    @wraps(view_func)
    def wrapper(request, site_locale=None, **kwargs):
        return view_func(request, **kwargs)

    return wrapper


_v = strip_site_locale

app_name = 'shop'

urlpatterns = [
    path('', _v(views.index), name='index'),
    path('product/<int:pk>/', _v(views.product_detail), name='product_detail'),
    path('cart/', _v(views.cart), name='cart'),
    path('cart/add/', _v(views.cart_add), name='cart_add'),
    path('cart/update-line/', _v(views.cart_update_line), name='cart_update_line'),
    path('cart/remove-line/', _v(views.cart_remove_line), name='cart_remove_line'),
    path('cart/remove-line/<int:line_index>/', _v(views.cart_remove_line), name='cart_remove_line_idx'),
    path('cart/apply-promo/', _v(views.cart_apply_promo), name='cart_apply_promo'),
    path('checkout/', _v(views.checkout), name='checkout'),
    path('checkout/success/<int:order_id>/', _v(views.checkout_success), name='checkout_success'),
    path('search/', _v(views.product_search), name='product_search'),
    path(
        'stock/<slug:type_slug>/<slug:brand_slug>/<slug:surface_slug>/',
        _v(views.stock),
        name='stock_catalog_shoe',
    ),
    path(
        'stock/<slug:type_slug>/<slug:brand_slug>/',
        _v(views.stock),
        name='stock_catalog_brand',
    ),
    path('stock/<slug:type_slug>/', _v(views.stock), name='stock_catalog_type'),
    path('stock/', _v(views.stock), name='stock'),
    path(
        'preorder/<slug:type_slug>/<slug:brand_slug>/<slug:surface_slug>/',
        _v(views.preorder),
        name='preorder_catalog_shoe',
    ),
    path(
        'preorder/<slug:type_slug>/<slug:brand_slug>/',
        _v(views.preorder),
        name='preorder_catalog_brand',
    ),
    path('preorder/<slug:type_slug>/', _v(views.preorder), name='preorder_catalog_type'),
    path('brands/', _v(views.brands), name='brands'),
    path('order-for-me/', _v(views.order_for_me_create), name='order_for_me'),
    path('collections/<slug:slug>/', _v(views.collection_detail), name='collection_detail'),
    path('blog/', _v(views.blog_index), name='blog_index'),
    path('blog/<slug:slug>/', _v(views.blog_post), name='blog_post'),
    path('info/delivery/', _v(views.shop_info_page), {'page_key': 'delivery'}, name='info_delivery'),
    path('info/payment/', _v(views.shop_info_page), {'page_key': 'payment'}, name='info_payment'),
    path('info/returns/', _v(views.shop_info_page), {'page_key': 'returns'}, name='info_returns'),
    path('info/size-guide/', _v(views.shop_info_page), {'page_key': 'size_guide'}, name='info_size_guide'),
    path('info/contacts/', _v(views.shop_info_page), {'page_key': 'contacts'}, name='info_contacts'),
    path('legal/terms/', _v(views.shop_info_page), {'page_key': 'terms'}, name='legal_terms'),
    path('legal/privacy/', _v(views.shop_info_page), {'page_key': 'privacy'}, name='legal_privacy'),
    path('legal/dmca/', _v(views.shop_info_page), {'page_key': 'dmca'}, name='legal_dmca'),
    path('legal/company/', _v(views.shop_info_page), {'page_key': 'company'}, name='legal_company'),
    path('legal/accessibility/', _v(views.shop_info_page), {'page_key': 'accessibility'}, name='legal_accessibility'),
    path('legal/cookies/', _v(views.shop_info_page), {'page_key': 'cookies'}, name='legal_cookies'),
    path('legal/sitemap/', _v(views.shop_info_page), {'page_key': 'sitemap'}, name='legal_sitemap'),
    re_path(r'^preorder/?$', _v(views.preorder), name='preorder'),
    path(
        'guides/how-to-choose-racket-weight/',
        _v(views.shop_guide),
        {'slug': 'racket-weight'},
        name='guide_racket_weight',
    ),
    path(
        'guides/clay-court-shoes-tbilisi/',
        _v(views.shop_guide),
        {'slug': 'clay-shoes-tbilisi'},
        name='guide_clay_shoes',
    ),
    path(
        'items_list_for_Laen',
        RedirectView.as_view(
            pattern_name='shop:stock',
            permanent=True,
            query_string=True,
        ),
    ),
    path('add', _v(views.product_create), name='product_create'),
    path('edit/<int:pk>', _v(views.product_edit), name='product_edit'),
]