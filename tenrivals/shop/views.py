import json
from decimal import Decimal
from itertools import chain
from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.core.mail import send_mail, EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.db.utils import DatabaseError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from .cart_session import (
    CART_TTL_DAYS,
    build_cart_page_rows,
    get_cart,
    product_eligible_for_storefront_cart,
    remove_line,
    remove_line_at_index_verified,
    remove_line_by_product_variant,
    save_cart,
    set_cart_promo,
    set_line_qty,
    try_add_to_cart,
)
from .catalog_utils import (
    FEATURED_STOCK_BRANDS,
    annotate_stock_listing_quantity,
    distinct_brands_for_type,
    filter_products_by_listing_channel,
    order_products_by_effective_price,
    stock_catalog_base_queryset,
)
from .forms import (
    AccessoryForm,
    ApparelForm,
    BagForm,
    BallsForm,
    ProductForm,
    RacketForm,
    ShoeForm,
    StringForm,
)
from .models import (
    Accessory,
    Apparel,
    Bag,
    Balls,
    Category,
    CourtSurface,
    Gender,
    BlogPost,
    HomeHeroContent,
    HomeHeroSlide,
    Customer,
    OrderForMe,
    OrderForMeItem,
    Product,
    ProductCollection,
    ProductListing,
    ProductListingChannel,
    ProductType,
    PromoCode,
    PromoRedemption,
    Racket,
    SalesOrder,
    SalesOrderLine,
    Shoe,
    String,
    HomePromoBanner,
)
from .sales_order_stock import (
    get_variant_qty_map,
    product_requires_variant,
    take_lines_from_stock,
    validate_order_line_demands,
)
from .sales_order_utils import (
    allocate_invoice_number,
    allocate_order_for_me_number,
    gross_split_vat_net,
    line_amounts,
    product_unit_gross_price,
)
from .promo_codes import evaluate_promo_for_cart_rows, normalize_promo_code

_PRODUCT_SUBCLASS_SELECT = (
    'shoe',
    'racket',
    'apparel',
    'string',
    'bag',
    'balls',
    'accessory',
)

_SHOE_TYPES = frozenset(
    {
        ProductType.MENS_SHOES,
        ProductType.WOMENS_SHOES,
        ProductType.JUNIOR_SHOES,
    }
)
_APPAREL_TYPES = frozenset(
    {
        ProductType.MENS_APPAREL,
        ProductType.WOMENS_APPAREL,
        ProductType.JUNIOR_APPAREL,
    }
)
_ACCESSORY_TYPES = frozenset(
    {ProductType.GRIPS, ProductType.DAMPENERS, ProductType.ACCESSORIES}
)

# Virtual catalog filter: accessories + grips + strings (separate ProductTypes in DB).
CATALOG_ACCESSORIES_EQUIPMENT_TYPE = 'ACC_GEAR'
_CATALOG_ACCESSORIES_EQUIPMENT_TYPES = frozenset(
    {
        ProductType.ACCESSORIES,
        ProductType.GRIPS,
        ProductType.DAMPENERS,
        ProductType.STRINGS,
    }
)


def _form_class_for_product_type(type_code: str | None):
    if not type_code:
        return ProductForm
    if type_code in _SHOE_TYPES:
        return ShoeForm
    if type_code == ProductType.RACKET:
        return RacketForm
    if type_code in _APPAREL_TYPES:
        return ApparelForm
    if type_code == ProductType.STRINGS:
        return StringForm
    if type_code == ProductType.BAGS:
        return BagForm
    if type_code == ProductType.BALLS:
        return BallsForm
    if type_code in _ACCESSORY_TYPES:
        return AccessoryForm
    return ProductForm


def _edit_instance_and_form(base: Product):
    """Resolve MTI child instance and matching ModelForm for staff edit."""
    t = base.type
    try:
        if t in _SHOE_TYPES:
            return base.shoe, ShoeForm
        if t == ProductType.RACKET:
            return base.racket, RacketForm
        if t in _APPAREL_TYPES:
            return base.apparel, ApparelForm
        if t == ProductType.STRINGS:
            return base.string, StringForm
        if t == ProductType.BAGS:
            return base.bag, BagForm
        if t == ProductType.BALLS:
            return base.balls, BallsForm
        if t in _ACCESSORY_TYPES:
            return base.accessory, AccessoryForm
    except (
        Shoe.DoesNotExist,
        Racket.DoesNotExist,
        Apparel.DoesNotExist,
        String.DoesNotExist,
        Bag.DoesNotExist,
        Balls.DoesNotExist,
        Accessory.DoesNotExist,
    ):
        pass
    return base, ProductForm


def _parse_listing_channel_param(raw):
    if raw in (ProductListingChannel.STOCK, 'stock', 'STOCK'):
        return ProductListingChannel.STOCK
    if raw in (ProductListingChannel.PREORDER, 'preorder', 'PREORDER'):
        return ProductListingChannel.PREORDER
    return None


def _product_on_listing_channel(product: Product, channel: str) -> bool:
    """Match catalog rules: if no rows exist for a channel, any product is eligible."""
    if not ProductListing.objects.filter(channel=channel).exists():
        return True
    return product.listings.filter(channel=channel).exists()


def _stock_listing_quantity(product: Product) -> int:
    row = product.listings.filter(channel=ProductListingChannel.STOCK).first()
    return int(row.quantity) if row else 0


def _resolve_pdp_display_mode(request, product: Product) -> str:
    """'stock' | 'preorder' — drives breadcrumbs and price copy."""
    requested = _parse_listing_channel_param(
        request.GET.get('channel') or request.GET.get('from')
    )
    on_stock = _product_on_listing_channel(product, ProductListingChannel.STOCK)
    on_preorder = _product_on_listing_channel(product, ProductListingChannel.PREORDER)
    qty = _stock_listing_quantity(product)

    if requested == ProductListingChannel.STOCK:
        return 'stock'
    if requested == ProductListingChannel.PREORDER:
        return 'preorder'

    if qty > 0 and on_stock:
        return 'stock'
    if on_preorder:
        return 'preorder'
    if on_stock:
        return 'stock'
    return 'preorder'


_PDP_BREADCRUMB_TYPE_LABELS = {
    ProductType.RACKET: 'Rackets',
    ProductType.MENS_APPAREL: "Men's apparel",
    ProductType.WOMENS_APPAREL: "Women's apparel",
    ProductType.JUNIOR_APPAREL: 'Junior apparel',
    ProductType.MENS_SHOES: "Men's Shoes",
    ProductType.WOMENS_SHOES: "Women's Shoes",
    ProductType.JUNIOR_SHOES: 'Junior Shoes',
    ProductType.BAGS: 'Bags & covers',
    ProductType.STRINGS: 'Strings',
    ProductType.GRIPS: 'Grips',
    ProductType.DAMPENERS: 'Dampeners',
    ProductType.BALLS: 'Balls',
    ProductType.ACCESSORIES: 'Accessories',
    ProductType.OTHER: 'Other',
}


def _pdp_breadcrumb_type_label(product: Product) -> str:
    return _PDP_BREADCRUMB_TYPE_LABELS.get(
        product.type,
        product.get_type_display(),
    )


def _save_listing_from_form(product, form):
    ch = form.cleaned_data.get('listing_channel')
    if not ch:
        return
    qty = form.cleaned_data.get('listing_quantity')
    ProductListing.objects.update_or_create(
        product=product,
        channel=ch,
        defaults={'quantity': max(0, int(qty if qty is not None else 0))},
    )


