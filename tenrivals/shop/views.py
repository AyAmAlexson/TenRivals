from itertools import chain
from urllib.parse import quote

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .catalog_utils import (
    annotate_stock_listing_quantity,
    distinct_brands_for_type,
    filter_products_by_listing_channel,
    order_products_by_effective_price,
    stock_catalog_base_queryset,
    top_stock_brands_by_listing_quantity,
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
    Product,
    ProductListing,
    ProductListingChannel,
    ProductType,
    Racket,
    Shoe,
    String,
)

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
_ACCESSORY_TYPES = frozenset({ProductType.GRIPS, ProductType.ACCESSORIES})

# Virtual catalog filter: accessories + grips + strings (separate ProductTypes in DB).
CATALOG_ACCESSORIES_EQUIPMENT_TYPE = 'ACC_GEAR'
_CATALOG_ACCESSORIES_EQUIPMENT_TYPES = frozenset(
    {
        ProductType.ACCESSORIES,
        ProductType.GRIPS,
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
    featured_products = (
        active_products.filter(featured_product=True).order_by('-created_at')[:5]
    )
    new_arrivals = list(
        chain(
            active_products.filter(type=ProductType.RACKET).order_by('-created_at')[:2],
            active_products.filter(type=ProductType.MENS_SHOES).order_by('-created_at')[:2],
            active_products.filter(type=ProductType.WOMENS_SHOES).order_by(
                '-created_at'
            )[:1],
        )
    )
    home_catalog_brands = top_stock_brands_by_listing_quantity(7)
    return render(
        request,
        'shop/index.html',
        {
            'hero_content': hero_content,
            'hero_slides': hero_slides,
            'featured_blog_posts': featured_blog_posts,
            'featured_products': featured_products,
            'new_arrivals': new_arrivals,
            'home_catalog_brands': home_catalog_brands,
        },
    )


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
    return render(
        request,
        "shop/product_detail.html",
        {
            "product": product,
            "pdp_gallery": gallery,
            "pdp_mode": pdp_mode,
            "stock_listing_qty": stock_listing_qty,
            "pdp_type_breadcrumb_label": _pdp_breadcrumb_type_label(product),
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
        products = products.filter(brand=catalog_brand)
        if not category_slug and not type_code:
            active_tab = f'cbrand:{catalog_brand}'

    products = filter_products_by_listing_channel(products, channel)
    products = products.select_related(*_PRODUCT_SUBCLASS_SELECT, 'category')
    if browse_mode == 'stock':
        products = annotate_stock_listing_quantity(products)
    products = order_products_by_effective_price(products)
    type_tabs = [(choice.value, choice.label) for choice in ProductType]

    product_count = products.count()

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
        products = annotate_stock_listing_quantity(products)
    elif channel == ProductListingChannel.PREORDER:
        products = filter_products_by_listing_channel(
            products, ProductListingChannel.PREORDER
        )

    products = order_products_by_effective_price(products)
    if channel == ProductListingChannel.STOCK:
        tile_mode = 'stock'
        search_channel = 'stock'
    elif channel == ProductListingChannel.PREORDER:
        tile_mode = 'preorder'
        search_channel = 'preorder'
    else:
        tile_mode = 'home'
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
    # Order: text 1 → quote → text 2 → CTA middle → text 3 → products → text 4 → CTA end
    _add_paragraph(article_sections, post.body_block_1)

    if _p(post.quote_text):
        article_sections.append(
            {
                'type': 'quote',
                'text': post.quote_text.strip(),
                'author': _p(post.quote_author),
            }
        )

    _add_paragraph(article_sections, post.body_block_2)

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
    return render(request, template, {})


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