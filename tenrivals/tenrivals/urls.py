from django.contrib import admin
from django.urls import path, include
from persons.views import CustomLoginView, CustomSignupView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('pages/', include('django.contrib.flatpages.urls')),
    path('users/', include('persons.urls')),
    path('', include('rivals.urls')),
    path('accounts/login/', CustomLoginView.as_view(), name='account_login'),
    path('accounts/signup/', CustomSignupView.as_view(), name='account_signup'),
    path('accounts/', include('allauth.urls')),
    # path('dashboard/', include('dashboard.urls')),
]