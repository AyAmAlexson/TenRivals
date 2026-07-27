from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class ProductType(models.TextChoices):
    RACKET = 'RACKET', 'Tennis Racket'
    MENS_APPAREL = 'M_APP', "Men's Apparel"
    WOMENS_APPAREL = 'W_APP', "Women's Apparel"
    JUNIOR_APPAREL = 'J_APP', 'Junior Apparel'
    MENS_SHOES = 'M_SHOES', "Men's Shoes"
    WOMENS_SHOES = 'W_SHOES', "Women's Shoes"
    JUNIOR_SHOES = 'J_SHOES', 'Junior Shoes'
    BAGS = 'BAGS', 'Tennis Bags'
    STRINGS = 'STRINGS', 'Strings'
    GRIPS = 'GRIPS', 'Grips'
    DAMPENERS = 'DAMPENERS', 'Dampeners'
    BALLS = 'BALLS', 'Balls'
    ACCESSORIES = 'ACCESSORIES', 'Accessories'
    OTHER = 'OTHER', 'Other'


class Gender(models.TextChoices):
    MEN = 'M', 'Men'
    WOMEN = 'W', 'Women'
    JUNIOR = 'J', 'Junior'
    UNISEX = 'U', 'Unisex'


class CourtSurface(models.TextChoices):
    ALL_COURT = 'AC', 'All Court'
    HARD = 'HC', 'Hard Court'
    CLAY = 'CL', 'Clay'
    GRASS = 'GR', 'Grass'
    PADEL = 'PD', 'Padel'


