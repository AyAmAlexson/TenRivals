from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import path, include
from persons.views import CustomLoginView, CustomSignupView
from django.conf.urls.static import static
from django.conf import settings
from django.views.generic import RedirectView

from shop.sitemaps import (
    BlogPostSitemap,
    ProductCollectionSitemap,
    ProductSitemap,
    ShopStaticSitemap,
)
from shop.views import robots_txt

_SHOP_SITEMAPS = {
    'static': ShopStaticSitemap,
    'products': ProductSitemap,
    'blog': BlogPostSitemap,
    'collections': ProductCollectionSitemap,
}

# Shop-first: root redirects to shop, but all league routes available for auth/nav
urlpatterns = [
    path('admin/', admin.site.urls),
    path('robots.txt', robots_txt),
    path(
        'sitemap.xml',
        sitemap,
        {'sitemaps': _SHOP_SITEMAPS},
        name='sitemap_xml',
    ),
    path('', RedirectView.as_view(pattern_name='shop:index', permanent=False)),
    path('shop/', include('shop.urls')),
    # Must be before allauth.urls so /accounts/login|signup use Custom* views (rate limits, forms).
    path('accounts/login/', CustomLoginView.as_view(), name='account_login'),
    path('accounts/signup/', CustomSignupView.as_view(), name='account_signup'),
    path('accounts/', include('allauth.urls')),
    path('administration/', include(('persons.administration_urls', 'administration'), namespace='administration')),
    path('persons/', include('persons.urls')),
    path('league/', include('rivals.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [
        path('__debug__/', include(debug_toolbar.urls)),
    ] + urlpatterns

handler404 = 'tenrivals.error_views.page_not_found'
handler500 = 'tenrivals.error_views.server_error'