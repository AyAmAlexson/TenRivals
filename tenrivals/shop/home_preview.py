"""JSON for the closed React shop-home preview.

The live home stays a Django template. This payload uses the same queries,
image renditions, and gettext strings so the preview can follow the active locale.
"""

from itertools import chain
from urllib.parse import urlencode

from django.contrib.staticfiles.storage import staticfiles_storage
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils.text import Truncator
from django.utils.translation import gettext as _

from .catalog_utils import (
    FEATURED_STOCK_BRANDS,
    annotate_stock_listing_quantity,
    annotate_stock_shelf_priority,
    order_products_by_effective_price,
    stock_catalog_storefront_queryset,
)
from .models import (
    BlogPost,
    HomeHeroContent,
    HomeHeroSlide,
    HomePromoBanner,
    Product,
    ProductType,
)
from .responsive_images import image_rendition
from .site_locale import django_lang_for_site_locale, shop_reverse

_MAX_HOME_FEATURED_BLOG = 12
_HOME_FEATURED_PRODUCTS = 10
_HOME_NEW_RACKETS = 4
_HOME_NEW_MENS_SHOES = 4
_HOME_NEW_WOMENS_SHOES = 2
_PRODUCT_SUBCLASS_SELECT = (
    'shoe',
    'racket',
    'apparel',
    'string',
    'bag',
    'balls',
    'accessory',
)


def build_home_preview_payload(request) -> dict:
    loc = getattr(request, 'site_locale', None)
    hero_row = HomeHeroContent.load()
    hero_text = hero_row.localized_strings(django_lang_for_site_locale(loc))
    stock_url = shop_reverse('shop:stock', site_locale=loc)
    return {
        'labels': _labels(),
        'hero': {
            'slides': [
                _slide(slide.image, slide.image_link_url, 1600, '100vw', index == 0)
                for index, slide in enumerate(HomeHeroSlide.objects.all()[:5])
            ],
            'headline': hero_text.headline,
            'subtext': hero_text.subtext,
            'cta': _optional_link(hero_text.cta_label, hero_text.cta_url),
            'secondary': _optional_link(
                hero_text.secondary_link_label,
                hero_text.secondary_link_url,
            ),
            'sticker': staticfiles_storage.url('assets/img/tr-sticker.png'),
        },
        'announce': (
            _('Free delivery in Tbilisi')
            + ' \u00a0|\u00a0 '
            + _('Pre-order from US & EU with official customs clearance')
        ),
        'categories': _categories(loc, stock_url),
        'featured': {
            'title': _('Featured Products'),
            'viewAll': stock_url,
            'controls': _('Featured products carousel controls'),
            'prev': _('Scroll featured products left'),
            'next': _('Scroll featured products right'),
            'tone': 'red',
            'products': [
                _product(request, product)
                for product in annotate_stock_listing_quantity(
                    Product.objects.filter(is_active=True, featured_product=True)
                    .select_related(*_PRODUCT_SUBCLASS_SELECT, 'category')
                    .order_by('-created_at')
                )[:_HOME_FEATURED_PRODUCTS]
            ],
        },
        'promos': _promos(),
        'arrivals': {
            'title': _('New Arrivals'),
            'viewAll': stock_url,
            'controls': _('New arrivals carousel controls'),
            'prev': _('Scroll new arrivals left'),
            'next': _('Scroll new arrivals right'),
            'tone': 'blue',
            'products': [_product(request, product) for product in _new_arrivals()],
        },
        'aurora': _aurora(loc),
        'stories': _stories(loc),
        'brands': _brands(loc, stock_url),
        'aboutHtml': render_to_string(
            'shop/includes/home_about_block.html',
            request=request,
        ),
    }


def _labels() -> dict:
    return {
        'viewAll': _('View all'),
        'categories': _('Shop categories'),
        'prevHero': _('Previous hero slide'),
        'nextHero': _('Next hero slide'),
        'heroPlaceholder': _('Hero image — add slides in Staff → Shop home'),
        'editProduct': 'Edit product',
        'comingSoon': 'Coming soon · Pre-order',
        'storyFallback': _('Ten Rivals'),
    }


def _categories(loc, stock_url) -> list:
    balls = shop_reverse('shop:stock_catalog_type', site_locale=loc, type_slug='balls')
    return [
        {'href': f'{stock_url}?type=RACKET', 'label': _('Rackets'), 'tone': 'red'},
        {'href': f'{stock_url}?type=M_SHOES', 'label': _('Shoes'), 'tone': 'teal'},
        {'href': f'{stock_url}?type=BAGS', 'label': _('Bags'), 'tone': 'blue'},
        {'href': f'{stock_url}?type=M_APP', 'label': _('Clothing'), 'tone': 'navy'},
        {'href': balls, 'label': _('Balls'), 'tone': 'slate'},
        {'href': f'{stock_url}?type=ACC_GEAR', 'label': _('Accessories'), 'tone': 'magenta'},
    ]


def _new_arrivals():
    stock_products = annotate_stock_shelf_priority(
        order_products_by_effective_price(
            stock_catalog_storefront_queryset()
            .select_related(*_PRODUCT_SUBCLASS_SELECT, 'category')
            .distinct()
        )
    ).order_by('-_shelf_first', '-in_stock', '-sort_price', 'id')
    return list(
        chain(
            stock_products.filter(type=ProductType.RACKET).order_by('-created_at')[
                :_HOME_NEW_RACKETS
            ],
            stock_products.filter(type=ProductType.MENS_SHOES).order_by('-created_at')[
                :_HOME_NEW_MENS_SHOES
            ],
            stock_products.filter(type=ProductType.WOMENS_SHOES).order_by('-created_at')[
                :_HOME_NEW_WOMENS_SHOES
            ],
        )
    )


