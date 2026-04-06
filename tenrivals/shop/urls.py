from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = 'shop'

urlpatterns = [
    path('', views.index, name='index'),
    path('product/<int:pk>/', views.product_detail, name='product_detail'),
    path('stock/', views.stock, name='stock'),
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