class Category(models.Model):
    # Optional taxonomy for navigation/filters
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    parent = models.ForeignKey('self', null=True, blank=True, related_name='children', on_delete=models.CASCADE)

    class Meta:
        verbose_name = 'Category'
        verbose_name_plural = 'Categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class Product(models.Model):
    # Base product fields used by all item types
    type = models.CharField(max_length=16, choices=ProductType.choices, db_index=True)
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    brand = models.CharField(max_length=120, blank=True, null=True)

    # Pricing and availability
    initial_price = models.DecimalField(max_digits=10, decimal_places=2)
    actual_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    in_stock = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    featured_product = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_('Show in Featured block on the shop home (up to 5, newest first).'),
    )

    # Images (up to 3)
    image_1 = models.ImageField(upload_to='shop/products/', null=True, blank=True)
    image_2 = models.ImageField(upload_to='shop/products/', null=True, blank=True)
    image_3 = models.ImageField(upload_to='shop/products/', null=True, blank=True)
    image_4 = models.ImageField(upload_to='shop/products/', null=True, blank=True)
    image_5 = models.ImageField(upload_to='shop/products/', null=True, blank=True)

    # Optional categorization
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name='products')

    # Misc
    color = models.CharField(max_length=80, blank=True, null=True)
    short_description = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    # Flexible attributes for uncommon types (JSON)
    attributes = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['type', 'is_active']),
            models.Index(fields=['in_stock']),
            models.Index(fields=['is_active', 'featured_product', '-created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name}'

    @property
    def main_image(self):
        return self.image_1 or self.image_2 or self.image_3 or self.image_4 or self.image_5

    @property
    def sizes_list(self):
        """Shoe or apparel sizes/grip-style list for PDP (dict → in-stock keys only)."""
        from .size_inventory import sizes_for_pdp_display

        try:
            return sizes_for_pdp_display(self.shoe.sizes) or None
        except Exception:
            pass
        try:
            return sizes_for_pdp_display(self.apparel.sizes) or None
        except Exception:
            return None

    @property
    def grip_sizes_display(self):
        try:
            from .size_inventory import sizes_for_pdp_display

            return sizes_for_pdp_display(self.racket.grip_sizes)
        except Exception:
            return []

    @property
    def string_gauges_display(self):
        """In-stock string gauges for PDP chips (legacy single gauge_mm if no per-gauge map)."""
        try:
            from .size_inventory import sizes_for_pdp_display

            st = self.string
            raw = st.gauges
            if raw:
                out = sizes_for_pdp_display(raw)
                if out:
                    return out
            if st.gauge_mm is not None:
                return [f'{st.gauge_mm} mm']
        except Exception:
            pass
        return []

    def staff_listing_size_summary(self) -> str:
        """Grip / shoe / apparel variants with qty > 0 (compact for staff listing tables)."""
        from .size_inventory import normalize_sizes_to_qty_map

        for rel_name, field in (
            ('racket', 'grip_sizes'),
            ('shoe', 'sizes'),
            ('apparel', 'sizes'),
            ('string', 'gauges'),
        ):
            try:
                sub = getattr(self, rel_name)
            except ObjectDoesNotExist:
                continue
            raw = getattr(sub, field, None)
            if not raw:
                continue
            m = normalize_sizes_to_qty_map(raw, fallback_total=0)
            parts = [f'{k}×{v}' for k, v in sorted(m.items()) if int(v or 0) > 0]
            if parts:
                return ', '.join(parts)
        return ''

    @property
    def primary_price(self):
        """Selling price: actual if set, otherwise initial."""
        if self.actual_price is not None:
            return self.actual_price
        return self.initial_price

    @property
    def margin_price(self):
        # primary_price + 5%, rounded up to nearest 10 (₾)
        from decimal import Decimal, ROUND_CEILING

        base_price = self.primary_price
        if base_price is None:
            return None
        base = (base_price * Decimal('1.05')).quantize(Decimal('0.01'))
        tens = (base / Decimal('10')).to_integral_value(rounding=ROUND_CEILING) * Decimal('10')
        return tens

    def invoice_line_title(self) -> str:
        """Brand + model name for invoices and staff lists."""
        b = (self.brand or '').strip()
        n = (self.name or '').strip()
        if b and n:
            return f'{b} {n}'
        return n or b or ''

    def storefront_cart_line_title(self) -> str:
        """Brand + model + colorway for cart, checkout, and add-to-cart modal (variant stays in its own column)."""
        base = self.invoice_line_title()
        c = (self.color or '').strip()
        if c:
            return f'{base} / {c}'
        return base

    def invoice_line_specs_slash(self) -> str:
        """Color (if set) plus type-specific details, joined with ' / '."""
        type_part = ''
        for rel in (
            'racket',
            'shoe',
            'apparel',
            'string',
            'bag',
            'balls',
            'accessory',
        ):
            try:
                sub = getattr(self, rel)
            except ObjectDoesNotExist:
                continue
            fn = getattr(sub, 'invoice_specs_slash', None)
            if callable(fn):
                type_part = fn()
                break
        parts: list[str] = []
        c = (self.color or '').strip()
        if c:
            parts.append(c)
        tp = (type_part or '').strip()
        if tp:
            parts.append(tp)
        return ' / '.join(parts)

    def invoice_line_label(self) -> str:
        """Full item description for invoice table (title + specs)."""
        title = self.invoice_line_title()
        specs = (self.invoice_line_specs_slash() or '').strip()
        if specs:
            return f'{title} / {specs}'
        return title


class Racket(Product):
    # Tennis racket specific fields
    weight_grams = models.PositiveIntegerField(null=True, blank=True)  # e.g. 300
    head_size_sq_in = models.PositiveIntegerField(null=True, blank=True)  # e.g. 98
    length_in = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)  # e.g. 27.00
    balance_mm = models.PositiveIntegerField(null=True, blank=True)  # e.g. 320
    swingweight = models.PositiveIntegerField(null=True, blank=True)  # e.g. 295
    string_pattern = models.CharField(max_length=16, blank=True, null=True)  # e.g. "16x19"
    is_strung = models.BooleanField(default=True)
    grip_sizes = models.JSONField(default=list, blank=True)  # e.g. ["L2", "L3"]

    class Meta:
        verbose_name = 'Racket'
        verbose_name_plural = 'Rackets'

    def invoice_specs_slash(self) -> str:
        parts = []
        if self.weight_grams:
            parts.append(f'{self.weight_grams} g')
        if self.head_size_sq_in:
            parts.append(f'{self.head_size_sq_in} sq in')
        if self.grip_sizes and isinstance(self.grip_sizes, list):
            grips = [str(x) for x in self.grip_sizes if x is not None and str(x).strip()]
            if grips:
                parts.append(', '.join(grips))
        return ' / '.join(parts)


class Shoe(Product):
    # Tennis shoes specific fields
    gender = models.CharField(max_length=1, choices=Gender.choices, default=Gender.UNISEX)
    surface = models.CharField(max_length=2, choices=CourtSurface.choices, default=CourtSurface.ALL_COURT)
    sizes = models.JSONField(default=list, blank=True)  # US size keys -> qty, e.g. {"US 10.5": 2}
    width = models.CharField(
        max_length=24,
        blank=True,
        null=True,
        help_text=_('Width / last (e.g. D, 2E, Wide).'),
    )

    class Meta:
        verbose_name = 'Shoe'
        verbose_name_plural = 'Shoes'

    def invoice_specs_slash(self) -> str:
        parts = []
        if self.surface:
            parts.append(str(self.get_surface_display()))
        if self.width:
            parts.append(self.width)
        if self.gender:
            parts.append(str(self.get_gender_display()))
        return ' / '.join(parts)


class Apparel(Product):
    # Apparel common fields (men/women/junior via gender)
    gender = models.CharField(max_length=1, choices=Gender.choices, default=Gender.UNISEX)
    sizes = models.JSONField(default=list, blank=True)  # e.g. ["S","M","L","XL"]
    material = models.CharField(max_length=120, blank=True, null=True)

    class Meta:
        verbose_name = 'Apparel'
        verbose_name_plural = 'Apparel'

    def invoice_specs_slash(self) -> str:
        parts = []
        if self.gender:
            parts.append(str(self.get_gender_display()))
        if self.material:
            parts.append(self.material)
        return ' / '.join(parts)


class String(Product):
    # Strings specific fields
    gauge_mm = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)  # e.g. 1.25 (legacy / default label)
    gauges = models.JSONField(default=dict, blank=True)  # e.g. {"1.25 mm": 4, "1.30 mm": 2} — stock by thickness
    material = models.CharField(max_length=80, blank=True, null=True)  # e.g. Polyester, Multifilament, Natural
    length_m = models.PositiveIntegerField(null=True, blank=True)  # e.g. 12

    class Meta:
        verbose_name = 'String'
        verbose_name_plural = 'Strings'

    def invoice_specs_slash(self) -> str:
        from .size_inventory import normalize_sizes_to_qty_map

        parts = []
        gmap = normalize_sizes_to_qty_map(self.gauges, fallback_total=0)
        if gmap:
            pairs = [f'{k}×{v}' for k, v in sorted(gmap.items()) if int(v or 0) > 0]
            if pairs:
                parts.append(', '.join(pairs))
        elif self.gauge_mm is not None:
            parts.append(f'{self.gauge_mm} mm')
        if self.material:
            parts.append(self.material)
        if self.length_m:
            parts.append(f'{self.length_m} m')
        return ' / '.join(parts)


