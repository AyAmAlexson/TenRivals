from rivals.const import *
from django import template


register = template.Library()

@register.filter(name='const_replace')
def const_replace(t):
   RESULT_DICT = TOURNAMENT_TYPE + TOURNAMENT_STATUS + TOURNAMENT_GENDER + TOURNAMENT_FORMAT + TOURNAMENT_CATEGORY + TR_CITIES + TR_GEOS
   return dict(RESULT_DICT)[t]


@register.filter(name='capitalize')
def capitalize(st:str):
   return st.upper()

@register.filter(name='last_sym')
def last_sym(st:str, num:int):
   return st[-num:]

@register.filter(name='minus_last_sym')
def minus_last_sym(st:str, num:int):
   return st[:-num]

@register.filter
def times(number):
    return range(number)