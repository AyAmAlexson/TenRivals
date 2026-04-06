from django import forms

from .models import (
    Accessory,
    Apparel,
    Bag,
    Balls,
    Product,
    ProductListingChannel,
    Racket,
    Shoe,
    String,
)


class ProductForm(forms.ModelForm):
    listing_channel = forms.ChoiceField(
        required=True,
        label='Catalog',
        help_text='Preorder — indicative catalog; In stock — warehouse listing with quantity.',
    )
    listing_quantity = forms.IntegerField(
        min_value=0,
        initial=0,
        required=False,
        label='Quantity (in stock)',
    )

    class Meta:
        model = Product
        fields = [
            'category',
            'brand',
            'name',
            'type',
            'sku',
            'initial_price',
            'actual_price',
            'in_stock',
            'is_active',
            'featured_product',
            'listing_channel',
            'listing_quantity',
            'image_1',
            'image_2',
            'image_3',
            'image_4',
            'image_5',
            'short_description',
            'description',
            'attributes',
        ]

    def __init__(
        self, *args, default_listing_channel=None, listing_quantity=None, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.fields['listing_channel'].choices = list(ProductListingChannel.choices)
        self.fields['type'].label = 'Product type'
        self.fields['name'].label = 'Model'
        self.fields['category'].required = False
        self.fields['category'].empty_label = '— None —'

        ch = default_listing_channel or ProductListingChannel.PREORDER
        qty_init = 0 if listing_quantity is None else int(listing_quantity)
        self.fields['listing_channel'].initial = ch
        self.fields['listing_quantity'].initial = qty_init


class ShoeForm(ProductForm):
    class Meta(ProductForm.Meta):
        model = Shoe
        fields = ProductForm.Meta.fields + ['gender', 'surface', 'sizes', 'color']


class RacketForm(ProductForm):
    class Meta(ProductForm.Meta):
        model = Racket
        fields = ProductForm.Meta.fields + [
            'weight_grams',
            'head_size_sq_in',
            'length_in',
            'balance_mm',
            'swingweight',
            'string_pattern',
            'is_strung',
            'grip_sizes',
        ]


class ApparelForm(ProductForm):
    class Meta(ProductForm.Meta):
        model = Apparel
        fields = ProductForm.Meta.fields + ['gender', 'sizes', 'material']


class StringForm(ProductForm):
    class Meta(ProductForm.Meta):
        model = String
        fields = ProductForm.Meta.fields + ['gauge_mm', 'material', 'length_m']


class BagForm(ProductForm):
    class Meta(ProductForm.Meta):
        model = Bag
        fields = ProductForm.Meta.fields + ['capacity_rackets']


class BallsForm(ProductForm):
    class Meta(ProductForm.Meta):
        model = Balls
        fields = ProductForm.Meta.fields + ['balls_per_can', 'surface']


class AccessoryForm(ProductForm):
    """Grips and generic accessories — no extra model fields beyond Product."""

    class Meta(ProductForm.Meta):
        model = Accessory
        fields = ProductForm.Meta.fields