def _redirect_after_product_form_save(request, product, form):
    """Honor `next` if internal; otherwise show the public product page in the right catalog context."""
    next_url = _safe_internal_redirect(request, request.POST.get('next'))
    if next_url:
        return redirect(next_url)
    ch = (
        form.cleaned_data.get('listing_channel') or ProductListingChannel.PREORDER
    )
    channel_q = (
        'stock' if ch == ProductListingChannel.STOCK else 'preorder'
    )
    return redirect(
        f'{reverse("shop:product_detail", args=[product.pk])}?channel={channel_q}'
    )


def _edit_listing_channel_and_qty(product: Product, query_channel: str | None):
    """Default catalog row for staff edit: ?channel= wins; else stock listing if any; else preorder."""
    if query_channel:
        row = product.listings.filter(channel=query_channel).first()
        return query_channel, int(row.quantity) if row else 0
    stock_row = product.listings.filter(channel=ProductListingChannel.STOCK).first()
    if stock_row:
        return ProductListingChannel.STOCK, int(stock_row.quantity)
    preorder_row = product.listings.filter(channel=ProductListingChannel.PREORDER).first()
    if preorder_row:
        return ProductListingChannel.PREORDER, int(preorder_row.quantity)
    return ProductListingChannel.PREORDER, 0


def _safe_internal_redirect(request, url: str | None):
    if not url:
        return None
    u = url.strip()
    if u.startswith('/') and not u.startswith('//'):
        return u
    allowed = {h for h in settings.ALLOWED_HOSTS if h and h != '*'}
    if not allowed:
        allowed = {request.get_host()}
    if url_has_allowed_host_and_scheme(
        url,
        allowed_hosts=allowed,
        require_https=request.is_secure(),
    ):
        return url
    return None

_MAX_HOME_FEATURED_BLOG = 12


def index(request):
    hero_content = HomeHeroContent.load()
    hero_slides = list(HomeHeroSlide.objects.all()[:5])
    promo_banners = list(HomePromoBanner.objects.all()[:5])
    featured_blog_posts = list(
        BlogPost.objects.filter(
            is_published=True,
            is_featured_on_home=True,
        ).order_by('featured_sort_order', '-published_at', '-id')[:_MAX_HOME_FEATURED_BLOG]
    )
    active_products = Product.objects.filter(is_active=True).select_related(
        *_PRODUCT_SUBCLASS_SELECT,
        'category',
    )
    stock_products = (
        annotate_stock_listing_quantity(
            stock_catalog_base_queryset().select_related(
                *_PRODUCT_SUBCLASS_SELECT,
                'category',
            )
        )
        .filter(stock_listing_qty__gt=0)
        .distinct()
    )
    featured_products = list(
        annotate_stock_listing_quantity(
            active_products.filter(featured_product=True).order_by('-created_at')
        )[:5]
    )
    new_arrivals = list(
        chain(
            stock_products.filter(type=ProductType.RACKET).order_by('-created_at')[:2],
            stock_products.filter(type=ProductType.MENS_SHOES).order_by('-created_at')[:2],
            stock_products.filter(type=ProductType.WOMENS_SHOES).order_by(
                '-created_at'
            )[:1],
        )
    )
    return render(
        request,
        'shop/index.html',
        {
            'hero_content': hero_content,
            'hero_slides': hero_slides,
            'promo_banners': promo_banners,
            'featured_blog_posts': featured_blog_posts,
            'featured_products': featured_products,
            'new_arrivals': new_arrivals,
            'featured_stock_brands': FEATURED_STOCK_BRANDS,
        },
    )


def brands(request):
    return render(
        request,
        'shop/brands.html',
        {'featured_stock_brands': FEATURED_STOCK_BRANDS},
    )


def _pdp_variant_option_dicts(product: Product):
    m = get_variant_qty_map(product)
    return [{'label': k, 'qty': int(v)} for k, v in sorted(m.items()) if int(v or 0) > 0]


@ensure_csrf_cookie
def product_detail(request, pk):
    product = get_object_or_404(
        Product.objects.select_related(
            *_PRODUCT_SUBCLASS_SELECT,
            'category',
        ).prefetch_related('listings'),
        pk=pk,
        is_active=True,
    )
    gallery = []
    for name in ("image_1", "image_2", "image_3", "image_4", "image_5"):
        f = getattr(product, name)
        if f:
            gallery.append(f.url)
    pdp_mode = _resolve_pdp_display_mode(request, product)
    stock_listing_qty = _stock_listing_quantity(product)
    variant_opts = _pdp_variant_option_dicts(product)
    pdp_cart_enabled = (
        pdp_mode == 'stock'
        and stock_listing_qty > 0
        and product_eligible_for_storefront_cart(product)
        and (not product_requires_variant(product) or bool(variant_opts))
    )
    pdp_requires_size_pick = bool(
        pdp_cart_enabled and product_requires_variant(product) and variant_opts
    )
    return render(
        request,
        "shop/product_detail.html",
        {
            "product": product,
            "pdp_gallery": gallery,
            "pdp_mode": pdp_mode,
            "stock_listing_qty": stock_listing_qty,
            "pdp_type_breadcrumb_label": _pdp_breadcrumb_type_label(product),
            "pdp_cart_enabled": pdp_cart_enabled,
            "pdp_requires_size_pick": pdp_requires_size_pick,
            "pdp_variant_options_json": json.dumps(variant_opts),
            "pdp_variant_options": variant_opts,
        },
    )


