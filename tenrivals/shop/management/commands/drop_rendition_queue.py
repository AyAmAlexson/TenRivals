"""Drop the stuck Celery image-build backlog without flushing Redis."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        'Delete the default Celery queue list. Does not flush Redis or other keys. '
        'The list is the image-build backlog that filled the 30 MB broker.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--queue',
            default='celery',
            help='Redis list key of the Celery queue (default: celery).',
        )

    def handle(self, *args, **options):
        url = (getattr(settings, 'CELERY_BROKER_URL', '') or '').strip()
        if not url.startswith('redis://') and not url.startswith('rediss://'):
            raise CommandError('CELERY_BROKER_URL is not a Redis URL.')
        queue = (options['queue'] or 'celery').strip() or 'celery'
        try:
            import redis
        except ImportError as exc:
            raise CommandError('The redis package is not installed.') from exc

        client = redis.Redis.from_url(url)
        try:
            queued = int(client.llen(queue) or 0)
            removed = int(client.delete(queue) or 0)
        finally:
            client.close()
        self.stdout.write(
            f'Removed Redis list {queue!r} ({queued} messages, deleted={removed}). '
            'Other keys were left in place.'
        )
