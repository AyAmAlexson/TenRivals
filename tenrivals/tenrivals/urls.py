from django.contrib import admin
from django.urls import path, include
from persons.views import CustomLoginView, CustomSignupView
from django.conf.urls.static import static
from django.conf import settings
from django.contrib.auth import views as auth_views
from persons import views as person_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('pages/', include('django.contrib.flatpages.urls')),
    path('persons/', include('persons.urls')),
    path('', include('rivals.urls')),
    path('accounts/', include('allauth.urls')),
    # path('dashboard/', include('dashboard.urls')),
    path('shop/', include('shop.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [
        path('__debug__/', include(debug_toolbar.urls)),
    ] + urlpatterns