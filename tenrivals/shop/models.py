from decimal import Decimal

from django.conf import settings
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
    GRIPS = 'GRIPS', 'Grips and Accessories'
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
    color = models.CharField(max_length=80, blank=True, null=True)

    class Meta:
        verbose_name = 'Shoe'
        verbose_name_plural = 'Shoes'


class Apparel(Product):
    # Apparel common fields (men/women/junior via gender)
    gender = models.CharField(max_length=1, choices=Gender.choices, default=Gender.UNISEX)
    sizes = models.JSONField(default=list, blank=True)  # e.g. ["S","M","L","XL"]
    material = models.CharField(max_length=120, blank=True, null=True)

    class Meta:
        verbose_name = 'Apparel'
        verbose_name_plural = 'Apparel'


class String(Product):
    # Strings specific fields
    gauge_mm = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)  # e.g. 1.25
    material = models.CharField(max_length=80, blank=True, null=True)  # e.g. Polyester, Multifilament, Natural
    length_m = models.PositiveIntegerField(null=True, blank=True)  # e.g. 12

    class Meta:
        verbose_name = 'String'
        verbose_name_plural = 'Strings'


class Bag(Product):
    capacity_rackets = models.PositiveIntegerField(null=True, blank=True)  # e.g. 6, 9, 12

    class Meta:
        verbose_name = 'Bag'
        verbose_name_plural = 'Bags'


class Balls(Product):
    balls_per_can = models.PositiveIntegerField(null=True, blank=True)  # e.g. 3 or 4
    surface = models.CharField(max_length=2, choices=CourtSurface.choices, default=CourtSurface.ALL_COURT)

    class Meta:
        verbose_name = 'Balls'
        verbose_name_plural = 'Balls'


class Accessory(Product):
    # Generic catch-all for grips and accessories
    class Meta:
        verbose_name = 'Accessory'
        verbose_name_plural = 'Accessories'


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


class BlogPost(models.Model):
    """Editorial post at /shop/blog/<slug>/ — featured home cards can link here."""

    slug = models.SlugField(max_length=160, unique=True, db_index=True)
    title = models.CharField(max_length=220)
    lead = models.TextField(blank=True, help_text=_('Short intro under the title (plain text).'))
    body = models.TextField(blank=True, help_text=_('Article body (plain text; line breaks preserved).'))
    hero_image = models.ImageField(upload_to='shop/blog/', null=True, blank=True)
    is_published = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-published_at', '-id']
        verbose_name = 'Blog post'
        verbose_name_plural = 'Blog posts'

    def __str__(self):
        return self.title


class HomeFeaturedStory(models.Model):
    """Vertical story tiles on the shop home carousel; optional blog post or custom URL."""

    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    image = models.ImageField(upload_to='shop/featured_stories/', null=True, blank=True)
    title = models.CharField(max_length=200, blank=True)
    caption = models.TextField(blank=True)
    link_label = models.CharField(max_length=120, blank=True, default='Read more')
    link_url = models.CharField(
        max_length=500,
        blank=True,
        help_text=_('Used when no blog post is selected (e.g. /shop/blog/your-slug/).'),
    )
    blog_post = models.ForeignKey(
        BlogPost,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='home_featured_cards',
    )
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'id']
        verbose_name = 'Home featured story'
        verbose_name_plural = 'Home featured stories'

    @property
    def is_visible(self) -> bool:
        return self.is_active and bool(self.image or self.title)


class ProductListingChannel(models.TextChoices):
    STOCK = 'STOCK', _('In stock')
    PREORDER = 'PREORDER', _('Preorder')


class ProductListing(models.Model):
    """Links a product to in-stock or preorder catalog with quantity (stock on hand)."""

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