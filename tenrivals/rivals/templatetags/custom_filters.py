from rivals.const import *
from django import template
from django.template.defaultfilters import timesince
from django.utils.timezone import template_localtime
import re
from django.utils import timezone
from datetime import datetime, date

from django.utils.safestring import mark_safe

register = template.Library()

@register.filter(name='const_replace')
def const_replace(t):
    # Преобразуем каждый список кортежей в словарь
    result_dict = dict(TOURNAMENT_TYPE + TOURNAMENT_STATUS + TOURNAMENT_GENDER +
                       TOURNAMENT_FORMAT + TOURNAMENT_CATEGORY + TR_CITIES + TR_GEOS + MATCH_STATUS)
    
    # Используем метод get для безопасного получения значения
    return result_dict.get(t, t)  # Если t не найдено, возвращаем t без изменений

@register.filter(name='get_color')
def get_color(t):
    # Преобразуем каждый список кортежей в словарь
    result_dict = dict(TIMELINE_EVENT_COLOR)
    
    # Используем метод get для безопасного получения значения
    return result_dict.get(t, t)  # Если t не найдено, возвращаем t без изменений

@register.filter(name='short_timesince')
def short_timesince(value):
    if not value:
        return ''
    
    # Получаем стандартный вывод timesince
    ts = timesince(value)
    
    # Используем регулярные выражения для замены с удалением пробела
    ts = re.sub(r'(\d+)\s*minutes?\b', r'\1m', ts)
    ts = re.sub(r'(\d+)\s*hours?\b', r'\1h', ts)
    ts = re.sub(r'(\d+)\s*days?\b', r'\1d', ts)
    ts = re.sub(r'(\d+)\s*weeks?\b', r'\1w', ts)
    ts = re.sub(r'(\d+)\s*months?\b', r'\1mo', ts)
    ts = re.sub(r'(\d+)\s*years?\b', r'\1y', ts)
    
    # Убираем запятые и лишние пробелы между разными единицами измерения
    ts = re.sub(r',\s*', ' ', ts)
    ts = re.sub(r'\s+', ' ', ts).strip()
    
    return ts


@register.filter(name='days_ago')
def days_ago(value):
    if not value:
        return ''
    
    # Преобразуем в datetime если это date
    if isinstance(value, date):
        value = datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            return ''
    
    # Вычисляем разницу в днях
    delta = timezone.now() - timezone.make_aware(value)
    days = delta.days
    
    # Форматируем вывод
    if days <= 1:
        return 'today'
    else:
        return f'{days} d'


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


@register.filter
def increment(value):
   return value + 1

@register.filter
def decrement(value):
   return value - 1 


@register.filter
def short_name(name:str):
   if ' ' in name:
      return name.split(' ')[0][0] + '. ' + name.split(' ')[1]
   else:
      return name
    
@register.filter
def dict_get(dictionary, key):
   return dictionary.get(key)

@register.filter
def brakets_to_sup(set):
   set=str(set)
   result = set.replace('(', '<sup>').replace(')' , '</sup>')
   result += ' '
   return mark_safe(result)

@register.filter(name='add_class')
def add_class(field, css):
    """
    Если переданный объект является BoundField, добавляет к нему CSS класс через метод as_widget.
    Если объект уже представляет собой строку, возвращает его без изменений.
    """
    if hasattr(field, 'as_widget'):
        return field.as_widget(attrs={"class": css})
    return field

@register.filter(name='abs')
def abs_filter(value):
    return abs(value)

@register.filter
def multiply(value, arg):
    return value * arg

@register.filter
def subtract(value, arg):
    return value - arg

@register.filter(name='has_session_key')
def has_session_key(session, key):
    return key in session

