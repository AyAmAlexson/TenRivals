from django_filters import FilterSet,DateFromToRangeFilter
from django.forms import DateInput
import django_filters
from .models import Tournament
from django import forms
from .const import TOURNAMENT_CATEGORY, TOURNAMENT_STATUS, TOURNAMENT_GENDER, TOURNAMENT_FORMAT, TOURNAMENT_TYPE, TR_GEOS, TR_CITIES



class TournamentsQuickFilter(FilterSet):
    type = django_filters.ChoiceFilter(     choices=TOURNAMENT_TYPE,
                                            field_name='_type',
                                            label="",
                                            empty_label="All",
                                            widget=forms.RadioSelect()
                                       )

    gender = django_filters.ChoiceFilter(   choices=TOURNAMENT_GENDER,
                                            field_name='_gender',
                                            label="",
                                            empty_label="All",
                                            widget=forms.RadioSelect()
                                        )

    geo = django_filters.ChoiceFilter( choices = TR_GEOS,
                                       field_name='_geo',
                                       label="",
                                       empty_label = 'All Countries',
                                       initial = 'Georgia',
                                       widget=forms.RadioSelect()
                                     )

    city = django_filters.ChoiceFilter( choices = TR_CITIES,
                                       field_name='_city',
                                       label="",
                                       empty_label = 'All Cities',
                                       initial = 'TBI',
                                        widget=forms.RadioSelect()
                                     )

    format = django_filters.ChoiceFilter(choices=TOURNAMENT_FORMAT,
                                       field_name='_format',
                                       label="",
                                       empty_label=None,
                                         widget=forms.RadioSelect()
                                       )

    status = django_filters.ChoiceFilter(choices=TOURNAMENT_STATUS,
                                       field_name='_status',
                                       label="",
                                       empty_label='All Statuses',
                                         widget=forms.RadioSelect()
                                       )

    category = django_filters.ChoiceFilter(choices=TOURNAMENT_CATEGORY,
                                       field_name='_category',
                                       label="",
                                       empty_label="All",
                                           widget=forms.RadioSelect()
                                       )
    class Meta:

        model = Tournament
        fields = {
            '_type':[],
            '_gender': [],
            '_geo': [],
            '_city': [],
            '_format': [],
            '_status': [],
            '_category': [],
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        print(self.data)  # Печатает данные, переданные в фильтр