class Bag(Product):
    capacity_rackets = models.PositiveIntegerField(null=True, blank=True)  # e.g. 6, 9, 12

    class Meta:
        verbose_name = 'Bag'
        verbose_name_plural = 'Bags'

    def invoice_specs_slash(self) -> str:
        if self.capacity_rackets:
            return f'{self.capacity_rackets} rackets'
        return ''


class Balls(Product):
    balls_per_can = models.PositiveIntegerField(null=True, blank=True)  # e.g. 3 or 4
    surface = models.CharField(max_length=2, choices=CourtSurface.choices, default=CourtSurface.ALL_COURT)

    class Meta:
        verbose_name = 'Balls'
        verbose_name_plural = 'Balls'

    def invoice_specs_slash(self) -> str:
        parts = []
        if self.balls_per_can:
            parts.append(f'{self.balls_per_can} pcs/can')
        if self.surface:
            parts.append(str(self.get_surface_display()))
        return ' / '.join(parts)


class Accessory(Product):
    # Generic catch-all for grips and accessories
    class Meta:
        verbose_name = 'Accessory'
        verbose_name_plural = 'Accessories'

    def invoice_specs_slash(self) -> str:
        return ''


class HomePromoStripSettings(models.Model):
    """Singleton row (pk=1): show/hide the two small promo tiles under the main hero."""

    left_visible = models.BooleanField(default=True)
    right_visible = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Home promo strip visibility'
        verbose_name_plural = 'Home promo strip visibility'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


@dataclass(frozen=True)
class HomeHeroLocalizedStrings:
    """Storefront hero overlay copy resolved for one Django language code (en / ru / ka)."""

    headline: str
    subtext: str
    cta_label: str
    cta_url: str
    secondary_link_label: str
    secondary_link_url: str


class HomeHeroContent(models.Model):
    """Singleton (pk=1): headline, subcopy, CTA + secondary link overlaid on the home hero carousel."""

    headline = models.TextField(
        default='The Line for Your Growth',
        help_text=_('Use a line break for a second line, e.g. before “Your Growth”.'),
    )
    subtext = models.TextField(
        blank=True,
        default=(
            'Curated tennis equipment from top brands. Free delivery in Tbilisi. '
            'Pre-order from US & EU with official customs clearance.'
        ),
    )
    cta_label = models.CharField(max_length=120, default='Shop Preorder')
    cta_url = models.CharField(max_length=500, default='/shop/preorder')
    secondary_link_label = models.CharField(max_length=120, blank=True)
    secondary_link_url = models.CharField(max_length=500, blank=True)
    headline_ru = models.TextField(
        blank=True,
        default='',
        help_text=_('Russian (ge_ru). Empty = use English fields above.'),
    )
    subtext_ru = models.TextField(blank=True, default='')
    cta_label_ru = models.CharField(max_length=120, blank=True, default='')
    cta_url_ru = models.CharField(max_length=500, blank=True, default='')
    secondary_link_label_ru = models.CharField(max_length=120, blank=True, default='')
    secondary_link_url_ru = models.CharField(max_length=500, blank=True, default='')
    headline_ka = models.TextField(
        blank=True,
        default='',
        help_text=_('Georgian (ge_ka). Empty = use English fields above.'),
    )
    subtext_ka = models.TextField(blank=True, default='')
    cta_label_ka = models.CharField(max_length=120, blank=True, default='')
    cta_url_ka = models.CharField(max_length=500, blank=True, default='')
    secondary_link_label_ka = models.CharField(max_length=120, blank=True, default='')
    secondary_link_url_ka = models.CharField(max_length=500, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Home hero text & CTAs'
        verbose_name_plural = 'Home hero text & CTAs'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={},
        )
        return obj

    def localized_strings(self, django_language_code: str | None) -> HomeHeroLocalizedStrings:
        """Resolve hero copy for storefront language; empty locale-specific fields fall back to English."""

        code = (django_language_code or 'en').lower().partition('-')[0]

        def _coalesce(local: str, base: str) -> str:
            s = (local or '').strip()
            return s if s else (base or '').strip()

        if code == 'ru':
            return HomeHeroLocalizedStrings(
                headline=_coalesce(self.headline_ru, self.headline),
                subtext=_coalesce(self.subtext_ru, self.subtext),
                cta_label=_coalesce(self.cta_label_ru, self.cta_label),
                cta_url=_coalesce(self.cta_url_ru, self.cta_url),
                secondary_link_label=_coalesce(
                    self.secondary_link_label_ru, self.secondary_link_label
                ),
                secondary_link_url=_coalesce(self.secondary_link_url_ru, self.secondary_link_url),
            )
        if code == 'ka':
            return HomeHeroLocalizedStrings(
                headline=_coalesce(self.headline_ka, self.headline),
                subtext=_coalesce(self.subtext_ka, self.subtext),
                cta_label=_coalesce(self.cta_label_ka, self.cta_label),
                cta_url=_coalesce(self.cta_url_ka, self.cta_url),
                secondary_link_label=_coalesce(
                    self.secondary_link_label_ka, self.secondary_link_label
                ),
                secondary_link_url=_coalesce(self.secondary_link_url_ka, self.secondary_link_url),
            )
        return HomeHeroLocalizedStrings(
            headline=(self.headline or '').strip(),
            subtext=(self.subtext or '').strip(),
            cta_label=(self.cta_label or '').strip(),
            cta_url=(self.cta_url or '').strip(),
            secondary_link_label=(self.secondary_link_label or '').strip(),
            secondary_link_url=(self.secondary_link_url or '').strip(),
        )


