from django.urls import path
from . import views

app_name = 'shop'

urlpatterns = [
    path('', views.index, name='index'),
    path('product/', views.product_detail, name='product_detail'),
    path('items_list_for_Laen', views.items_list_for_Laen, name='items_list_for_Laen'),
    path('preorder', views.preorder, name='preorder'),
    path('add', views.product_create, name='product_create'),
    path('edit/<int:pk>', views.product_edit, name='product_edit'),
]