def _catalog_browse_context(request, browse_mode: str):
    if browse_mode not in ('preorder', 'stock'):
        raise ValueError(browse_mode)
    channel = (
        ProductListingChannel.PREORDER
        if browse_mode == 'preorder'
        else ProductListingChannel.STOCK
    )

    category_slug = request.GET.get('category')
    type_code = request.GET.get('type')
    if type_code == 'RACKETS':
        type_code = ProductType.RACKET
    gender_filter = request.GET.get('g', 'all')
    surface_filter = request.GET.get('surf', 'all')
    shoe_brand = request.GET.get('sbrand', 'all')
    racket_brand = request.GET.get('brand', 'all')
    racket_weight = request.GET.get('w', 'all')
    racket_head = request.GET.get('head', 'all')
    racket_pattern = request.GET.get('pat', 'all')
    catalog_brand = (request.GET.get('cbrand') or '').strip()

    categories = Category.objects.all().order_by('name')
    products = Product.objects.filter(is_active=True)
    active_tab = 'all'

    if category_slug:
        products = products.filter(category__slug=category_slug)
        active_tab = f'cat:{category_slug}'
    elif type_code == CATALOG_ACCESSORIES_EQUIPMENT_TYPE:
        products = products.filter(type__in=_CATALOG_ACCESSORIES_EQUIPMENT_TYPES)
        active_tab = f'type:{CATALOG_ACCESSORIES_EQUIPMENT_TYPE}'
    elif type_code:
        products = products.filter(type=type_code)
        active_tab = f'type:{type_code}'

    shoe_types = {
        ProductType.MENS_SHOES,
        ProductType.WOMENS_SHOES,
        ProductType.JUNIOR_SHOES,
    }
    show_shoe_filters = type_code in shoe_types
    show_racket_filters = type_code == ProductType.RACKET

    shoe_brands = []
    racket_brands = []
    racket_patterns = []

    if show_shoe_filters:
        if gender_filter == 'm':
            products = products.filter(shoe__gender__in=[Gender.MEN, Gender.UNISEX])
        elif gender_filter == 'w':
            products = products.filter(shoe__gender__in=[Gender.WOMEN, Gender.UNISEX])

        surf_map = {
            'clay': CourtSurface.CLAY,
            'hard': CourtSurface.HARD,
            'allcourt': CourtSurface.ALL_COURT,
            'grass': CourtSurface.GRASS,
            'padel': CourtSurface.PADEL,
        }
        if surface_filter in surf_map:
            products = products.filter(shoe__surface=surf_map[surface_filter])

        if browse_mode == 'stock':
            shoe_brands = distinct_brands_for_type(
                stock_catalog_base_queryset(), type_code
            )
        else:
            shoe_brands = list(
                Product.objects.filter(is_active=True, type=type_code)
                .exclude(brand__isnull=True)
                .exclude(brand__exact='')
                .values_list('brand', flat=True)
                .distinct()
                .order_by('brand')
            )
        if shoe_brand != 'all':
            products = products.filter(brand=shoe_brand)

    if show_racket_filters:
        if browse_mode == 'stock':
            racket_brands = distinct_brands_for_type(
                stock_catalog_base_queryset(), ProductType.RACKET
            )
        else:
            racket_brands = list(
                Product.objects.filter(is_active=True, type=ProductType.RACKET)
                .exclude(brand__isnull=True)
                .exclude(brand__exact='')
                .values_list('brand', flat=True)
                .distinct()
                .order_by('brand')
            )
        racket_patterns = list(
            Racket.objects.filter(is_active=True)
            .exclude(string_pattern__isnull=True)
            .exclude(string_pattern__exact='')
            .values_list('string_pattern', flat=True)
            .distinct()
            .order_by('string_pattern')
        )

        if racket_brand != 'all':
            products = products.filter(brand=racket_brand)
        if racket_weight == 'lt280':
            products = products.filter(racket__weight_grams__lt=280)
        elif racket_weight == '280_299':
            products = products.filter(
                racket__weight_grams__gte=280, racket__weight_grams__lte=299
            )
        elif racket_weight == '300':
            products = products.filter(racket__weight_grams=300)
        elif racket_weight == 'ge301':
            products = products.filter(racket__weight_grams__gte=301)

        if racket_head == 'lt100':
            products = products.filter(racket__head_size_sq_in__lt=100)
        elif racket_head == '100':
            products = products.filter(racket__head_size_sq_in=100)
        elif racket_head == 'ge101':
            products = products.filter(racket__head_size_sq_in__gte=101)

        if racket_pattern != 'all':
            products = products.filter(racket__string_pattern=racket_pattern)

    if catalog_brand:
        products = products.filter(brand__iexact=catalog_brand)
        if not category_slug and not type_code:
            active_tab = f'cbrand:{catalog_brand}'

    products = filter_products_by_listing_channel(products, channel)
    products = products.select_related(*_PRODUCT_SUBCLASS_SELECT, 'category')
    if browse_mode == 'stock':
        products = annotate_stock_listing_quantity(products)
    products = order_products_by_effective_price(products)
    products = products.order_by('-in_stock', '-sort_price', 'id')
    type_tabs = [(choice.value, choice.label) for choice in ProductType]

    product_count = products.count()
    storefront_collections = []
    if browse_mode == 'stock':
        try:
            storefront_collections = list(
                ProductCollection.objects.filter(is_archived=False)
                .prefetch_related('products')
                .order_by('title', 'id')
            )
        except DatabaseError:
            # Local/staging DB can lag behind migrations; keep stock page available.
            storefront_collections = []

    return {
        'browse_mode': browse_mode,
        'product_count': product_count,
        'categories': categories,
        'type_tabs': type_tabs,
        'products': products,
        'active_tab': active_tab,
        'active_type_code': type_code,
        'show_shoe_filters': show_shoe_filters,
        'gender_active': gender_filter,
        'surface_active': surface_filter,
        'shoe_brands': shoe_brands if show_shoe_filters else [],
        'shoe_brand_active': shoe_brand,
        'show_racket_filters': show_racket_filters,
        'racket_brands': racket_brands,
        'racket_brand_active': racket_brand,
        'racket_weight_active': racket_weight,
        'racket_head_active': racket_head,
        'racket_patterns': racket_patterns,
        'racket_pattern_active': racket_pattern,
        'catalog_brand': catalog_brand,
        'cbrand_qs': f'&cbrand={quote(catalog_brand)}' if catalog_brand else '',
        'storefront_collections': storefront_collections,
    }


def product_search(request):
    q = (request.GET.get('q') or '').strip()
    channel = _parse_listing_channel_param((request.GET.get('channel') or '').strip())

    products = Product.objects.filter(is_active=True).select_related(
        *_PRODUCT_SUBCLASS_SELECT,
        'category',
    )
    if q:
        products = products.filter(
            Q(name__icontains=q)
            | Q(brand__icontains=q)
            | Q(short_description__icontains=q)
            | Q(description__icontains=q)
            | Q(sku__icontains=q)
            | Q(shoe__color__icontains=q)
        ).distinct()
    else:
        products = products.none()

    if channel == ProductListingChannel.STOCK:
        products = filter_products_by_listing_channel(
            products, ProductListingChannel.STOCK
        )
    elif channel == ProductListingChannel.PREORDER:
        products = filter_products_by_listing_channel(
            products, ProductListingChannel.PREORDER
        )

    # Always annotate stock qty so product tiles can hide shelf prices when qty is 0
    # (unscoped search used browse_mode "home" before and always showed prices).
    products = annotate_stock_listing_quantity(products)

    products = order_products_by_effective_price(products)
    if channel == ProductListingChannel.STOCK:
        tile_mode = 'stock'
        search_channel = 'stock'
    elif channel == ProductListingChannel.PREORDER:
        tile_mode = 'preorder'
        search_channel = 'preorder'
    else:
        tile_mode = 'stock'
        search_channel = ''

    return render(
        request,
        'shop/search_results.html',
        {
            'q': q,
            'products': products,
            'search_channel': search_channel,
            'tile_browse_mode': tile_mode,
        },
    )


def collection_detail(request, slug):
    collection = get_object_or_404(
        ProductCollection.objects.prefetch_related('products', 'groups', 'groups__products'),
        slug=slug,
        is_archived=False,
    )
    active_groups = list(
        collection.groups.all().order_by('sort_order', 'id')[:5]
    )
    group_sections = []
    union_ids = set()
    for grp in active_groups:
        grp_products = (
            Product.objects.filter(
                pk__in=grp.products.values('pk'),
                is_active=True,
            ).select_related(*_PRODUCT_SUBCLASS_SELECT, 'category')
        )
        grp_products = order_products_by_effective_price(
            annotate_stock_listing_quantity(grp_products)
        )
        grp_ids = list(grp_products.values_list('pk', flat=True))
        union_ids.update(grp_ids)
        group_sections.append(
            {
                'group': grp,
                'products': list(grp_products),
                'product_count': len(grp_ids),
            }
        )

    all_products = (
        Product.objects.filter(pk__in=union_ids, is_active=True)
        .select_related(*_PRODUCT_SUBCLASS_SELECT, 'category')
    )
    all_products = list(order_products_by_effective_price(annotate_stock_listing_quantity(all_products)))
    if not group_sections and collection.products.exists():
        fallback = (
            Product.objects.filter(pk__in=collection.products.values('pk'), is_active=True)
            .select_related(*_PRODUCT_SUBCLASS_SELECT, 'category')
        )
        fallback = list(order_products_by_effective_price(annotate_stock_listing_quantity(fallback)))
        group_sections.append(
            {
                'group': None,
                'products': fallback,
                'product_count': len(fallback),
            }
        )
        all_products = fallback

    product_count = len(all_products)
    return render(
        request,
        'shop/collection_detail.html',
        {
            'collection': collection,
            'group_sections': group_sections,
            'all_products': all_products,
            'product_count': product_count,
            'browse_mode': 'stock',
        },
    )