class HomeHeroSlide(models.Model):
    """Up to five rotating full-width hero images on the shop home."""

    image = models.ImageField(upload_to='shop/hero_slides/')
    sort_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    image_link_url = models.CharField(blank=True, max_length=500)
    internal_note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'id']
        verbose_name = 'Home hero slide'
        verbose_name_plural = 'Home hero slides'

    def __str__(self):
        return f'Hero slide #{self.pk}'

    def save(self, *args, **kwargs):
        from shop.image_uploads import assign_optimized_imagefield, imagefield_changed

        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'image' not in update_fields:
            return super().save(*args, **kwargs)
        prev = HomeHeroSlide.objects.filter(pk=self.pk).first() if self.pk else None
        if self.image and imagefield_changed(prev, self, 'image'):
            assign_optimized_imagefield(self.image, max_width=2000, max_height=1200)
        return super().save(*args, **kwargs)


class HomePromoBanner(models.Model):
    """Wide clickable banner carousel placed between featured products and new arrivals."""

    image = models.FileField(
        upload_to='shop/home_promo_banners/',
        validators=[
            FileExtensionValidator(
                allowed_extensions=('png', 'jpg', 'jpeg', 'webp', 'svg'),
            ),
        ],
        help_text=_(
            'Raster (PNG/JPEG/WebP) or SVG. Recommended raster size: 1600x420 px '
            '(wide landscape). SVG keeps text sharp at any width.'
        ),
    )
    sort_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    image_link_url = models.CharField(blank=True, max_length=500)
    internal_note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'id']
        verbose_name = 'Home promo banner'
        verbose_name_plural = 'Home promo banners'

    def __str__(self):
        return f'Home promo banner #{self.pk}'

    def save(self, *args, **kwargs):
        from shop.image_uploads import assign_optimized_filefield, imagefield_changed

        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'image' not in update_fields:
            return super().save(*args, **kwargs)
        prev = HomePromoBanner.objects.filter(pk=self.pk).first() if self.pk else None
        if self.image and imagefield_changed(prev, self, 'image'):
            assign_optimized_filefield(self.image, max_width=2000, max_height=700)
        return super().save(*args, **kwargs)


class ProductCollection(models.Model):
    """Curated group of products shown as a storefront collection landing page."""

    title = models.CharField(max_length=160, unique=True)
    slug = models.SlugField(max_length=180, unique=True, db_index=True)
    banner_image = models.FileField(
        upload_to='shop/collections/',
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(
                allowed_extensions=('png', 'jpg', 'jpeg', 'webp', 'svg'),
            ),
        ],
        help_text=_(
            'Raster (PNG/JPEG/WebP) or SVG. Recommended raster size: 1600x560 px '
            '(wide banner). SVG keeps text sharp at any width.'
        ),
    )
    description = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    products = models.ManyToManyField(
        'Product',
        blank=True,
        related_name='collections',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title', 'id']
        verbose_name = 'Product collection'
        verbose_name_plural = 'Product collections'

    def __str__(self):
        return self.title


class ProductCollectionGroup(models.Model):
    """Optional visual/content block with its own product subset inside a collection."""

    collection = models.ForeignKey(
        ProductCollection,
        on_delete=models.CASCADE,
        related_name='groups',
    )
    sort_order = models.PositiveSmallIntegerField(default=1, db_index=True)
    image = models.FileField(
        upload_to='shop/collections/groups/',
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(
                allowed_extensions=('png', 'jpg', 'jpeg', 'webp', 'svg'),
            ),
        ],
        help_text=_(
            'Optional square image for this group. '
            'Raster recommendation: 1200x1200 px. SVG is allowed.'
        ),
    )
    title = models.CharField(max_length=180, blank=True)
    description = models.TextField(blank=True)
    products = models.ManyToManyField(
        'Product',
        blank=True,
        related_name='collection_groups',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'id']
        verbose_name = 'Collection group'
        verbose_name_plural = 'Collection groups'

    def __str__(self):
        label = self.title.strip() if self.title else f'Group #{self.pk}'
        return f'{self.collection.title}: {label}'


