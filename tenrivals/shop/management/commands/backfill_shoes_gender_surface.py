from django.core.management.base import BaseCommand
from django.db import transaction
from shop.models import Shoe, ProductType, Gender, CourtSurface


class Command(BaseCommand):
    help = (
        "Backfill shoes gender and surface from product type and name.\n"
        "Gender: type=M_SHOES => MEN; type=W_SHOES => WOMEN.\n"
        "Surface (by name, case-insensitive):\n"
        "  contains 'Clay' => CLAY; contains 'HC' => HARD; contains 'AC' or 'SPD' => ALL_COURT.\n"
        "Order of evaluation: Clay > HC > AC/SPD."
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Do not persist changes')

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options['dry_run']

        shoes = Shoe.objects.all()
        updated_gender = 0
        updated_surface = 0

        for shoe in shoes:
            changed = False

            # Set gender from type (only for M_SHOES / W_SHOES)
            if shoe.type == ProductType.MENS_SHOES:
                if shoe.gender != Gender.MEN:
                    shoe.gender = Gender.MEN
                    updated_gender += 1
                    changed = True
            elif shoe.type == ProductType.WOMENS_SHOES:
                if shoe.gender != Gender.WOMEN:
                    shoe.gender = Gender.WOMEN
                    updated_gender += 1
                    changed = True

            # Surface from name/title
            name = (shoe.name or '').lower()
            desired_surface = None
            # Priority: Clay > HC > AC/SPD
            if 'clay' in name:
                desired_surface = CourtSurface.CLAY
            elif 'hc' in name:
                desired_surface = CourtSurface.HARD
            elif 'ac' in name or 'spd' in name:
                desired_surface = CourtSurface.ALL_COURT

            if desired_surface and shoe.surface != desired_surface:
                shoe.surface = desired_surface
                updated_surface += 1
                changed = True

            if changed and not dry_run:
                shoe.save(update_fields=['gender', 'surface'])

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"Dry run: would update genders={updated_gender}, surfaces={updated_surface}"
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Updated shoes: genders={updated_gender}, surfaces={updated_surface}"
            ))


