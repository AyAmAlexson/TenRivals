import os
from celery import Celery
from celery.schedules import crontab
from django.conf import settings

# Установка переменной окружения для настроек Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tenrivals.settings')

# Создание экземпляра приложения Celery
app = Celery('tenrivals')

# Загрузка настроек из settings.py
# Настройки Celery должны иметь префикс CELERY_
app.config_from_object('django.conf:settings', namespace='CELERY')

# Автоматическое обнаружение и регистрация задач из всех приложений Django
app.autodiscover_tasks()

# Настройка периодических задач
app.conf.beat_schedule = {
    'cleanup-old-events': {
        'task': 'rivals.tasks.cleanup_old_events',
        'schedule': crontab(hour=0, minute=0),  # каждый день в полночь
    },
} 