class BlogPost(models.Model):
    """Editorial post at /shop/blog/<slug>/. Featured home carousel uses the same rows."""

    slug = models.SlugField(max_length=160, unique=True, db_index=True)
    title = models.CharField(max_length=220)
    lead = models.TextField(blank=True, help_text=_('Short intro under the title (plain text).'))
    body = models.TextField(
        blank=True,
        help_text=_('Legacy aggregated body (auto-built from text blocks).'),
    )
    body_block_1 = models.TextField(blank=True, help_text=_('Article text block 1/4 (before quote).'))
    body_block_2 = models.TextField(blank=True, help_text=_('Article text block 2/4 (after quote, before mid CTA).'))
    body_block_3 = models.TextField(blank=True, help_text=_('Article text block 3/4 (after mid CTA, before product rail).'))
    body_block_4 = models.TextField(blank=True, help_text=_('Article text block 4/4 (after product rail, before end CTA).'))
    hero_image = models.ImageField(
        upload_to='shop/blog/',
        null=True,
        blank=True,
        help_text=_('Wide image for the article page header (landscape recommended).'),
    )
    card_image = models.ImageField(
        upload_to='shop/blog/cards/',
        null=True,
        blank=True,
        help_text=_('Portrait image for the home “Featured stories” carousel (3:4 works best). Falls back to hero if empty.'),
    )
    article_image_1 = models.FileField(
        upload_to='shop/blog/inline/',
        null=True,
        blank=True,
        help_text=_('Optional inline figure (1/3). Raster (JPEG/PNG/WebP) is auto-optimized; SVG is allowed.'),
    )
    article_image_2 = models.FileField(
        upload_to='shop/blog/inline/',
        null=True,
        blank=True,
        help_text=_('Optional inline figure (2/3). Raster is auto-optimized; SVG is allowed.'),
    )
    article_image_3 = models.FileField(
        upload_to='shop/blog/inline/',
        null=True,
        blank=True,
        help_text=_('Optional inline figure (3/3). Raster is auto-optimized; SVG is allowed.'),
    )
    quote_text = models.TextField(
        blank=True,
        help_text=_('Highlighted quote block shown mid-article.'),
    )
    quote_author = models.CharField(
        max_length=160,
        blank=True,
        help_text=_('Optional quote author/signature.'),
    )
    cta_mid_text = models.CharField(
        max_length=240,
        blank=True,
        help_text=_('Middle CTA heading text.'),
    )
    cta_mid_button_label = models.CharField(max_length=120, blank=True)
    cta_mid_button_url = models.CharField(max_length=500, blank=True)
    cta_end_text = models.CharField(
        max_length=240,
        blank=True,
        help_text=_('Ending CTA heading text.'),
    )
    cta_end_button_label = models.CharField(max_length=120, blank=True)
    cta_end_button_url = models.CharField(max_length=500, blank=True)
    featured_product_1 = models.ForeignKey(
        'Product',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='blog_featured_slot_1',
    )
    featured_product_2 = models.ForeignKey(
        'Product',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='blog_featured_slot_2',
    )
    featured_product_3 = models.ForeignKey(
        'Product',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='blog_featured_slot_3',
    )
    featured_product_4 = models.ForeignKey(
        'Product',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='blog_featured_slot_4',
    )
    featured_product_5 = models.ForeignKey(
        'Product',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='blog_featured_slot_5',
    )
    is_published = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    is_featured_on_home = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_('Show this post in the Featured stories carousel on the shop home page.'),
    )
    featured_sort_order = models.PositiveIntegerField(
        default=0,
        db_index=True,
        help_text=_('Lower numbers appear first in the home carousel.'),
    )
    featured_cta_label = models.CharField(
        max_length=120,
        blank=True,
        default='',
        help_text=_('Button text on the home card (default: Read more).'),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-published_at', '-id']
        verbose_name = 'Blog post'
        verbose_name_plural = 'Blog posts'

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        from shop.image_uploads import (
            assign_optimized_filefield,
            assign_optimized_imagefield,
            imagefield_changed,
        )

        img_fields = (
            'hero_image',
            'card_image',
            'article_image_1',
            'article_image_2',
            'article_image_3',
        )
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and not set(update_fields) & set(img_fields):
            return super().save(*args, **kwargs)

        prev = BlogPost.objects.filter(pk=self.pk).first() if self.pk else None

        if self.hero_image and imagefield_changed(prev, self, 'hero_image'):
            assign_optimized_imagefield(self.hero_image, max_width=2000, max_height=1200)
        if self.card_image and imagefield_changed(prev, self, 'card_image'):
            assign_optimized_imagefield(self.card_image, max_width=900, max_height=1400)
        for fname in ('article_image_1', 'article_image_2', 'article_image_3'):
            f = getattr(self, fname)
            if f and imagefield_changed(prev, self, fname):
                assign_optimized_filefield(f, max_width=1400, max_height=1400)

        return super().save(*args, **kwargs)

    @property
    def teaser_image(self):
        """Portrait card image if set, else hero (listings / fallbacks)."""
        if self.card_image:
            return self.card_image
        return self.hero_image

    @property
    def featured_cta_display(self) -> str:
        return (self.featured_cta_label or '').strip() or 'Read more'


class ProductListingChannel(models.TextChoices):
    STOCK = 'STOCK', _('In stock')
    PREORDER = 'PREORDER', _('Preorder')


class ProductListing(models.Model):
    """Per-channel availability for storefront merchandising.

    * **STOCK** ``quantity`` — units physically in Tbilisi (real shelf stock). Signals set
      ``Product.in_stock=True`` when ``quantity > 0`` and ``False`` when the STOCK row is
      removed; ``quantity == 0`` does not clear the checkbox so staff can keep vitrine SKUs
      in the In stock grid.
    * **PREORDER** ``quantity`` — preorder allocation / slots from suppliers. ``0`` means
      vitrine / “coming soon” in the preorder catalog (indicative pricing, contact to order),
      not on-hand inventory.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='listings')
    channel = models.CharField(max_length=16, choices=ProductListingChannel.choices)
    quantity = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(
                fields=('product', 'channel'),
                name='shop_product_listing_unique_channel',
            ),
        ]

    def __str__(self):
        return f'{self.product_id} {self.channel} ×{self.quantity}'


class UserCart(models.Model):
    """Persistent cart for authenticated users (cross-browser/session)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='shop_cart',
    )
    promo_code = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User cart'
        verbose_name_plural = 'User carts'

    def __str__(self):
        return f'Cart for user #{self.user_id}'