def stock(request):
    return render(
        request,
        'shop/catalog_browse.html',
        _catalog_browse_context(request, 'stock'),
    )


def preorder(request):
    return render(
        request,
        'shop/catalog_browse.html',
        _catalog_browse_context(request, 'preorder'),
    )


def _order_for_me_initial_contact(request):
    if request.user.is_authenticated:
        return {
            'first_name': (request.user.first_name or '').strip(),
            'last_name': (request.user.last_name or '').strip(),
            'email': (request.user.email or '').strip(),
            'phone': (getattr(request.user, 'mobile', '') or '').strip(),
            'telegram': (getattr(request.user, 'telegram', '') or '').strip(),
            'contact_method': 'EMAIL',
            'general_comment': '',
            'agree_terms': False,
        }
    return {
        'first_name': '',
        'last_name': '',
        'email': '',
        'phone': '',
        'telegram': '',
        'contact_method': 'EMAIL',
        'general_comment': '',
        'agree_terms': False,
    }


def _order_for_me_items_from_post(request):
    items = []
    for idx in range(1, 7):
        url = (request.POST.get(f'item_url_{idx}') or '').strip()
        comment = (request.POST.get(f'item_comment_{idx}') or '').strip()
        if not url:
            continue
        items.append({'item_url': url, 'item_comment': comment, 'sort_order': idx})
    return items


def _send_order_for_me_staff_email(request, order: OrderForMe):
    website = (getattr(settings, 'WEBSITE_URL', '') or '').rstrip('/')
    staff_path = reverse('administration:staff_order_for_me_list')
    if website:
        staff_url = f'{website}{staff_path}'
    else:
        staff_url = request.build_absolute_uri(staff_path)
    lines = [
        f'Order: {order.order_number}',
        f'Status: {order.get_status_display()}',
        f'Customer: {order.first_name} {order.last_name}',
        f'Email: {order.email}',
        f'Phone: {order.phone}',
        f'Telegram: {order.telegram or "—"}',
        f'Contact method: {order.get_contact_method_display()}',
        f'General comment: {order.general_comment or "—"}',
        '',
        'Items:',
    ]
    for item in order.items.all():
        lines.append(f'- {item.item_url}')
        lines.append(f'  Comment: {item.item_comment or "—"}')
    lines.extend(['', f'Staff list: {staff_url}'])
    send_mail(
        subject='NEW Order For Me form submitted',
        message='\n'.join(lines),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None) or 'no-reply@tenrivals.com',
        recipient_list=_ORDER_FOR_ME_EMAILS,
        fail_silently=True,
    )


@login_required
def order_for_me_create(request):
    contact = _order_for_me_initial_contact(request)
    items = [{'item_url': '', 'item_comment': ''}]
    max_items = 6
    success_modal = request.GET.get('submitted') == '1'

    if request.method == 'POST':
        contact = {
            'first_name': (request.POST.get('first_name') or '').strip(),
            'last_name': (request.POST.get('last_name') or '').strip(),
            'email': (request.POST.get('email') or '').strip(),
            'phone': (request.POST.get('phone') or '').strip(),
            'telegram': (request.POST.get('telegram') or '').strip(),
            'contact_method': (request.POST.get('contact_method') or 'EMAIL').strip().upper(),
            'general_comment': (request.POST.get('general_comment') or '').strip(),
            'agree_terms': request.POST.get('agree_terms') == '1',
        }
        items = _order_for_me_items_from_post(request)
        if not items:
            items = [{'item_url': '', 'item_comment': ''}]

        errors = []
        if not contact['first_name'] or not contact['last_name']:
            errors.append('First and last name are required.')
        if not contact['email'] or '@' not in contact['email']:
            errors.append('Valid email is required.')
        if not contact['phone']:
            errors.append('Phone is required.')
        if contact['contact_method'] not in {'EMAIL', 'WHATSAPP', 'TELEGRAM'}:
            errors.append('Select a valid contact method.')
        if not contact['agree_terms']:
            errors.append('You need to agree to Terms and Privacy Policy.')
        if len(items) > max_items:
            errors.append('You can submit up to 6 item links.')
        for item in items:
            if not (item['item_url'].startswith('http://') or item['item_url'].startswith('https://')):
                errors.append(f'Please provide a valid URL: {item["item_url"]}')
                break

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            with transaction.atomic():
                customer, _ = Customer.objects.get_or_create(
                    user=request.user,
                    defaults={
                        'first_name': contact['first_name'],
                        'last_name': contact['last_name'],
                        'phone': contact['phone'],
                        'email': contact['email'],
                        'tg_account': contact['telegram'],
                    },
                )
                customer.first_name = contact['first_name']
                customer.last_name = contact['last_name']
                customer.phone = contact['phone']
                customer.email = contact['email']
                customer.tg_account = contact['telegram']
                customer.save(
                    update_fields=[
                        'first_name',
                        'last_name',
                        'phone',
                        'email',
                        'tg_account',
                        'updated_at',
                    ]
                )

                order = OrderForMe.objects.create(
                    order_number=allocate_order_for_me_number(timezone.localdate().year),
                    customer=customer,
                    first_name=contact['first_name'],
                    last_name=contact['last_name'],
                    email=contact['email'],
                    phone=contact['phone'],
                    telegram=contact['telegram'],
                    contact_method=contact['contact_method'],
                    general_comment=contact['general_comment'],
                    status=OrderForMe.Status.SUBMITTED,
                )
                OrderForMeItem.objects.bulk_create(
                    [
                        OrderForMeItem(
                            order=order,
                            sort_order=item['sort_order'],
                            item_url=item['item_url'],
                            item_comment=item['item_comment'],
                        )
                        for item in items
                    ]
                )
            order = OrderForMe.objects.prefetch_related('items').get(pk=order.pk)
            _send_order_for_me_staff_email(request, order)
            return redirect(f'{reverse("shop:order_for_me")}?' + urlencode({'submitted': '1'}))

    return render(
        request,
        'shop/order_for_me.html',
        {
            'contact': contact,
            'items': items,
            'max_items': max_items,
            'success_modal': success_modal,
        },
    )


def blog_index(request):
    posts = BlogPost.objects.filter(is_published=True).order_by('-published_at', '-id')
    return render(
        request,
        'shop/blog_index.html',
        {'blog_posts': posts},
    )


