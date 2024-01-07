from django.urls import path
from .views import AccountDetailView

app_name = 'persons'

urlpatterns = [

    path('my_account/', AccountDetailView.as_view(), name='account_details'),

]
