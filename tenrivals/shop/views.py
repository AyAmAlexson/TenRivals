from itertools import chain
from urllib.parse import quote

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .catalog_utils import (
    annotate_stock_listing_quantity,
    distinct_brands_for_type,
    filter_products_by_listing_channel,
    order_products_by_effective_price,
    stock_catalog_base_queryset,
)
from .forms import ProductForm, ShoeForm
from .models import (
    Category,
    CourtSurface,
    Gender,
    HomeBanner,
    HomeBannerSlot,
    HomePromoStripSettings,
    Product,
    ProductListing,
    ProductListingChannel,
    ProductType,
    Racket,
    Shoe,
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

def index(request):
    home_banners = {}
    for slot_value, _label in HomeBannerSlot.choices:
        home_banners[slot_value] = (
            HomeBanner.objects.filter(slot=slot_value, archived_at__isnull=True)
            .order_by('-created_at')
            .first()
        )
    promo = HomePromoStripSettings.load()
    active_products = Product.objects.filter(is_active=True).select_related(
        *_PRODUCT_SUBCLASS_SELECT,
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
    home_catalog_brands = list(
        stock_catalog_base_queryset()
        .exclude(brand__isnull=True)
        .exclude(brand__exact='')
        .values_list('brand', flat=True)
        .distinct()
        .order_by('brand')
    )
    return render(
        request,
        'shop/index.html',
        {
            'home_banners': home_banners,
            'promo_strip_left_visible': promo.left_visible,
            'promo_strip_right_visible': promo.right_visible,
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
    products = products.select_related(*_PRODUCT_SUBCLASS_SELECT)
    if browse_mode == 'stock':
        products = annotate_stock_listing_quantity(products)
    products = order_products_by_effective_price(products)
    type_tabs = [(choice.value, choice.label) for choice in ProductType]

    return {
        'browse_mode': browse_mode,
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


@staff_member_required
def product_create(request):
    type_code = request.GET.get('type')
    return_next = request.GET.get('next', '')
    edit_channel = _parse_listing_channel_param(request.GET.get('channel'))
    is_shoe = type_code in {ProductType.MENS_SHOES, ProductType.WOMENS_SHOES, ProductType.JUNIOR_SHOES}
    FormClass = ShoeForm if is_shoe else ProductForm

    if request.method == 'POST':
        # Decide form by posted type
        posted_type = request.POST.get('type')
        is_shoe_post = posted_type in {ProductType.MENS_SHOES, ProductType.WOMENS_SHOES, ProductType.JUNIOR_SHOES}
        FormClass = ShoeForm if is_shoe_post else ProductForm
        form = FormClass(request.POST, request.FILES)
        if form.is_valid():
            obj = form.save()
            _save_listing_from_form(obj, form)
            next_url = _safe_internal_redirect(request, request.POST.get('next'))
            if next_url:
                return redirect(next_url)
            return redirect(reverse('shop:product_edit', args=[obj.pk]))
    else:
        initial_kw = {}
        if type_code:
            initial_kw['type'] = type_code
        form = FormClass(
            initial=initial_kw,
            default_listing_channel=edit_channel,
        )

    return render(
        request,
        'shop/product_form.html',
        {
            'form': form,
            'is_edit': False,
            'return_next': return_next,
        },
    )


@staff_member_required
def product_edit(request, pk):
    base = get_object_or_404(Product, pk=pk)
    instance = base
    try:
        instance = base.shoe
        FormClass = ShoeForm
    except Shoe.DoesNotExist:
        FormClass = ProductForm

    edit_channel = _parse_listing_channel_param(request.GET.get('channel'))
    listing_row = None
    if edit_channel:
        listing_row = base.listings.filter(channel=edit_channel).first()
    return_next = request.GET.get('next', '')

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
            next_url = _safe_internal_redirect(request, request.POST.get('next'))
            if next_url:
                return redirect(next_url)
            return redirect(reverse('shop:product_edit', args=[obj.pk]))
    else:
        form = FormClass(
            instance=instance,
            default_listing_channel=edit_channel,
            listing_quantity=(listing_row.quantity if listing_row else None),
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
        },
    )