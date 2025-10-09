from django import forms
from .models import Product, Shoe


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            'type', 'name', 'sku', 'brand', 'price', 'in_stock', 'is_active',
            'image_1', 'image_2', 'image_3', 'short_description', 'description', 'attributes',
        ]


class ShoeForm(ProductForm):
    class Meta(ProductForm.Meta):
        model = Shoe
        fields = ProductForm.Meta.fields + ['gender', 'surface', 'sizes', 'color']


