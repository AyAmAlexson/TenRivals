from django.core.management.base import BaseCommand
from django.db import transaction
from shop.models import (
    Product,
    Category,
    Racket,
    Shoe,
    Apparel,
    String,
    Bag,
    Balls,
    Accessory,
)


def _delete_images(obj):
    for field_name in ("image_1", "image_2", "image_3"):
        f = getattr(obj, field_name, None)
        if f and getattr(f, "name", None):
            try:
                f.delete(save=False)
            except Exception:
                pass


class Command(BaseCommand):
    help = "Clear all shop data (products, categories) and delete associated image files."

    @transaction.atomic
    def handle(self, *args, **options):
        # Delete concrete subclasses first
        total = 0
        for Model in (Racket, Shoe, Apparel, String, Bag, Balls, Accessory):
            for obj in Model.objects.all():
                _delete_images(obj)
                obj.delete()
                total += 1

        # Delete remaining base products (if any)
        for p in Product.objects.all():
            _delete_images(p)
            p.delete()
            total += 1

        # Categories
        cats = Category.objects.count()
        Category.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(
            f"Cleared {total} products and {cats} categories"
        ))



