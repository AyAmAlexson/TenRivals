web: PYTHONPATH=TenRivals gunicorn --chdir tenrivals --workers=${WEB_CONCURRENCY:-4} tenrivals.wsgi --log-file -

# Celery worker для выполнения фоновых задач
worker: celery -A tenrivals worker --loglevel=info

# Celery Beat для планирования периодических задач (использует базу данных)
beat: celery -A tenrivals beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler

# Ваш Telegram бот (предполагается запуск через manage.py)
telegram_bot: PYTHONPATH=TenRivals python tenrivals/manage.py run_telegram_bot
