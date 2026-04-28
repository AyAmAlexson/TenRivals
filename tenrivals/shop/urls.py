from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = 'shop'

urlpatterns = [
    path('', views.index, name='index'),
    path('product/<int:pk>/', views.product_detail, name='product_detail'),
    path('cart/', views.cart, name='cart'),
    path('cart/add/', views.cart_add, name='cart_add'),
    path('cart/update-line/', views.cart_update_line, name='cart_update_line'),
    path('cart/remove-line/', views.cart_remove_line, name='cart_remove_line'),
    path('cart/remove-line/<int:line_index>/', views.cart_remove_line, name='cart_remove_line_idx'),
    path('cart/apply-promo/', views.cart_apply_promo, name='cart_apply_promo'),
    path('checkout/', views.checkout, name='checkout'),
    path('checkout/success/<int:order_id>/', views.checkout_success, name='checkout_success'),
    path('search/', views.product_search, name='product_search'),
    path(
        'stock/<slug:type_slug>/<slug:brand_slug>/<slug:surface_slug>/',
        views.stock,
        name='stock_catalog_shoe',
    ),
    path(
        'stock/<slug:type_slug>/<slug:brand_slug>/',
        views.stock,
        name='stock_catalog_brand',
    ),
    path('stock/<slug:type_slug>/', views.stock, name='stock_catalog_type'),
    path('stock/', views.stock, name='stock'),
    path(
        'preorder/<slug:type_slug>/<slug:brand_slug>/<slug:surface_slug>/',
        views.preorder,
        name='preorder_catalog_shoe',
    ),
    path(
        'preorder/<slug:type_slug>/<slug:brand_slug>/',
        views.preorder,
        name='preorder_catalog_brand',
    ),
    path('preorder/<slug:type_slug>/', views.preorder, name='preorder_catalog_type'),
    path('brands/', views.brands, name='brands'),
    path('order-for-me/', views.order_for_me_create, name='order_for_me'),
    path('collections/<slug:slug>/', views.collection_detail, name='collection_detail'),
    path('blog/', views.blog_index, name='blog_index'),
    path('blog/<slug:slug>/', views.blog_post, name='blog_post'),
    path('info/delivery/', views.shop_info_page, {'page_key': 'delivery'}, name='info_delivery'),
    path('info/payment/', views.shop_info_page, {'page_key': 'payment'}, name='info_payment'),
    path('info/returns/', views.shop_info_page, {'page_key': 'returns'}, name='info_returns'),
    path('info/size-guide/', views.shop_info_page, {'page_key': 'size_guide'}, name='info_size_guide'),
    path('info/contacts/', views.shop_info_page, {'page_key': 'contacts'}, name='info_contacts'),
    path('legal/terms/', views.shop_info_page, {'page_key': 'terms'}, name='legal_terms'),
    path('legal/privacy/', views.shop_info_page, {'page_key': 'privacy'}, name='legal_privacy'),
    path('legal/dmca/', views.shop_info_page, {'page_key': 'dmca'}, name='legal_dmca'),
    path('legal/company/', views.shop_info_page, {'page_key': 'company'}, name='legal_company'),
    path('legal/accessibility/', views.shop_info_page, {'page_key': 'accessibility'}, name='legal_accessibility'),
    path('legal/cookies/', views.shop_info_page, {'page_key': 'cookies'}, name='legal_cookies'),
    path('legal/sitemap/', views.shop_info_page, {'page_key': 'sitemap'}, name='legal_sitemap'),
    path(
        'guides/how-to-choose-racket-weight/',
        views.shop_guide,
        {'slug': 'racket-weight'},
        name='guide_racket_weight',
    ),
    path(
        'guides/clay-court-shoes-tbilisi/',
        views.shop_guide,
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
    path('preorder', views.preorder, name='preorder'),
    path('add', views.product_create, name='product_create'),
    path('edit/<int:pk>', views.product_edit, name='product_edit'),
]