class UserCartLine(models.Model):
    cart = models.ForeignKey(UserCart, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='user_cart_lines')
    variant = models.CharField(max_length=64, blank=True, default='')
    qty = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(
                fields=('cart', 'product', 'variant'),
                name='shop_user_cart_line_unique_product_variant',
            ),
        ]

    def __str__(self):
        return f'cart#{self.cart_id} product#{self.product_id} {self.variant} ×{self.qty}'


class ShopOrder(models.Model):
    """Customer order (created via admin or future checkout)."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        CONFIRMED = 'CONFIRMED', _('Confirmed')
        FULFILLED = 'FULFILLED', _('Fulfilled')
        CANCELLED = 'CANCELLED', _('Cancelled')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='shop_orders',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text=_('Total in GEL (₾); updated from line items on save.'),
    )
    customer_note = models.TextField(blank=True)
    internal_note = models.TextField(blank=True, help_text=_('Staff only, not shown to customer.'))

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Order #{self.pk} — {self.user.email}'

    def recalculate_total(self):
        from django.db.models import Sum

        agg = self.items.aggregate(s=Sum('line_total'))
        total = agg['s'] or Decimal('0.00')
        self.total_amount = total


class ShopOrderItem(models.Model):
    """Single line on a shop order (product snapshot + pricing)."""

    order = models.ForeignKey(
        ShopOrder,
        on_delete=models.CASCADE,
        related_name='items',
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='order_items',
    )
    product_name = models.CharField(max_length=200)
    brand = models.CharField(max_length=120, blank=True)
    sku = models.CharField(max_length=64, blank=True)
    variant_label = models.CharField(
        max_length=120,
        blank=True,
        help_text=_('Size, grip, colorway, etc.'),
    )
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
    )

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.product_name} × {self.quantity}'

    def save(self, *args, **kwargs):
        if self.unit_price is not None and self.quantity:
            self.line_total = (self.unit_price * self.quantity).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)
        self._refresh_order_total()

    def delete(self, *args, **kwargs):
        order = self.order
        super().delete(*args, **kwargs)
        order.recalculate_total()
        order.save(update_fields=['total_amount', 'updated_at'])

    def _refresh_order_total(self):
        order = self.order
        order.recalculate_total()
        order.save(update_fields=['total_amount', 'updated_at'])


# --- Staff retail: customers & sales orders (POS-style invoices, separate from ShopOrder) ---


class Customer(models.Model):
    """Walk-in / registered buyer. Optional link to CustomUser when they sign up on the site."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='retail_customer',
    )
    first_name = models.CharField(max_length=120)
    last_name = models.CharField(max_length=120, blank=True)
    name_local = models.CharField(
        max_length=120,
        blank=True,
        help_text=_('Georgian / local script first name (optional).'),
    )
    surname_local = models.CharField(
        max_length=120,
        blank=True,
        help_text=_('Georgian / local script last name (optional).'),
    )
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    newsletter_opt_in = models.BooleanField(default=False)
    tg_account = models.CharField(
        max_length=64,
        blank=True,
        help_text=_('Telegram username or handle (optional).'),
    )
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name', 'id']
        indexes = [
            models.Index(fields=['phone']),
            models.Index(fields=['email']),
        ]

    def __str__(self):
        parts = [self.first_name, self.last_name]
        return ' '.join(p for p in parts if p).strip() or f'Customer #{self.pk}'

    def display_name(self) -> str:
        return str(self)

    def display_name_for_invoice(self) -> str:
        """Prefer Georgian/local name on invoice; fall back to Latin first/last."""
        parts = [
            (self.name_local or '').strip(),
            (self.surname_local or '').strip(),
        ]
        local = ' '.join(p for p in parts if p).strip()
        if local:
            return local
        return str(self)