def blog_post(request, slug):
    post = get_object_or_404(
        BlogPost,
        slug=slug,
        is_published=True,
    )
    product_slots = [
        post.featured_product_1_id,
        post.featured_product_2_id,
        post.featured_product_3_id,
        post.featured_product_4_id,
        post.featured_product_5_id,
    ]
    slot_ids = [pid for pid in product_slots if pid]
    products_by_id = {
        p.id: p
        for p in Product.objects.filter(id__in=slot_ids, is_active=True).select_related(
            *_PRODUCT_SUBCLASS_SELECT,
            'category',
        )
    }
    featured_products = [products_by_id[pid] for pid in slot_ids if pid in products_by_id]

    def _p(text: str) -> str:
        return (text or '').strip()

    def _add_paragraph(sections, text: str):
        t = _p(text)
        if t:
            sections.append({'type': 'paragraph', 'text': t})

    article_sections = []
    # Order: text 1 → optional inline img 1 → quote → text 2 → img 2 → CTA middle → text 3 → img 3 → products → text 4 → CTA end
    _add_paragraph(article_sections, post.body_block_1)
    if post.article_image_1:
        article_sections.append({"type": "figure", "image": post.article_image_1})

    if _p(post.quote_text):
        article_sections.append(
            {
                'type': 'quote',
                'text': post.quote_text.strip(),
                'author': _p(post.quote_author),
            }
        )

    _add_paragraph(article_sections, post.body_block_2)
    if post.article_image_2:
        article_sections.append({"type": "figure", "image": post.article_image_2})

    if _p(post.cta_mid_button_label) and _p(post.cta_mid_button_url):
        article_sections.append(
            {
                'type': 'cta',
                'text': _p(post.cta_mid_text),
                'label': post.cta_mid_button_label.strip(),
                'url': post.cta_mid_button_url.strip(),
            }
        )

    _add_paragraph(article_sections, post.body_block_3)
    if post.article_image_3:
        article_sections.append({"type": "figure", "image": post.article_image_3})

    if featured_products:
        article_sections.append({'type': 'products', 'products': featured_products})

    _add_paragraph(article_sections, post.body_block_4)

    if _p(post.cta_end_button_label) and _p(post.cta_end_button_url):
        article_sections.append(
            {
                'type': 'cta',
                'text': _p(post.cta_end_text),
                'label': post.cta_end_button_label.strip(),
                'url': post.cta_end_button_url.strip(),
            }
        )

    if not article_sections and _p(post.body):
        article_sections.append({'type': 'paragraph', 'text': post.body.strip()})

    return render(
        request,
        'shop/blog_post.html',
        {'post': post, 'article_sections': article_sections},
    )


_SHOP_STATIC_PAGE_TEMPLATES = {
    'delivery': 'shop/info/delivery.html',
    'payment': 'shop/info/payment.html',
    'returns': 'shop/info/returns.html',
    'size_guide': 'shop/info/size_guide.html',
    'contacts': 'shop/info/contacts.html',
    'terms': 'shop/legal/terms.html',
    'privacy': 'shop/legal/privacy.html',
    'dmca': 'shop/legal/dmca.html',
    'company': 'shop/legal/company.html',
    'accessibility': 'shop/legal/accessibility.html',
    'cookies': 'shop/legal/cookies.html',
    'sitemap': 'shop/legal/sitemap.html',
}


def shop_info_page(request, page_key: str):
    template = _SHOP_STATIC_PAGE_TEMPLATES.get(page_key)
    if template is None:
        raise Http404('Page not found')
    context = {}
    if page_key == 'sitemap':
        try:
            from .models import ProductCollection

            context['sitemap_collections'] = list(
                ProductCollection.objects.filter(is_archived=False).order_by(
                    'title', 'id'
                )
            )
        except DatabaseError:
            context['sitemap_collections'] = []
    return render(request, template, context)


def robots_txt(request):
    """Crawl hints for bots; Sitemap URL follows the current host (staging-friendly)."""
    from django.urls import reverse

    lines = [
        'User-agent: *',
        '',
        '# Admin & internal apps',
        'Disallow: /admin/',
        'Disallow: /administration/',
        'Disallow: /league/',
        'Disallow: /__debug__/',
        '',
        '# Staff / editor flows',
        'Disallow: /shop/add',
        'Disallow: /shop/edit',
        '',
        '# Account & auth (no SEO value)',
        'Disallow: /persons/my_account/',
        'Disallow: /persons/delete/',
        'Disallow: /persons/email/',
        'Disallow: /persons/telegram/',
        'Disallow: /persons/verify-telegram/',
        'Disallow: /persons/resend-verification-code/',
        'Disallow: /persons/staff/',
        'Disallow: /persons/superuser/',
        'Disallow: /persons/accounts/',
        'Disallow: /accounts/',
        '',
        f'Sitemap: {request.build_absolute_uri(reverse("sitemap_xml"))}',
        '',
    ]
    return HttpResponse('\n'.join(lines), content_type='text/plain; charset=utf-8')


def _product_create_pick_type_qs(return_next: str, channel_raw: str) -> str:
    parts = []
    if return_next:
        parts.append(f'next={quote(return_next)}')
    if channel_raw:
        parts.append(f'channel={quote(channel_raw)}')
    return '&'.join(parts)


@staff_member_required
def product_create(request):
    """Create flow: GET /add → pick product type; GET /add?type=X → type-specific ModelForm."""
    return_next = request.GET.get('next', '')
    channel_raw = request.GET.get('channel', '')
    edit_channel = _parse_listing_channel_param(channel_raw)
    type_code = request.GET.get('type')
    pick_type_qs = _product_create_pick_type_qs(return_next, channel_raw)
    form_back_url = _safe_internal_redirect(request, return_next) or reverse(
        'shop:stock'
    )

    if request.method == 'GET' and not type_code:
        return render(
            request,
            'shop/product_create_pick_type.html',
            {
                'return_next': return_next,
                'channel_raw': channel_raw,
                'pick_type_qs': pick_type_qs,
                'product_types': ProductType.choices,
                'form_back_url': form_back_url,
            },
        )

    type_labels = dict(ProductType.choices)
    if request.method == 'GET' and type_code not in type_labels:
        base = reverse('shop:product_create')
        if pick_type_qs:
            return redirect(f'{base}?{pick_type_qs}')
        return redirect(base)

    if request.method == 'POST':
        FormClass = _form_class_for_product_type(request.POST.get('type'))
        form = FormClass(request.POST, request.FILES)
        if form.is_valid():
            obj = form.save()
            _save_listing_from_form(obj, form)
            return _redirect_after_product_form_save(request, obj, form)
    else:
        FormClass = _form_class_for_product_type(type_code)
        form = FormClass(
            initial={'type': type_code},
            default_listing_channel=(
                edit_channel or ProductListingChannel.PREORDER
            ),
            listing_quantity=0,
        )

    display_type = (
        request.POST.get('type') if request.method == 'POST' else type_code
    )

    return render(
        request,
        'shop/product_form.html',
        {
            'form': form,
            'is_edit': False,
            'return_next': return_next,
            'pick_type_qs': pick_type_qs,
            'product_type_label': type_labels.get(display_type, display_type or ''),
            'form_back_url': form_back_url,
        },
    )


