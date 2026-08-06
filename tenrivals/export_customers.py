"""One-off script: export all shop.Customer rows to CSV.

Run from the tenrivals/ directory: ../venv/bin/python export_customers.py
"""
import csv
import os
from pathlib import Path

import environ
import django

BASE_DIR = Path(__file__).resolve().parent
environ.Env.read_env(BASE_DIR / 'tenrivals' / '.env', overwrite=False)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tenrivals.settings')
django.setup()

from shop.models import Customer  # noqa: E402

OUTPUT = BASE_DIR / 'customers_export.csv'

fields = [
    'id',
    'user_id',
    'first_name',
    'last_name',
    'name_local',
    'surname_local',
    'phone',
    'email',
    'newsletter_opt_in',
    'tg_account',
    'address',
    'source',
    'comment',
    'created_at',
    'updated_at',
]

qs = Customer.objects.all().order_by('id')
with open(OUTPUT, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    writer.writerow(fields)
    for c in qs.iterator():
        writer.writerow([getattr(c, name) for name in fields])

print(f'Exported {qs.count()} customers to {OUTPUT}')