class SalesInvoiceYearSequence(models.Model):
    """
    Per calendar year sequence for invoice numbers YYYY-XXXXXX.
    Deleted orders do not reuse numbers — last_seq only increases.
    First number in a year is ...000039 (i.e. last_seq starts at 38).
    """

    year = models.PositiveIntegerField(unique=True, db_index=True)
    last_seq = models.PositiveIntegerField(default=38)

    class Meta:
        verbose_name = 'Sales invoice sequence (year)'

    def __str__(self):
        return f'{self.year} → {self.last_seq}'


class OrderForMeYearSequence(models.Model):
    """Per-year sequence for Order For Me numbers OFM-YYYY-XXXXXX."""

    year = models.PositiveIntegerField(unique=True, db_index=True)
    last_seq = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Order For Me sequence (year)'

    def __str__(self):
        return f'OFM {self.year} → {self.last_seq}'


class OrderForMe(models.Model):
    class ContactMethod(models.TextChoices):
        EMAIL = 'EMAIL', _('Email')
        WHATSAPP = 'WHATSAPP', _('WhatsApp')
        TELEGRAM = 'TELEGRAM', _('Telegram')

    class Status(models.TextChoices):
        SUBMITTED = 'SUBMITTED', _('Submitted')
        QUOTE_PROVIDED = 'QUOTE_PROVIDED', _('Quote provided')
        CANCELLED = 'CANCELLED', _('Cancelled')
        ORDERED = 'ORDERED', _('Ordered')

    order_number = models.CharField(max_length=20, unique=True, db_index=True)
    customer = models.ForeignKey(
        Customer,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='order_for_me_orders',
    )
    first_name = models.CharField(max_length=120)
    last_name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=32)
    telegram = models.CharField(max_length=64, blank=True)
    contact_method = models.CharField(
        max_length=16,
        choices=ContactMethod.choices,
        default=ContactMethod.EMAIL,
        db_index=True,
    )
    general_comment = models.TextField(blank=True)
    estimated_total = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.SUBMITTED,
        db_index=True,
    )
    status_changed_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['customer', '-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self):
        return self.order_number


class OrderForMeItem(models.Model):
    order = models.ForeignKey(
        OrderForMe,
        on_delete=models.CASCADE,
        related_name='items',
    )
    sort_order = models.PositiveSmallIntegerField(default=1, db_index=True)
    item_url = models.URLField(max_length=1000)
    item_comment = models.TextField(blank=True)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.order.order_number} item #{self.sort_order}'


class SalesOrder(models.Model):
    """Staff-issued retail invoice (VAT-inclusive GEL)."""

    invoice_number = models.CharField(max_length=16, unique=True, db_index=True)
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name='sales_orders',
    )
    order_date = models.DateField(db_index=True)
    gross_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    vat_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    net_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    delivery_gross = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text=_('Delivery / extra charge, VAT-inclusive (₾).'),
    )
    fiscal_receipt = models.CharField(max_length=64, blank=True)
    payment_method = models.CharField(max_length=200, blank=True)
    services = models.JSONField(
        default=list,
        blank=True,
        help_text=_('List of {"name": str, "gross": str|number} — VAT-inclusive amounts.'),
    )
    notes = models.TextField(blank=True)
    promo_code_label = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text=_('Promo code string applied at checkout (snapshot).'),
    )
    promo_discount_gross = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text=_('VAT-inclusive promo discount (₾), subtracted from line subtotal.'),
    )
    payment_currency = models.CharField(
        max_length=8,
        default='GEL',
        db_index=True,
        help_text=_('Currency for payment (line totals remain in GEL).'),
    )
    exchange_rate = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        default=Decimal('1'),
        help_text=_('₾ per 1 unit of payment currency; foreign amount = gross_total ÷ rate (1 for GEL).'),
    )
    amount_in_payment_currency = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text=_('Order gross in payment_currency (gross_total ÷ exchange_rate).'),
    )

    class Status(models.TextChoices):
        SUBMITTED = 'SUBMITTED', _('Submitted')
        AWAITING_PAYMENT = 'AWAITING_PAYMENT', _('Awaiting payment')
        CONFIRMED = 'CONFIRMED', _('Confirmed')
        SHIPPED = 'SHIPPED', _('Shipped')
        COMPLETED = 'COMPLETED', _('Completed')
        CANCELLED = 'CANCELLED', _('Cancelled')
        REFUNDED = 'REFUNDED', _('Refunded')

    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.SUBMITTED,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-order_date', '-id']

    def __str__(self):
        return self.invoice_number

    def payment_currency_display(self) -> str:
        from .sales_order_currency import normalize_payment_currency

        return normalize_payment_currency(self.payment_currency)