@staff_member_required
def product_edit(request, pk):
    base = get_object_or_404(Product, pk=pk)
    instance, FormClass = _edit_instance_and_form(base)

    edit_channel = _parse_listing_channel_param(request.GET.get('channel'))
    return_next = request.GET.get('next', '')
    list_ch, list_qty = _edit_listing_channel_and_qty(base, edit_channel)

    if request.method == 'POST':
        if request.POST.get('_delete') == '1':
            for field in ('image_1', 'image_2', 'image_3', 'image_4', 'image_5'):
                f = getattr(base, field, None)
                if f and getattr(f, 'name', None):
                    try:
                        f.delete(save=False)
                    except Exception:
                        pass
            base.delete()
            return redirect(reverse('shop:stock'))
        form = FormClass(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            obj = form.save()
            _save_listing_from_form(obj, form)
            return _redirect_after_product_form_save(request, obj, form)
    else:
        form = FormClass(
            instance=instance,
            default_listing_channel=list_ch,
            listing_quantity=list_qty,
        )

    return render(
        request,
        'shop/product_form.html',
        {
            'form': form,
            'is_edit': True,
            'object': instance,
            'return_next': return_next,
            'edit_channel': request.GET.get('channel', ''),
            'form_back_url': _safe_internal_redirect(request, return_next)
            or reverse('shop:stock'),
        },
    )


def _promo_eval_for_request(request, rows):
    cart = get_cart(request)
    code = cart.get('promo_code') or ''
    user = request.user if getattr(request.user, 'is_authenticated', False) else None
    return evaluate_promo_for_cart_rows(code=code, rows=rows, user=user)


def cart(request):
    rows, subtotal = build_cart_page_rows(request)
    cart_data = get_cart(request)
    initial_subtotal = sum(
        (Decimal(str(r['product'].initial_price)) * Decimal(int(r['qty'])) for r in rows),
        Decimal('0.00'),
    ).quantize(Decimal('0.01'))
    saving = (initial_subtotal - subtotal).quantize(Decimal('0.01'))
    if saving < Decimal('0.00'):
        saving = Decimal('0.00')
    ev = _promo_eval_for_request(request, rows)
    promo_discount = ev.discount_gross if ev.ok else Decimal('0.00')
    cart_total = (subtotal - promo_discount).quantize(Decimal('0.01'))
    return render(
        request,
        'shop/cart.html',
        {
            'cart_rows': rows,
            'cart_subtotal': subtotal,
            'cart_initial_subtotal': initial_subtotal,
            'cart_saving': saving,
            'cart_promo_code': cart_data.get('promo_code') or '',
            'cart_discount': promo_discount,
            'cart_total': cart_total,
            'saved_cart_ttl_days': CART_TTL_DAYS,
        },
    )


@require_POST
def cart_add(request):
    try:
        data = json.loads(request.body.decode() or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'Invalid request.'}, status=400)
    try:
        product_id = int(data.get('product_id'))
        qty = int(data.get('quantity', 1))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'message': 'Invalid product or quantity.'}, status=400)
    variant = data.get('variant') or ''
    if not isinstance(variant, str):
        variant = str(variant)
    ok, msg, payload = try_add_to_cart(request, product_id=product_id, variant=variant, qty=qty)
    if not ok:
        return JsonResponse({'ok': False, 'message': msg})
    return JsonResponse({'ok': True, 'message': msg, **(payload or {})})


@require_POST
def cart_update_line(request):
    try:
        idx = int(request.POST.get('line_index', ''))
        qty = int(request.POST.get('qty', ''))
    except (TypeError, ValueError):
        messages.error(request, 'Invalid quantity.')
        return redirect('shop:cart')
    ok, msg = set_line_qty(request, idx, qty)
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect('shop:cart')


@require_POST
def cart_remove_line(request, line_index=None):
    post = request.POST
    variant = post.get('variant') or ''
    if not isinstance(variant, str):
        variant = str(variant)
    variant = variant.strip()

    try:
        idx = int(line_index if line_index is not None else post.get('line_index', ''))
    except (TypeError, ValueError):
        idx = None
    try:
        product_id = int(post.get('product_id') or '')
    except (TypeError, ValueError):
        product_id = None

    removed = False
    if idx is not None and product_id is not None:
        removed = remove_line_at_index_verified(request, idx, product_id, variant)
    if not removed and product_id is not None:
        removed = remove_line_by_product_variant(request, product_id, variant)
    if not removed and idx is not None:
        removed = remove_line(request, idx)

    if removed:
        messages.info(request, 'Item removed.')
    else:
        messages.warning(request, 'Could not remove that item. Try refreshing the page.')
    return redirect('shop:cart')


@require_POST
def cart_apply_promo(request):
    code = request.POST.get('promo', '')
    if not isinstance(code, str):
        code = str(code)
    rows, _subtotal = build_cart_page_rows(request)
    user = request.user if getattr(request.user, 'is_authenticated', False) else None
    ev = evaluate_promo_for_cart_rows(code=code, rows=rows, user=user)
    if (code or '').strip() == '':
        set_cart_promo(request, '')
        messages.info(request, 'Promo code removed.')
    elif ev.ok:
        set_cart_promo(request, normalize_promo_code(code))
        messages.success(
            request,
            f'Promo applied: −{ev.discount_gross} ₾ off eligible items.',
        )
    else:
        set_cart_promo(request, '')
        messages.error(request, ev.message or 'Promo could not be applied.')
    return redirect('shop:cart')


_CHECKOUT_EMAILS = [
    'a.molodenko@gmail.com',
    'mr.alexson.assistant@gmail.com',
    'andy.rivals@tenrivals.com',
]
_ORDER_FOR_ME_EMAILS = list(_CHECKOUT_EMAILS)


def _checkout_initial_contact(request):
    if request.user.is_authenticated:
        return {
            'first_name': (request.user.first_name or '').strip(),
            'last_name': (request.user.last_name or '').strip(),
            'phone': (getattr(request.user, 'mobile', '') or '').strip(),
            'email': (request.user.email or '').strip(),
            'tg_account': (getattr(request.user, 'telegram', '') or '').strip(),
        }
    return {
        'first_name': '',
        'last_name': '',
        'phone': '',
        'email': '',
        'tg_account': '',
    }


def _build_checkout_contact(request):
    base = _checkout_initial_contact(request)
    if request.method != 'POST':
        return {
            **base,
            'delivery_city': 'TBILISI',
            'delivery_city_other': '',
            'delivery_address': '',
            'comment': '',
            'payment_method': 'COD',
        }
    return {
        'first_name': (request.POST.get('first_name') or base['first_name']).strip(),
        'last_name': (request.POST.get('last_name') or base['last_name']).strip(),
        'phone': (request.POST.get('phone') or base['phone']).strip(),
        'email': (request.POST.get('email') or base['email']).strip(),
        'tg_account': (request.POST.get('tg_account') or '').strip(),
        'delivery_city': (request.POST.get('delivery_city') or 'TBILISI').strip().upper(),
        'delivery_city_other': (request.POST.get('delivery_city_other') or '').strip(),
        'delivery_address': (request.POST.get('delivery_address') or '').strip(),
        'comment': (request.POST.get('comment') or '').strip(),
        'payment_method': (request.POST.get('payment_method') or 'COD').strip().upper(),
    }


def _validate_checkout_contact(data, *, is_authenticated: bool):
    errors = []
    if not (data['first_name'] and data['last_name']):
        errors.append('First and last name are required.')
    if not data['phone']:
        errors.append('Phone is required.')
    if not data['email'] or '@' not in data['email']:
        errors.append('Valid email is required.')
    if not data['delivery_address']:
        errors.append('Delivery address is required.')
    if data['delivery_city'] not in {'TBILISI', 'OTHER'}:
        errors.append('Select delivery city.')
    if data['delivery_city'] == 'OTHER' and not data['delivery_city_other']:
        errors.append('Specify city for non-Tbilisi delivery.')
    if data['payment_method'] not in {'COD', 'TRANSFER'}:
        errors.append('Select payment method.')
    if is_authenticated:
        # Immutable by requirement for logged-in users.
        pass
    return errors


def _compose_delivery_address(data):
    city = 'Tbilisi' if data['delivery_city'] == 'TBILISI' else data['delivery_city_other']
    return f'{city}. {data["delivery_address"]}'.strip()


def _checkout_payment_label(code: str) -> str:
    return {
        'COD': 'Payment on delivery',
        'TRANSFER': 'Bank transfer to TBC account',
    }.get(code, code)


