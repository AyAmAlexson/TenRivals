from django import forms

from .models import Product, Shoe, ProductListingChannel


class ProductForm(forms.ModelForm):
    listing_channel = forms.ChoiceField(
        required=False,
        label='Catalog',
        help_text='In stock = warehouse qty; Preorder = catalog without holding qty requirement.',
    )
    listing_quantity = forms.IntegerField(
        min_value=0,
        initial=0,
        required=False,
        label='Quantity (on hand)',
    )

    class Meta:
        model = Product
        fields = [
            'type',
            'name',
            'sku',
            'brand',
            'initial_price',
            'actual_price',
            'in_stock',
            'is_active',
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
        self.fields['listing_channel'].choices = [
            ('', '—'),
        ] + list(ProductListingChannel.choices)
        if default_listing_channel:
            self.fields['listing_channel'].initial = default_listing_channel
        if listing_quantity is not None:
            self.fields['listing_quantity'].initial = listing_quantity


class ShoeForm(ProductForm):
    class Meta(ProductForm.Meta):
        model = Shoe
        fields = ProductForm.Meta.fields + ['gender', 'surface', 'sizes', 'color']
