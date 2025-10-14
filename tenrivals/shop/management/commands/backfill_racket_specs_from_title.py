from django.core.management.base import BaseCommand
from django.db import transaction
from shop.models import Racket
import re


class Command(BaseCommand):
    help = (
        "Backfill racket weight_grams and head_size_sq_in from title when missing.\n"
        "Logic: if weight is missing, find a number 250-350 in the name => unstrung weight.\n"
        "If head size is missing, find a number 85-117 in the name => head size in²."
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Do not write changes, only show what would change')

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        qs = Racket.objects.all()

        updated_weight = 0
        updated_head = 0

        # precompile patterns
        # числа могут быть рядом с буквами (305g, 100in), поэтому ограничиваем только по соседним цифрам
        weight_re = re.compile(r"(?<!\d)(25\d|26\d|27\d|28\d|29\d|30\d|31\d|32\d|33\d|34\d|350)(?!\d)")
        head_re = re.compile(r"(?<!\d)(8[5-9]|9\d|10\d|11[0-7])(?!\d)")

        for r in qs:
            name = (r.name or '').strip()
            changed = False

            if not r.weight_grams and name:
                m_w = weight_re.search(name)
                if m_w:
                    r.weight_grams = int(m_w.group(1))
                    if r.is_strung is None:
                        r.is_strung = False
                    updated_weight += 1
                    changed = True

            if not r.head_size_sq_in and name:
                m_h = head_re.search(name)
                if m_h:
                    r.head_size_sq_in = int(m_h.group(1))
                    updated_head += 1
                    changed = True

            if changed and not dry_run:
                r.save(update_fields=['weight_grams', 'is_strung', 'head_size_sq_in'])

        self.stdout.write(self.style.SUCCESS(
            f"Backfill complete. Updated weight for {updated_weight} rackets, head size for {updated_head} rackets."
        ))