def _promos() -> dict:
    alt_default = _('Promo banner')
    slides = []
    for index, banner in enumerate(HomePromoBanner.objects.all()[:5]):
        alt = (banner.internal_note or '').strip() or alt_default
        slides.append(
            _slide(
                banner.image,
                banner.image_link_url,
                1280,
                '(max-width:1200px) 100vw, 1200px',
                first=False,
                alt=alt,
                priority='low' if index == 0 else 'lazy',
            )
        )
    return {
        'slides': slides,
        'controls': _('Promo banner controls'),
        'prev': _('Previous promo banner'),
        'next': _('Next promo banner'),
        'goTo': _('Go to banner'),
    }


def _aurora(loc) -> dict:
    return {
        'kicker': _('Next drop'),
        'title': _('Pre-order gear from the US and EU'),
        'lead': _(
            'Rackets, shoes, and strings, cleared through customs and delivered in Tbilisi.'
        ),
        'primary': {
            'href': shop_reverse('shop:order_for_me', site_locale=loc),
            'label': _('Order for me'),
        },
        'secondary': {
            'href': shop_reverse('shop:info_payment', site_locale=loc),
            'label': _('Delivery and payment'),
        },
    }


def _stories(loc) -> dict:
    posts = BlogPost.objects.filter(
        is_published=True,
        is_featured_on_home=True,
    ).order_by('featured_sort_order', '-published_at', '-id')[:_MAX_HOME_FEATURED_BLOG]
    items = []
    for post in posts:
        lead = Truncator(strip_tags(post.lead or '')).words(28)
        items.append(
            {
                'href': shop_reverse(
                    'shop:blog_post',
                    site_locale=loc,
                    slug=post.slug,
                ),
                'title': post.title,
                'lead': lead,
                'cta': post.featured_cta_display,
                'image': _picture(
                    post.teaser_image,
                    640,
                    '(max-width:640px) 78vw, 320px',
                    post.title,
                    'lazy',
                ),
            }
        )
    return {
        'title': _('Featured Stories'),
        'viewAll': shop_reverse('shop:blog_index', site_locale=loc),
        'controls': _('Featured stories carousel controls'),
        'prev': _('Scroll stories left'),
        'next': _('Scroll stories right'),
        'tone': 'teal',
        'items': items,
    }


def _brands(loc, stock_url) -> dict:
    items = []
    for row in FEATURED_STOCK_BRANDS:
        label = row['label']
        items.append(
            {
                'href': f'{stock_url}?{urlencode({"cbrand": row["stock_brand"]})}',
                'title': _('Shop %(brand)s in stock') % {'brand': label},
                'src': staticfiles_storage.url(row['logo']),
                'alt': label,
                'scale': row.get('logo_optical_scale', 1),
            }
        )
    return {
        'title': _('Our Brands'),
        'viewAll': shop_reverse('shop:brands', site_locale=loc),
        'label': _('Featured tennis brands'),
        'items': items,
    }


def _product(request, product) -> dict:
    loc = getattr(request, 'site_locale', None)
    qty = int(getattr(product, 'stock_listing_qty', 0) or 0)
    channel = 'stock' if qty > 0 or product.in_stock else 'preorder'
    href = shop_reverse('shop:product_detail', product.pk, site_locale=loc)
    href = f'{href}?channel={channel}'
    edit = ''
    if request.user.is_staff:
        edit = shop_reverse('shop:product_edit', product.pk, site_locale=loc)
        edit = f'{edit}?{urlencode({"channel": channel, "next": request.get_full_path()})}'
    if qty > 0 and product.actual_price:
        price = {
            'kind': 'sale',
            'current': _money(product.actual_price),
            'old': _money(product.initial_price),
        }
    elif qty > 0:
        price = {'kind': 'regular', 'current': _money(product.initial_price)}
    else:
        price = {'kind': 'muted', 'label': 'Coming soon · Pre-order'}
    return {
        'href': href,
        'edit': edit,
        'brand': product.brand or '',
        'name': product.name,
        'color': product.color or '',
        'short': product.short_description or '',
        'type': product.get_type_display(),
        'image': _picture(
            product.main_image,
            640,
            '(max-width:640px) 58vw, 180px',
            product.name,
            'lazy',
        ),
        'price': price,
    }


def _slide(image, link_url, width, sizes, first, alt='', priority=None):
    return {
        'image': _picture(
            image,
            width,
            sizes,
            alt,
            priority or ('high' if first else 'lazy'),
        ),
        'link': _href(link_url),
    }


def _picture(image, width, sizes, alt, priority):
    pic = image_rendition(image, width) if image else None
    if not pic or not pic.src:
        return None
    return {
        'src': pic.src,
        'srcset': pic.srcset or '',
        'width': pic.width or None,
        'height': pic.height or None,
        'sizes': sizes,
        'alt': alt or '',
        'priority': priority,
    }


def _optional_link(label, url):
    href = _href(url)
    text = (label or '').strip()
    if not text or not href:
        return None
    return {'label': text, 'href': href['href'], 'external': href['external']}


def _href(url):
    raw = (url or '').strip()
    if not raw:
        return None
    low = raw.lower()
    if low.startswith(('http://', 'https://', '//')):
        href = raw
    else:
        href = raw if raw.startswith('/') else f'/{raw}'
    return {'href': href, 'external': low.startswith('http')}


def _money(value) -> str:
    if value is None:
        return ''
    return str(value)
