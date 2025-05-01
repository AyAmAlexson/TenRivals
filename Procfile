web: PYTHONPATH=TenRivals gunicorn --chdir tenrivals --workers=${WEB_CONCURRENCY:-4} tenrivals.wsgi --log-file -

worker: PYTHONPATH=TenRivals celery --workdir tenrivals -A tenrivals.celery:app worker --loglevel=info

beat: PYTHONPATH=TenRivals celery --workdir tenrivals -A tenrivals.celery:app beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler

telegram_bot: PYTHONPATH=TenRivals python tenrivals/manage.py run_telegram_bot