def _send_checkout_email(subject: str, body: str):
    send_mail(
        subject=subject,
        message=body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None) or 'no-reply@tenrivals.com',
        recipient_list=_CHECKOUT_EMAILS,
        fail_silently=True,
    )


def _send_checkout_customer_submitted_email(
    request,
    *,
    order: SalesOrder,
    rows: list[dict],
    contact: dict,
    payment_label: str,
    subtotal: Decimal,
    discount: Decimal,
    total: Decimal,
) -> None:
    recipient = (contact.get('email') or order.customer.email or '').strip()
    if not recipient:
        return
    website = (getattr(settings, 'WEBSITE_URL', '') or '').rstrip('/')
    logo_rel = '/static/assets/img/tr_footer_line_frame148.svg'
    logo_url = f'{website}{logo_rel}' if website else request.build_absolute_uri(logo_rel)
    history_path = reverse('persons:shop_order_history')
    history_url = f'{website}{history_path}' if website else request.build_absolute_uri(history_path)
    initial_subtotal = sum(
        (Decimal(str(r['product'].initial_price)) * Decimal(int(r['qty'])) for r in rows),
        Decimal('0.00'),
    ).quantize(Decimal('0.01'))
    saving = (initial_subtotal - subtotal).quantize(Decimal('0.01'))
    if saving < Decimal('0.00'):
        saving = Decimal('0.00')
    email_rows = []
    for r in rows:
        thumb_url = ''
        p = r.get('product')
        if p and getattr(p, 'main_image', None):
            try:
                rel = p.main_image.url
                thumb_url = f'{website}{rel}' if website and rel.startswith('/') else rel
            except Exception:
                thumb_url = ''
        email_rows.append(
            {
                'name': r.get('name', ''),
                'variant_label': r.get('variant_label', '—'),
                'qty': int(r.get('qty') or 0),
                'unit_price': r.get('unit_price'),
                'line_total': r.get('line_total'),
                'thumb_url': thumb_url,
            }
        )
    ctx = {
        'order': order,
        'rows': email_rows,
        'contact': contact,
        'payment_label': payment_label,
        'subtotal': subtotal,
        'discount': discount,
        'total': total,
        'initial_subtotal': initial_subtotal,
        'saving': saving,
        'history_url': history_url,
        'logo_url': logo_url,
        'company_name': 'Tennis Rivals Shop',
        'contact_tg': 'https://t.me/andyrivals',
        'contact_email': 'anry.rivals@tenrivals.com',
        'contact_phone': '+995 591 288 967',
        'is_bank_transfer': (payment_label or '').strip().lower().startswith('bank transfer'),
        'bank': {
            'iban': 'GE52TB7920236010100046',
            'beneficiary': 'P/E ANDREY MOLODENKO',
            'bank_name': 'JSC TBC Bank',
            'bank_code': 'TBCBGE22',
        },
    }
    html = render_to_string('shop/emails/order_submitted.html', ctx)
    msg = EmailMultiAlternatives(
        subject=f'Tennis Rivals: Thank You for Your Order - {order.invoice_number}',
        body=strip_tags(html),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None) or 'no-reply@tenrivals.com',
        to=[recipient],
    )
    msg.attach_alternative(html, 'text/html')
    msg.send(fail_silently=True)


