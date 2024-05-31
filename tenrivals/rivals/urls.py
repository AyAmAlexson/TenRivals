from django.urls import path
from .views import IndexView,TournamentsListView

urlpatterns = [
    path('', IndexView.as_view(), name='rivals'),
    path('tournaments/', TournamentsListView.as_view(), name='tournaments'),
]
