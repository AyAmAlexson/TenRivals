"""Run migrate under a PostgreSQL advisory lock.

Heroku can start two release dynos for the same push (or overlapping
deploys). Concurrent ``migrate`` on a fresh app creates the same table
types twice and fails with:

    UniqueViolation: duplicate key ... (typname)=(buying_...)

Session-level ``pg_advisory_lock`` serializes release migrations.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection

# Stable app-wide lock id (not a secret). Collision with other soft locks
# in this DB is extremely unlikely.
MIGRATE_LOCK_KEY = 4_201_996_031


class Command(BaseCommand):
    help = 'Apply migrations serialized by a PostgreSQL advisory lock.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--noinput',
            '--no-input',
            action='store_true',
            help='Pass --noinput through to migrate.',
        )

    def handle(self, *args, **options):
        noinput = options['noinput']
        if connection.vendor != 'postgresql':
            call_command('migrate', interactive=not noinput)
            return

        self.stdout.write('Waiting for PostgreSQL migration advisory lock...')
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_lock(%s)', [MIGRATE_LOCK_KEY])
        try:
            self.stdout.write('Lock acquired — running migrate.')
            call_command('migrate', interactive=not noinput)
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [MIGRATE_LOCK_KEY])
            self.stdout.write('Migration lock released.')