def checkout(request):
    rows, subtotal = build_cart_page_rows(request)
    if not rows:
        messages.error(request, 'Your cart is empty.')
        return redirect('shop:cart')

    if request.method == 'POST' and request.POST.get('action') == 'apply_checkout_promo':
        code = request.POST.get('promo', '')
        if not isinstance(code, str):
            code = str(code)
        user = request.user if getattr(request.user, 'is_authenticated', False) else None
        ev = evaluate_promo_for_cart_rows(code=code, rows=rows, user=user)
        if (code or '').strip() == '':
            set_cart_promo(request, '')
            messages.info(request, 'Promo code removed.')
        elif ev.ok:
            set_cart_promo(request, normalize_promo_code(code))
            messages.success(
                request,
                f'Promo applied: −{ev.discount_gross} ₾ off eligible items.',
            )
        else:
            set_cart_promo(request, '')
            messages.error(request, ev.message or 'Promo could not be applied.')
        return redirect('shop:checkout')

    show_auth_gate = bool(
        not request.user.is_authenticated
        and request.GET.get('guest') != '1'
        and request.POST.get('action')
        not in {'guest_continue', 'login', 'submit_order', 'apply_checkout_promo'}
    )

    if request.method == 'POST' and request.POST.get('action') == 'login':
        email = (request.POST.get('login_email') or '').strip()
        password = request.POST.get('login_password') or ''
        user = authenticate(request, username=email, password=password)
        if user is None:
            messages.error(request, 'Login failed. Check email and password.')
            show_auth_gate = True
        else:
            login(request, user)
            return redirect('shop:checkout')

    contact = _build_checkout_contact(request)
    initial_contact = _checkout_initial_contact(request)
    if request.user.is_authenticated:
        contact['first_name'] = initial_contact['first_name']
        contact['last_name'] = initial_contact['last_name']
        contact['phone'] = initial_contact['phone']
        contact['email'] = initial_contact['email']

    delivery_note = (
        'Free delivery in Tbilisi within 48 hours.'
        if contact['delivery_city'] == 'TBILISI'
        else 'Delivery cost for other cities is calculated separately.'
    )
    promo_ev = _promo_eval_for_request(request, rows)
    discount = promo_ev.discount_gross if promo_ev.ok else Decimal('0.00')
    total = (subtotal - discount).quantize(Decimal('0.01'))

    if request.method == 'POST' and request.POST.get('action') == 'submit_order':
        errors = _validate_checkout_contact(contact, is_authenticated=request.user.is_authenticated)
        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            cart_code = (get_cart(request).get('promo_code') or '').strip()
            ev_submit = evaluate_promo_for_cart_rows(
                code=cart_code,
                rows=rows,
                user=request.user if request.user.is_authenticated else None,
            )
            if cart_code and not ev_submit.ok:
                messages.error(request, ev_submit.message)
                return redirect('shop:checkout')
            discount = ev_submit.discount_gross if ev_submit.ok else Decimal('0.00')
            total = (subtotal - discount).quantize(Decimal('0.01'))
            delivery_address = _compose_delivery_address(contact)
            payment_label = _checkout_payment_label(contact['payment_method'])
            is_guest = not request.user.is_authenticated
            user_flag = 'guest' if is_guest else 'logged-in'
            cart_lines = []
            demands = []
            for r in rows:
                vk = (r['variant_label'] if r['variant_label'] != '—' else '').strip()
                demands.append((r['product'], vk, int(r['qty'])))
                cart_lines.append(
                    f'- {r["name"]} | variant: {r["variant_label"]} | qty: {r["qty"]} | '
                    f'unit: {r["unit_price"]} | line: {r["line_total"]}'
                )
            raw_body = (
                f'Checkout type: {user_flag}\n'
                f'First name: {contact["first_name"]}\n'
                f'Last name: {contact["last_name"]}\n'
                f'Phone: {contact["phone"]}\n'
                f'Email: {contact["email"]}\n'
                f'Telegram: {contact["tg_account"]}\n'
                f'Delivery city: {contact["delivery_city"]}\n'
                f'Delivery city other: {contact["delivery_city_other"]}\n'
                f'Delivery address: {contact["delivery_address"]}\n'
                f'Delivery rule: {delivery_note}\n'
                f'Payment method: {payment_label}\n'
                f'Comment: {contact["comment"]}\n'
                f'Promo code: {(get_cart(request).get("promo_code") or "").strip()}\n'
                f'Cart items:\n' + '\n'.join(cart_lines) + '\n'
                f'Total GEL: {total}\n'
            )
            _send_checkout_email(
                f'TR - NEW Checkout - {total} GEL',
                raw_body,
            )
            stock_errors = validate_order_line_demands(
                demands,
                order_pk=None,
                old_status=None,
                old_lines=[],
            )
            if stock_errors:
                for err in stock_errors:
                    messages.error(request, err)
            else:
                try:
                    with transaction.atomic():
                        if request.user.is_authenticated:
                            customer, _ = Customer.objects.get_or_create(
                                user=request.user,
                                defaults={
                                    'first_name': contact['first_name'],
                                    'last_name': contact['last_name'],
                                    'phone': contact['phone'],
                                    'email': contact['email'],
                                    'tg_account': contact['tg_account'],
                                    'address': delivery_address,
                                },
                            )
                            customer.tg_account = contact['tg_account']
                            customer.address = delivery_address
                            customer.save(update_fields=['tg_account', 'address', 'updated_at'])
                        else:
                            customer = Customer.objects.filter(email__iexact=contact['email']).first()
                            if customer is None:
                                customer = Customer.objects.create(
                                    first_name=contact['first_name'],
                                    last_name=contact['last_name'],
                                    phone=contact['phone'],
                                    email=contact['email'],
                                    tg_account=contact['tg_account'],
                                    address=delivery_address,
                                )
                            else:
                                customer.first_name = contact['first_name']
                                customer.last_name = contact['last_name']
                                customer.phone = contact['phone']
                                customer.email = contact['email']
                                customer.tg_account = contact['tg_account']
                                customer.address = delivery_address
                                customer.save(
                                    update_fields=[
                                        'first_name',
                                        'last_name',
                                        'phone',
                                        'email',
                                        'tg_account',
                                        'address',
                                        'updated_at',
                                    ]
                                )

                        order = SalesOrder.objects.create(
                            invoice_number=allocate_invoice_number(timezone.localdate().year),
                            customer=customer,
                            order_date=timezone.localdate(),
                            gross_total=Decimal('0.00'),
                            vat_total=Decimal('0.00'),
                            net_total=Decimal('0.00'),
                            delivery_gross=Decimal('0.00'),
                            payment_method=payment_label,
                            status=SalesOrder.Status.SUBMITTED,
                            notes=(
                                f'Checkout source: storefront ({user_flag}).\n'
                                f'Delivery: {delivery_note}\n'
                                f'Address: {delivery_address}\n'
                                f'Telegram: {contact["tg_account"] or "—"}\n'
                                f'Comment: {contact["comment"] or "—"}'
                            ),
                        )
                        created_lines = []
                        gross_sum = Decimal('0.00')
                        for r in rows:
                            unit = product_unit_gross_price(r['product'])
                            qty = int(r['qty'])
                            vk = (r['variant_label'] if r['variant_label'] != '—' else '').strip()
                            lg, lv, ln = line_amounts(qty, unit, Decimal('0.00'))
                            line = SalesOrderLine.objects.create(
                                order=order,
                                product=r['product'],
                                quantity=qty,
                                unit_price_gross=unit,
                                discount_percent=Decimal('0.00'),
                                variant_label=vk,
                                line_gross=lg,
                                line_vat=lv,
                                line_net=ln,
                            )
                            created_lines.append(line)
                            gross_sum += lg

                        promo_d = Decimal('0.00')
                        if cart_code and ev_submit.ok:
                            promo_d = min(ev_submit.discount_gross, gross_sum).quantize(Decimal('0.01'))
                            if ev_submit.promo_id:
                                locked = PromoCode.objects.select_for_update().get(pk=ev_submit.promo_id)
                                cnt = PromoRedemption.objects.filter(promo=locked).count()
                                if locked.single_use_globally and cnt >= 1:
                                    raise ValueError('This promo code is no longer available.')
                                if (
                                    locked.max_redemptions is not None
                                    and cnt >= locked.max_redemptions
                                ):
                                    raise ValueError('This promo code is no longer available.')

                        final_gross = (gross_sum - promo_d).quantize(Decimal('0.01'))
                        if final_gross < Decimal('0'):
                            final_gross = Decimal('0.00')
                        net_o, vat_o = gross_split_vat_net(final_gross)
                        order.promo_discount_gross = promo_d
                        order.promo_code_label = (
                            normalize_promo_code(cart_code) if cart_code and ev_submit.ok else ''
                        )
                        order.gross_total = final_gross
                        order.vat_total = vat_o
                        order.net_total = net_o
                        order.save(
                            update_fields=[
                                'gross_total',
                                'vat_total',
                                'net_total',
                                'promo_discount_gross',
                                'promo_code_label',
                                'updated_at',
                            ]
                        )
                        take_lines_from_stock(created_lines)

                        if promo_d > Decimal('0.00') and ev_submit.ok and ev_submit.promo_id:
                            PromoRedemption.objects.create(
                                promo_id=ev_submit.promo_id,
                                sales_order=order,
                                redeemed_by=request.user
                                if request.user.is_authenticated
                                else None,
                                discount_gross=promo_d,
                            )

                        cart_data = get_cart(request)
                        cart_data['lines'] = []
                        cart_data['promo_code'] = ''
                        save_cart(request, cart_data)

                        staff_path = reverse('administration:staff_sales_order_edit', args=[order.pk])
                        staff_url = request.build_absolute_uri(staff_path)
                        _send_checkout_email(
                            f'TR - NEW Order Submitted - {order.invoice_number} - {order.gross_total} GEL',
                            (
                                f'Order: {order.invoice_number}\n'
                                f'Customer: {customer.display_name()}\n'
                                f'Total GEL: {order.gross_total}\n'
                                f'Staff link: {staff_url}\n'
                            ),
                        )
                        _send_checkout_customer_submitted_email(
                            request,
                            order=order,
                            rows=rows,
                            contact=contact,
                            payment_label=payment_label,
                            subtotal=subtotal,
                            discount=discount,
                            total=total,
                        )
                    return redirect('shop:checkout_success', order_id=order.pk)
                except Exception as exc:
                    messages.error(request, f'Could not submit order: {exc}')

    return render(
        request,
        'shop/checkout.html',
        {
            'checkout_rows': rows,
            'checkout_subtotal': subtotal,
            'checkout_total': total,
            'checkout_contact': contact,
            'show_auth_gate': show_auth_gate,
            'delivery_note': delivery_note,
            'checkout_is_authenticated': request.user.is_authenticated,
            'saved_cart_ttl_days': CART_TTL_DAYS,
            'checkout_cart_promo_code': get_cart(request).get('promo_code') or '',
            'checkout_promo_discount': promo_ev.discount_gross
            if promo_ev.ok
            else Decimal('0.00'),
        },
    )


def checkout_success(request, order_id: int):
    order = get_object_or_404(
        SalesOrder.objects.select_related('customer').prefetch_related('lines', 'lines__product'),
        pk=order_id,
    )
    return render(
        request,
        'shop/checkout_success.html',
        {
            'order': order,
            'payment_method': (order.payment_method or '').strip(),
            'show_transfer': 'transfer' in (order.payment_method or '').lower(),
            'show_cod': 'delivery' in (order.payment_method or '').lower(),
        },
    )