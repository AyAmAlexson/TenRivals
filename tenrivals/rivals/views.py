from django.shortcuts import render
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from datetime import datetime
from random import sample
from .const import TR_GEOS, TR_CITIES

from django.http import HttpResponse
from django.views import View

from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin,PermissionRequiredMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView

from .filters import TournamentsQuickFilter
# from .forms import TournamentForm
from .models import Tournament

from django.utils.translation import gettext as _  # импортируем функцию для перевода

class IndexView(LoginRequiredMixin, TemplateView):
    template_name = 'tr-landing.html'



# class ResPropertiesList(LoginRequiredMixin, ListView):
class TournamentsListView(ListView):
    model = Tournament
    # Поле, которое будет использоваться для сортировки объектов
    ordering = '_date_created'
    # Указываем имя шаблона, в котором будут все инструкции о том,
    # как именно пользователю должны быть показаны наши объекты
    template_name = 'tournaments.html'
    # Это имя списка, в котором будут лежать все объекты.
    # Его надо указать, чтобы обратиться к списку объектов в html-шаблоне.
    context_object_name = 'tournaments'
    paginate_by = 6

    def get_queryset(self):
        # Получаем обычный запрос
        queryset = super().get_queryset()
        get_query = self.request.GET.copy()
        if 'geo' not in get_query:
            get_query['geo'] = 'GE'
        if 'city' not in get_query:
            get_query['city'] = 'TBI'
        if 'format' not in get_query:
            get_query['format'] = 'S'
        # Используем наш класс фильтрации.
        # Сохраняем нашу фильтрацию в объекте класса,
        # чтобы потом добавить в контекст и использовать в шаблоне.

        self.filterset = TournamentsQuickFilter(get_query, queryset)

        return self.filterset.qs

    def get_ordering(self):
        self.order = self.request.GET.get('order', 'asc')
        selected_ordering = self.request.GET.get('ordering', '_date_created')
        if self.order == "desc":
            selected_ordering = "-" + selected_ordering
        return selected_ordering

    def get_context_data(self, **kwargs):
        # С помощью super() мы обращаемся к родительским классам
        # и вызываем у них метод get_context_data с теми же аргументами,
        # что и были переданы нам.
        # В ответе мы должны получить словарь.
        context = super().get_context_data(**kwargs)
        # К словарю добавим текущую дату в ключ 'time_now'.
        context['time_now'] = datetime.utcnow()
        # Добавим ещё одну пустую переменную,
        # чтобы на её примере рассмотреть работу ещё одного фильтра.
        context['geo_list'] = TR_GEOS
        context['cities_list'] = TR_CITIES
        context['current_order'] = self.get_ordering()
        context['order'] = self.order
        context['filterset'] = self.filterset

        return context
