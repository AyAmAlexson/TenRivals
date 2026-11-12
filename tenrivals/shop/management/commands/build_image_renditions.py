"""Build WebP width variants for storefront images already in storage."""

from django.core.management.base import BaseCommand

from shop.models import (
    BlogPost,
    HomeHeroSlide,
    HomePromoBanner,
    Product,
    ProductCollection,
    ProductCollectionGroup,
)
from shop.responsive_images import PRODUCT_IMAGE_FIELDS, build_rendition


class Command(BaseCommand):
    help = 'Write width-limited WebP copies for shop photos, heroes, banners, collections, and blog images.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Rebuild even when a current rendition row already exists.',
        )

    def handle(self, *args, **options):
        force = bool(options['force'])
        names = list(_source_names())
        built = skipped = unsupported = missing = 0
        for index, name in enumerate(names, start=1):
            result = build_rendition(name, force=force)
            if result == 'built':
                built += 1
            elif result == 'skipped':
                skipped += 1
            elif result == 'unsupported':
                unsupported += 1
            else:
                missing += 1
            if index % 25 == 0:
                self.stdout.write(f'{index}/{len(names)}')
        self.stdout.write(
            self.style.SUCCESS(
                f'Renditions: {built} built, {skipped} skipped, '
                f'{unsupported} unsupported, {missing} missing (of {len(names)}).'
            )
        )


def _source_names():
    seen = set()

    def add(field):
        name = (getattr(field, 'name', None) or '').strip()
        if name and name not in seen:
            seen.add(name)
            yield name

    product_qs = Product.objects.all().only(*PRODUCT_IMAGE_FIELDS).iterator(chunk_size=200)
    for product in product_qs:
        for fname in PRODUCT_IMAGE_FIELDS:
            yield from add(getattr(product, fname, None))

    for slide in HomeHeroSlide.objects.all().only('image').iterator():
        yield from add(slide.image)
    for banner in HomePromoBanner.objects.all().only('image').iterator():
        yield from add(banner.image)
    for collection in ProductCollection.objects.all().only('banner_image').iterator():
        yield from add(collection.banner_image)
    for group in ProductCollectionGroup.objects.all().only('image').iterator():
        yield from add(group.image)
    blog_fields = (
        'hero_image',
        'card_image',
        'article_image_1',
        'article_image_2',
        'article_image_3',
    )
    for post in BlogPost.objects.all().only(*blog_fields).iterator():
        for fname in blog_fields:
            yield from add(getattr(post, fname, None))
