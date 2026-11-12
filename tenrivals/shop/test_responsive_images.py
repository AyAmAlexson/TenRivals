import io

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.template.loader import get_template, render_to_string
from django.test import RequestFactory, TestCase, override_settings
from PIL import Image

from shop.models import Product, ProductType
from shop.responsive_images import build_rendition, image_rendition

_STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}


def _jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (width, height), (180, 20, 20)).save(buf, format='JPEG', quality=85)
    return buf.getvalue()


@override_settings(STORAGES=_STORAGES, SHOP_BUILD_IMAGE_RENDITIONS=False)
class ResponsiveImageTests(TestCase):
    def test_build_writes_widths_under_the_source(self):
        default_storage.save('shop/products/racket.jpg', ContentFile(_jpeg(1000, 800)))
        self.assertEqual(build_rendition('shop/products/racket.jpg'), 'built')

        rendition = image_rendition(_Named('shop/products/racket.jpg'), default_width=640)
        self.assertIn('640w', rendition.srcset)
        self.assertIn('320w', rendition.srcset)
        self.assertNotIn('1600w', rendition.srcset)
        self.assertTrue(rendition.src.endswith('640.webp') or '640.webp' in rendition.src)
        self.assertIn('960.webp', rendition.full)
        self.assertEqual(rendition.width, 1000)
        self.assertEqual(rendition.height, 800)

    def test_svg_is_left_as_the_original(self):
        default_storage.save('shop/products/mark.svg', ContentFile(b'<svg xmlns="http://www.w3.org/2000/svg"/>'))
        rendition = image_rendition(_Named('shop/products/mark.svg'))
        self.assertIn('mark.svg', rendition.src)
        self.assertEqual(rendition.srcset, '')

    def test_render_does_not_open_the_original(self):
        default_storage.save('shop/products/ball.jpg', ContentFile(_jpeg(400, 400)))
        product = Product.objects.create(
            type=ProductType.BALLS,
            name='Ball',
            initial_price='10.00',
            image_1='shop/products/ball.jpg',
        )
        opens = {'n': 0}
        real_open = default_storage.open

        def counting_open(*args, **kwargs):
            opens['n'] += 1
            return real_open(*args, **kwargs)

        default_storage.open = counting_open
        try:
            request = RequestFactory().get('/ge_en/shop/')
            request.site_locale = 'ge_en'
            request.user = type('U', (), {'is_authenticated': False, 'is_staff': False})()
            html = render_to_string(
                'shop/includes/product_tile.html',
                {'p': product, 'browse_mode': 'stock', 'tile_fetch': 0, 'request': request},
                request=request,
            )
        finally:
            default_storage.open = real_open

        self.assertIn('ball.jpg', html)
        self.assertNotIn('srcset=', html)
        self.assertEqual(opens['n'], 0)

    def test_storefront_templates_compile(self):
        for name in (
            'shop/index.html',
            'shop/catalog_browse.html',
            'shop/product_detail.html',
            'shop/collection_detail.html',
            'shop/blog_post.html',
            'shop/blog_index.html',
            'shop/cart.html',
            'shop/checkout.html',
            'shop/includes/product_tile.html',
        ):
            get_template(name)

    def test_second_build_is_skipped(self):
        default_storage.save('shop/products/again.jpg', ContentFile(_jpeg(320, 200)))
        self.assertEqual(build_rendition('shop/products/again.jpg'), 'built')
        self.assertEqual(build_rendition('shop/products/again.jpg'), 'skipped')


class _Named:
    def __init__(self, name: str):
        self.name = name
