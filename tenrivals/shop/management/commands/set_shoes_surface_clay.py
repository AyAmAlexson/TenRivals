from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Set surface=ClayCourt for all existing Shoe records.'

    def handle(self, *args, **options):
        from shop.models import Shoe, CourtSurface
        updated = Shoe.objects.all().update(surface=CourtSurface.CLAY)
        self.stdout.write(self.style.SUCCESS(f'Updated {updated} shoes to ClayCourt'))


