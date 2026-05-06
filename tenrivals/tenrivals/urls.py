from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import path, include, re_path
from persons.views import ConfirmEmailWithGtmView, CustomLoginView, CustomSignupView
from django.conf.urls.static import static
from django.conf import settings
from shop.sitemaps import (
    BlogPostSitemap,
    ProductCollectionSitemap,
    ProductSitemap,
    ShopStaticSitemap,
)
from shop.site_locale import ACTIVE_SITE_LOCALE_PATTERN
from shop.views import robots_txt
from shop.views_locale import (
    shop_legacy_path_redirect,
    shop_root_locale_redirect,
    set_shop_site_locale,
)

_SHOP_SITEMAPS = {
    'static': ShopStaticSitemap,
    'products': ProductSitemap,
    'blog': BlogPostSitemap,
    'collections': ProductCollectionSitemap,
}

# Shop storefront under /<locale>/shop/..., legacy /shop → redirect, language switcher endpoint.
urlpatterns = [
    path('admin/', admin.site.urls),
    path('robots.txt', robots_txt),
    path(
        'sitemap.xml',
        sitemap,
        {'sitemaps': _SHOP_SITEMAPS},
        name='sitemap_xml',
    ),
    path(
        'shop/set-lang/<slug:site_locale>/',
        set_shop_site_locale,
        name='shop_set_site_locale',
    ),
    re_path(rf'^(?P<site_locale>{ACTIVE_SITE_LOCALE_PATTERN})/shop/', include('shop.urls')),
    path('shop/', shop_legacy_path_redirect),
    re_path(r'^shop/(?P<path_rest>.+)/$', shop_legacy_path_redirect),
    path('', shop_root_locale_redirect),
    # Must be before allauth.urls so /accounts/login|signup use Custom* views (rate limits, forms).
    path('accounts/login/', CustomLoginView.as_view(), name='account_login'),
    path('accounts/signup/', CustomSignupView.as_view(), name='account_signup'),
    # Same path as allauth confirm-email; registered first so analytics can run before redirect.
    re_path(
        r'^accounts/confirm-email/(?P<key>[-:\w]+)/$',
        ConfirmEmailWithGtmView.as_view(),
        name='tenrivals_confirm_email',
    ),
    path('accounts/', include('allauth.urls')),
    path('administration/', include(('persons.administration_urls', 'administration'), namespace='administration')),
    path('persons/', include('persons.urls')),
    path('league/', include('rivals.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    import debug_toolbar
    from tenrivals.error_views import sentry_debug_trigger

    urlpatterns = [
        path('__debug__/', include(debug_toolbar.urls)),
        path('sentry-debug/', sentry_debug_trigger),
    ] + urlpatterns

handler404 = 'tenrivals.error_views.page_not_found'
handler403 = 'tenrivals.error_views.permission_denied'
handler500 = 'tenrivals.error_views.server_error'