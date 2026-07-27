import json

from django import forms
from django.core.exceptions import ValidationError

from .models import (
    Accessory,
    Apparel,
    Bag,
    Balls,
    CourtSurface,
    Product,
    ProductListingChannel,
    Racket,
    Shoe,
    String,
)
from .size_inventory import (
    APPAREL_SIZE_LABELS,
    GRIP_SIZE_LABELS,
    STRING_GAUGE_MM_LABELS,
    labels_for_size_grid,
    normalize_sizes_to_qty_map,
    us_shoe_size_labels,
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
            'brand',
            'name',
            'color',
            'type',
            'sku',
            'initial_price',
            'actual_price',
            'landed_cost_gel',
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
        self.fields['landed_cost_gel'].label = 'Landed cost (₾)'

        self.fields['attributes'].widget = forms.HiddenInput()
        self.fields['attributes'].required = False

        ch = default_listing_channel or ProductListingChannel.PREORDER
        qty_init = 0 if listing_quantity is None else int(listing_quantity)
        self.fields['listing_channel'].initial = ch
        self.fields['listing_quantity'].initial = qty_init
        self.size_inventory_rows = []

    def clean_attributes(self):
        val = self.cleaned_data.get('attributes')
        if val in (None, ''):
            return {}
        if isinstance(val, str):
            try:
                val = json.loads(val) if val.strip() else {}
            except json.JSONDecodeError as e:
                raise ValidationError('Invalid extra attributes JSON.') from e
        if not isinstance(val, dict):
            raise ValidationError('Extra attributes must be a JSON object.')
        out = {}
        for k, v in val.items():
            k = str(k).strip()
            if not k:
                continue
            out[k] = str(v).strip() if v is not None else ''
        return out


def _clean_qty_map_field(raw, *, error_label: str) -> dict[str, int]:
    if raw in (None, '', {}):
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as e:
            raise ValidationError(f'Invalid {error_label} data.') from e
    if isinstance(raw, list):
        raw = normalize_sizes_to_qty_map(raw, fallback_total=0)
    if not isinstance(raw, dict):
        raise ValidationError(f'Invalid {error_label} data.')
    out: dict[str, int] = {}
    for k, v in raw.items():
        k = str(k).strip()
        if not k:
            continue
        try:
            n = int(v)
        except (TypeError, ValueError) as e:
            raise ValidationError(f'Invalid quantity for “{k}”.') from e
        if n < 0:
            raise ValidationError('Quantities cannot be negative.')
        if n > 0:
            out[k] = n
    return out


class ShoeForm(ProductForm):
    class Meta(ProductForm.Meta):
        model = Shoe
        fields = ProductForm.Meta.fields + ['gender', 'surface', 'sizes', 'width']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['surface'].required = False
        self.fields['sizes'].widget = forms.HiddenInput()
        self.fields['sizes'].required = False
        self.fields['width'].required = False
        self.fields['listing_quantity'].widget.attrs['readonly'] = True
        self.fields['listing_quantity'].label = 'Total quantity (auto)'
        self.fields['listing_quantity'].help_text = (
            'Auto: sum of per-size quantities (you edit quantities below).'
        )
        self._init_shoe_size_rows()

    def clean_surface(self):
        v = self.cleaned_data.get('surface')
        if v in (None, ''):
            return CourtSurface.ALL_COURT
        return v

    def _init_shoe_size_rows(self):
        allowed = us_shoe_size_labels()
        if self.data:
            raw = self.data.get('sizes', '')
            try:
                parsed = json.loads(raw) if raw else {}
                qty_map = normalize_sizes_to_qty_map(parsed, fallback_total=0)
            except json.JSONDecodeError:
                qty_map = {}
        else:
            raw_sizes = self.instance.sizes if self.instance.pk else self.initial.get('sizes')
            fb = int(self.initial.get('listing_quantity', 0) or 0)
            if isinstance(raw_sizes, dict):
                qty_map = normalize_sizes_to_qty_map(raw_sizes, fallback_total=0)
            elif isinstance(raw_sizes, list):
                qty_map = normalize_sizes_to_qty_map(raw_sizes, fallback_total=fb)
            else:
                qty_map = {}
        labels = labels_for_size_grid(allowed, qty_map)
        self.size_inventory_rows = [(lab, qty_map.get(lab, 0)) for lab in labels]

    def clean_sizes(self):
        return _clean_qty_map_field(
            self.cleaned_data.get('sizes'),
            error_label='shoe size',
        )

    def clean(self):
        cd = super().clean()
        sizes = cd.get('sizes')
        if isinstance(sizes, dict):
            cd['listing_quantity'] = sum(sizes.values())
        return cd


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['grip_sizes'].widget = forms.HiddenInput()
        self.fields['grip_sizes'].required = False
        self.fields['listing_quantity'].widget.attrs['readonly'] = True
        self.fields['listing_quantity'].label = 'Total quantity (auto)'
        self.fields['listing_quantity'].help_text = (
            'Auto: sum of per-grip quantities (you edit quantities below).'
        )
        self._init_grip_size_rows()

    def _init_grip_size_rows(self):
        allowed = list(GRIP_SIZE_LABELS)
        if self.data:
            raw = self.data.get('grip_sizes', '')
            try:
                parsed = json.loads(raw) if raw else {}
                qty_map = normalize_sizes_to_qty_map(parsed, fallback_total=0)
            except json.JSONDecodeError:
                qty_map = {}
        else:
            raw_sizes = self.instance.grip_sizes if self.instance.pk else self.initial.get('grip_sizes')
            fb = int(self.initial.get('listing_quantity', 0) or 0)
            if isinstance(raw_sizes, dict):
                qty_map = normalize_sizes_to_qty_map(raw_sizes, fallback_total=0)
            elif isinstance(raw_sizes, list):
                qty_map = normalize_sizes_to_qty_map(raw_sizes, fallback_total=fb)
            else:
                qty_map = {}
        labels = labels_for_size_grid(allowed, qty_map)
        self.size_inventory_rows = [(lab, qty_map.get(lab, 0)) for lab in labels]

    def clean_grip_sizes(self):
        return _clean_qty_map_field(
            self.cleaned_data.get('grip_sizes'),
            error_label='grip size',
        )

    def clean(self):
        cd = super().clean()
        grips = cd.get('grip_sizes')
        if isinstance(grips, dict):
            cd['listing_quantity'] = sum(grips.values())
        return cd


class ApparelForm(ProductForm):
    class Meta(ProductForm.Meta):
        model = Apparel
        fields = ProductForm.Meta.fields + ['gender', 'sizes', 'material']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sizes'].widget = forms.HiddenInput()
        self.fields['sizes'].required = False
        self.fields['listing_quantity'].widget.attrs['readonly'] = True
        self.fields['listing_quantity'].label = 'Total quantity (auto)'
        self.fields['listing_quantity'].help_text = (
            'Auto: sum of per-size quantities (you edit quantities below).'
        )
        self._init_apparel_size_rows()

    def _init_apparel_size_rows(self):
        allowed = list(APPAREL_SIZE_LABELS)
        if self.data:
            raw = self.data.get('sizes', '')
            try:
                parsed = json.loads(raw) if raw else {}
                qty_map = normalize_sizes_to_qty_map(parsed, fallback_total=0)
            except json.JSONDecodeError:
                qty_map = {}
        else:
            raw_sizes = self.instance.sizes if self.instance.pk else self.initial.get('sizes')
            fb = int(self.initial.get('listing_quantity', 0) or 0)
            if isinstance(raw_sizes, dict):
                qty_map = normalize_sizes_to_qty_map(raw_sizes, fallback_total=0)
            elif isinstance(raw_sizes, list):
                qty_map = normalize_sizes_to_qty_map(raw_sizes, fallback_total=fb)
            else:
                qty_map = {}
        labels = labels_for_size_grid(allowed, qty_map)
        self.size_inventory_rows = [(lab, qty_map.get(lab, 0)) for lab in labels]

    def clean_sizes(self):
        return _clean_qty_map_field(
            self.cleaned_data.get('sizes'),
            error_label='apparel size',
        )

    def clean(self):
        cd = super().clean()
        sizes = cd.get('sizes')
        if isinstance(sizes, dict):
            cd['listing_quantity'] = sum(sizes.values())
        return cd


class StringForm(ProductForm):
    class Meta(ProductForm.Meta):
        model = String
        fields = ProductForm.Meta.fields + ['gauges', 'material', 'length_m']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['gauges'].widget = forms.HiddenInput()
        self.fields['gauges'].required = False
        self.fields['listing_quantity'].widget.attrs['readonly'] = True
        self.fields['listing_quantity'].label = 'Total quantity (auto)'
        self.fields['listing_quantity'].help_text = (
            'Auto: sum of per-gauge quantities (you edit quantities below).'
        )
        self._init_string_gauge_rows()

    def _init_string_gauge_rows(self):
        allowed = list(STRING_GAUGE_MM_LABELS)
        if self.data:
            raw = self.data.get('gauges', '')
            try:
                parsed = json.loads(raw) if raw else {}
                qty_map = normalize_sizes_to_qty_map(parsed, fallback_total=0)
            except json.JSONDecodeError:
                qty_map = {}
        else:
            raw_g = self.instance.gauges if self.instance.pk else self.initial.get('gauges')
            fb = int(self.initial.get('listing_quantity', 0) or 0)
            if isinstance(raw_g, dict):
                qty_map = normalize_sizes_to_qty_map(raw_g, fallback_total=0)
            elif isinstance(raw_g, list):
                qty_map = normalize_sizes_to_qty_map(raw_g, fallback_total=fb)
            else:
                qty_map = {}
        labels = labels_for_size_grid(allowed, qty_map)
        self.size_inventory_rows = [(lab, qty_map.get(lab, 0)) for lab in labels]

    def clean_gauges(self):
        return _clean_qty_map_field(
            self.cleaned_data.get('gauges'),
            error_label='string gauge',
        )

    def clean(self):
        cd = super().clean()
        gauges = cd.get('gauges')
        if isinstance(gauges, dict) and gauges:
            cd['listing_quantity'] = sum(gauges.values())
        return cd


class BagForm(ProductForm):
    class Meta(ProductForm.Meta):
        model = Bag
        fields = ProductForm.Meta.fields + ['capacity_rackets']


class BallsForm(ProductForm):
    class Meta(ProductForm.Meta):
        model = Balls
        fields = ProductForm.Meta.fields + ['balls_per_can', 'surface']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['surface'].required = False

    def clean_surface(self):
        v = self.cleaned_data.get('surface')
        if v in (None, ''):
            return CourtSurface.ALL_COURT
        return v


class AccessoryForm(ProductForm):
    """Grips and generic accessories — no extra model fields beyond Product."""

    class Meta(ProductForm.Meta):
        model = Accessory
        fields = ProductForm.Meta.fields
