
from django_filters import FilterSet, ChoiceFilter
from django.forms import DateInput
import django_filters
from .models import Tournament, Player
from persons.models import CustomUser
from django import forms
from .const import GENDER, TOURNAMENT_CATEGORY, TOURNAMENT_STATUS, TOURNAMENT_GENDER, TOURNAMENT_FORMAT, TOURNAMENT_TYPE, TR_GEOS, TR_CITIES



class TournamentsQuickFilter(FilterSet):
    type = ChoiceFilter(choices=TOURNAMENT_TYPE, field_name='type', label="", empty_label="All", widget=forms.RadioSelect())
    gender = ChoiceFilter(choices=TOURNAMENT_GENDER, field_name='gender', label="", empty_label="All", widget=forms.RadioSelect())
    #geo = ChoiceFilter(choices=TR_GEOS, field_name='geo', label="", empty_label='All Countries', initial='Georgia', widget=forms.RadioSelect())
    #city = ChoiceFilter(choices=TR_CITIES, field_name='city', label="", empty_label='All Cities', initial='TBI', widget=forms.RadioSelect())
    geo = ChoiceFilter(choices=TR_GEOS, field_name='geo', label="", empty_label='All Countries', widget=forms.RadioSelect())
    city = ChoiceFilter(choices=TR_CITIES, field_name='city', label="", empty_label='All Cities', widget=forms.RadioSelect())
    
    format = ChoiceFilter(choices=TOURNAMENT_FORMAT, field_name='format', label="", empty_label=None, widget=forms.RadioSelect())
    status = ChoiceFilter(choices=TOURNAMENT_STATUS, field_name='status', label="", empty_label='All Statuses', widget=forms.RadioSelect())
    category = ChoiceFilter(choices=TOURNAMENT_CATEGORY, field_name='category', label="", empty_label="All", widget=forms.RadioSelect())

    class Meta:
        model = Tournament
        fields = ['type', 'gender', 'geo', 'city', 'format', 'status', 'category']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        



class AllRivalsFilter(FilterSet):
    gender = ChoiceFilter(
        choices=GENDER,
        field_name='gender',
        label="",
        empty_label="All",
        widget=forms.RadioSelect()
    )
    geo = ChoiceFilter(
        choices=TR_GEOS,
        field_name='geo',
        label="",
        empty_label='All Countries',
        widget=forms.RadioSelect()
    )
    city = ChoiceFilter(
        choices=TR_CITIES,
        field_name='city',
        label="",
        empty_label='All Cities',
        widget=forms.RadioSelect()
    )

    category = ChoiceFilter(
        choices=TOURNAMENT_CATEGORY,
        field_name='category',
        label="",
        empty_label="All",
        widget=forms.RadioSelect()
    )

    

    class Meta:
        model = Player
        fields = ['gender', 'geo', 'city', 'category']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)