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
    price = models.DecimalField(max_digits=10, decimal_places=2)
    in_stock = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    # Images (up to 3)
    image_1 = models.ImageField(upload_to='shop/products/', null=True, blank=True)
    image_2 = models.ImageField(upload_to='shop/products/', null=True, blank=True)
    image_3 = models.ImageField(upload_to='shop/products/', null=True, blank=True)

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
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name}'

    @property
    def main_image(self):
        return self.image_1 or self.image_2 or self.image_3

    @property
    def sizes_list(self):
        try:
            return self.shoe.sizes
        except Exception:
            return None


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
    sizes = models.JSONField(default=list, blank=True)  # e.g. ["EU 41", "EU 42", "EU 43"]
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