class SalesOrderLine(models.Model):
    order = models.ForeignKey(
        SalesOrder,
        on_delete=models.CASCADE,
        related_name='lines',
    )
    product = models.ForeignKey(
        Product,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='sales_order_lines',
        help_text=_('Empty for non-stock (legacy / free-text) lines.'),
    )
    custom_label = models.CharField(
        max_length=200,
        blank=True,
        default='',
        help_text=_('Free-text item name for non-stock lines (when product is empty).'),
    )
    quantity = models.PositiveIntegerField()
    unit_price_gross = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text=_('Unit price including VAT (₾).'),
    )
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    variant_label = models.CharField(
        max_length=48,
        blank=True,
        help_text=_('Grip size (L2, …) or shoe US size when product uses size grid.'),
    )
    line_gross = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    line_vat = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    line_net = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        ordering = ['id']

    @property
    def is_stock_line(self) -> bool:
        return self.product_id is not None

    def display_title(self) -> str:
        """Item name: catalog title for stock lines, typed text for custom lines."""
        if self.product_id:
            return self.product.storefront_cart_line_title()
        return (self.custom_label or '').strip() or 'Custom item'

    def __str__(self):
        return f'{self.display_title()} ×{self.quantity}'

    def staff_order_item_summary(self) -> str:
        """One line for orders list: qty× brand model + color (+ variant)."""
        base = self.display_title()
        v = (self.variant_label or '').strip()
        if v:
            base = f'{base} ({v})'
        return f'{self.quantity}× {base}'

    def invoice_display_label(self) -> str:
        """Line text for PDF / invoice table."""
        if not self.product_id:
            return (self.custom_label or '').strip() or 'Custom item'
        base = self.product.invoice_line_label()
        if (self.variant_label or '').strip():
            return f'{base} / {self.variant_label}'
        return base


class PromoCode(models.Model):
    """Staff-configured checkout promo: fixed or percent off eligible cart subtotal."""

    class DiscountType(models.TextChoices):
        FIXED_GROSS = 'FIXED_GROSS', _('Fixed amount (₾)')
        PERCENT = 'PERCENT', _('Percent of eligible subtotal')

    class ApplicationScope(models.TextChoices):
        ALL = 'ALL', _('Entire catalog')
        PRODUCTS = 'PRODUCTS', _('Specific products (SKU)')
        COLLECTIONS = 'COLLECTIONS', _('Specific collections')

    code = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text=_('Case-insensitive; stored uppercase.'),
    )
    title = models.CharField(
        max_length=160,
        blank=True,
        help_text=_('Internal label for staff (optional).'),
    )
    is_manually_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text=_('Turn off to deactivate regardless of dates.'),
    )
    discount_type = models.CharField(
        max_length=16,
        choices=DiscountType.choices,
        default=DiscountType.PERCENT,
    )
    fixed_amount_gross = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_('For fixed type: amount to subtract (₾), capped by eligible subtotal and by % cap below.'),
    )
    fixed_cap_percent_of_eligible = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('100.00'),
        help_text=_('For fixed type: discount cannot exceed this % of eligible subtotal (default 100).'),
    )
    percent_off = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_('For percent type: 0–100.'),
    )
    percent_min_eligible_subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text=_('For percent type: eligible subtotal must be at least this (₾).'),
    )
    valid_from = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_('If set, code is not valid before this instant (site timezone).'),
    )
    valid_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_('If set, code is not valid after this instant.'),
    )
    single_use_globally = models.BooleanField(
        default=False,
        help_text=_('If true, only one redemption ever (across all customers).'),
    )
    max_redemptions = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_('Optional cap on total redemptions; leave empty for unlimited (within dates).'),
    )
    restricted_to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_promo_codes',
        help_text=_('If set, only this signed-in user may redeem the code.'),
    )
    application_scope = models.CharField(
        max_length=16,
        choices=ApplicationScope.choices,
        default=ApplicationScope.ALL,
    )
    restricted_products = models.ManyToManyField(
        'Product',
        blank=True,
        related_name='promo_codes_by_product',
        help_text=_('When scope is “Specific products” — eligible SKUs.'),
    )
    restricted_collections = models.ManyToManyField(
        'ProductCollection',
        blank=True,
        related_name='promo_codes_by_collection',
        help_text=_('When scope is “Specific collections” — union of collection + group products.'),
    )
    sort_order = models.IntegerField(default=0, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'code']
        verbose_name = _('Promo code')
        verbose_name_plural = _('Promo codes')

    def __str__(self) -> str:
        return self.code

    def save(self, *args, **kwargs):
        self.code = (self.code or '').strip().upper()
        super().save(*args, **kwargs)


class PromoRedemption(models.Model):
    """One successful checkout application of a promo (audit + usage limits)."""

    promo = models.ForeignKey(
        PromoCode,
        on_delete=models.CASCADE,
        related_name='redemptions',
    )
    sales_order = models.ForeignKey(
        SalesOrder,
        on_delete=models.CASCADE,
        related_name='promo_redemptions',
    )
    redeemed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='promo_redemptions',
    )
    discount_gross = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        verbose_name = _('Promo redemption')
        verbose_name_plural = _('Promo redemptions')

    def __str__(self) -> str:
        return f'{self.promo.code} → order {self.sales_order_id}'