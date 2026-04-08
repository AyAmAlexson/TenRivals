from django.contrib import admin
from django.urls import path, include
from persons.views import CustomLoginView, CustomSignupView
from django.conf.urls.static import static
from django.conf import settings
from django.contrib.auth import views as auth_views
from persons import views as person_views
from django.views.generic import RedirectView

# Shop-first: root redirects to shop, but all league routes available for auth/nav
urlpatterns = [
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(pattern_name='shop:index', permanent=False)),
    path('shop/', include('shop.urls')),
    path('accounts/', include('allauth.urls')),
    path('persons/', include('persons.urls')),
    path('league/', include('rivals.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [
        path('__debug__/', include(debug_toolbar.urls)),
    ] + urlpatterns