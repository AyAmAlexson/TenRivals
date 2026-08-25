"""Staff forms for the Buying section."""

from django import forms

from shop.models import Customer

from .models import (
    BuyingBatch,
    BuyingBatchLine,
    BuyingRequest,
    CalculationRule,
    FulfillmentProvider,
    FulfillmentRoute,
    FulfillmentWarehouse,
    NormalizedProduct,
    OptimizationCandidate,
    ProductCategory,
    Supplier,
    SupplierOffer,
    TaxDisplayMode,
)


class BuyingRequestForm(forms.ModelForm):
    class Meta:
        model = BuyingRequest
        fields = ['original_query', 'client', 'quantity', 'notes']
        widgets = {
            'original_query': forms.Textarea(
                attrs={
                    'rows': 4,
                    'placeholder': 'e.g. ASICS Gel Resolution X Clay, men, white, EU 44',
                }
            ),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['client'].queryset = Customer.objects.order_by('last_name', 'first_name')
        self.fields['client'].required = False


class NormalizedProductForm(forms.ModelForm):
    class Meta:
        model = NormalizedProduct
        fields = [
            'brand', 'model_name', 'generation', 'variant', 'category', 'gender', 'court',
            'size', 'size_system', 'grip_size', 'color', 'color_policy',
            'weight_g', 'head_size', 'string_pattern', 'length_cm', 'quantity',
            'required_attributes', 'optional_attributes', 'aliases',
            'manufacturer_code', 'ean', 'upc',
        ]
        widgets = {
            'required_attributes': forms.Textarea(attrs={'rows': 2}),
            'optional_attributes': forms.Textarea(attrs={'rows': 2}),
            'aliases': forms.Textarea(attrs={'rows': 2}),
        }


class SupplierOfferForm(forms.ModelForm):
    """Manual offer entry (Phase 1). Connector-created offers reuse the model."""

    class Meta:
        model = SupplierOffer
        fields = [
            'supplier', 'title', 'product_url', 'supplier_sku', 'manufacturer_code',
            'ean', 'upc', 'original_price', 'current_price', 'currency',
            'requested_variant_available', 'stock_status', 'stock_quantity',
            'color', 'size', 'grip_size', 'court', 'gender', 'weight_g_actual',
            'length_cm', 'width_cm', 'height_cm',
            'local_shipping_cost', 'local_shipping_source', 'free_shipping_threshold',
            'tax_display_mode', 'supplier_tax_amount', 'supplier_tax_source',
            'match_status', 'match_score',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['supplier'].queryset = Supplier.objects.filter(enabled=True)
        self.fields['requested_variant_available'].widget = forms.NullBooleanSelect()

    def clean(self):
        cleaned = super().clean()
        supplier = cleaned.get('supplier')
        if supplier and not cleaned.get('currency'):
            cleaned['currency'] = supplier.currency
        return cleaned


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = [
            'name', 'code', 'base_url', 'country', 'currency', 'connector_class', 'enabled',
            'onex_applicability', 'default_destination_country', 'default_destination_postal_code',
            'tax_display_mode', 'free_shipping_threshold', 'average_delivery_days',
            'return_complexity', 'reliability_score', 'risk_score', 'notes',
        ]
        widgets = {'notes': forms.Textarea(attrs={'rows': 3})}
        help_texts = {
            'free_shipping_threshold': (
                'Free local shipping to the Onex warehouse when the cart (in this '
                'store’s currency) reaches this amount. Different per shop.'
            ),
            'tax_display_mode': (
                'For Onex calculator: use “Prices include VAT” / “VAT included” when '
                'you type shelf prices with local tax already in them — tax is kept '
                '(not stripped). Use “No local tax” only if the shop never charges it.'
            ),
        }


class FulfillmentProviderForm(forms.ModelForm):
    class Meta:
        model = FulfillmentProvider
        fields = ['name', 'code', 'enabled', 'website', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 2})}


class FulfillmentWarehouseForm(forms.ModelForm):
    class Meta:
        model = FulfillmentWarehouse
        fields = [
            'provider', 'country', 'state_or_region', 'city', 'postal_code',
            'address_reference', 'currency', 'enabled', 'notes',
        ]
        widgets = {'notes': forms.Textarea(attrs={'rows': 2})}


class FulfillmentRouteForm(forms.ModelForm):
    class Meta:
        model = FulfillmentRoute
        fields = [
            'name', 'provider', 'warehouse', 'origin_country', 'destination_country',
            'delivery_method', 'enabled', 'supplier', 'category',
            'estimated_min_days', 'estimated_max_days', 'priority',
            'valid_from', 'valid_to', 'notes',
        ]
        widgets = {
            'valid_from': forms.DateInput(attrs={'type': 'date'}),
            'valid_to': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }


class CalculationRuleForm(forms.ModelForm):
    class Meta:
        model = CalculationRule
        fields = [
            'rule_type', 'name', 'country', 'supplier', 'provider', 'warehouse',
            'category', 'params', 'conditions', 'version', 'active_from',
            'active_to', 'enabled', 'priority', 'notes',
        ]
        widgets = {
            'params': forms.Textarea(attrs={'rows': 4, 'class': 'mono'}),
            'conditions': forms.Textarea(attrs={'rows': 2, 'class': 'mono'}),
            'active_from': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'active_to': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def clean(self):
        # Params are validated per rule type at save time; conflicting active
        # rules (same type/scope/priority, overlapping window) are refused so
        # resolution never depends on a tie-breaker.
        from buying.engine.rule_handlers import RuleParamsError, validate_rule_params
        from buying.engine.rules import find_conflicting_rules

        cleaned = super().clean()
        rule_type = cleaned.get('rule_type')
        params = cleaned.get('params')
        if rule_type and params is not None:
            try:
                errors = validate_rule_params(rule_type, params)
            except RuleParamsError as exc:
                errors = [str(exc)]
            if errors:
                self.add_error('params', '; '.join(errors))

        if cleaned.get('enabled') and rule_type and not self.errors:
            candidate = CalculationRule(
                pk=self.instance.pk,
                rule_type=rule_type,
                country=cleaned.get('country') or '',
                supplier=cleaned.get('supplier'),
                provider=cleaned.get('provider'),
                warehouse=cleaned.get('warehouse'),
                category=cleaned.get('category') or '',
                priority=cleaned.get('priority'),
                active_from=cleaned.get('active_from'),
                active_to=cleaned.get('active_to'),
                enabled=True,
            )
            conflicts = find_conflicting_rules(candidate)
            if rule_type == CalculationRule.RuleType.FX_RATE:
                # FX rules are scoped by params["currency"], not by model scope.
                currency = str((params or {}).get('currency', '')).upper()
                conflicts = [
                    r for r in conflicts
                    if str(r.params.get('currency', '')).upper() == currency
                ]
            else:
                conflicts = list(conflicts)
            if conflicts:
                names = ', '.join(f'#{r.pk} "{r.name}" v{r.version}' for r in conflicts[:5])
                self.add_error(
                    None,
                    'Conflicts with active rule(s) of the same type, scope, priority '
                    f'and overlapping period: {names}. Disable the old rule, change '
                    'the priority, or close its active period first.',
                )
        return cleaned


class OptimizationCandidateForm(forms.ModelForm):
    class Meta:
        model = OptimizationCandidate
        fields = [
            'title', 'product', 'supplier', 'supplier_url', 'estimated_price',
            'currency', 'category', 'allowed_for_optimization', 'maximum_quantity',
            'expected_margin', 'expected_turnover_days', 'inventory_risk',
            'priority', 'notes',
        ]
        widgets = {'notes': forms.Textarea(attrs={'rows': 2})}


class OverrideForm(forms.Form):
    component = forms.ChoiceField()
    new_amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, components=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['component'].choices = components or []


class BuyingBatchForm(forms.ModelForm):
    class Meta:
        model = BuyingBatch
        fields = ['supplier', 'title', 'notes']
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 2}),
            'title': forms.TextInput(attrs={'placeholder': 'Optional label for this cart'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['supplier'].queryset = Supplier.objects.filter(enabled=True).order_by('name')
        self.fields['supplier'].empty_label = 'Select store…'
        self.fields['title'].required = False
        self.fields['notes'].required = False


class BuyingBatchLineForm(forms.ModelForm):
    class Meta:
        model = BuyingBatchLine
        fields = [
            'title',
            'unit_price',
            'currency',
            'quantity',
            'category',
            'url',
            'sku_hint',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Product name'}),
            'url': forms.URLInput(attrs={'placeholder': 'https://…'}),
            'sku_hint': forms.TextInput(attrs={'placeholder': 'SKU (optional)'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].choices = [('', 'Category…')] + list(ProductCategory.choices)
        self.fields['url'].required = False
        self.fields['sku_hint'].required = False
        self.fields['category'].required = True
        self.fields['currency'].required = False
        # Avoid treating blank extra rows as "partially filled" via model default qty=1.
        if not self.is_bound and not getattr(self.instance, 'pk', None):
            self.fields['quantity'].initial = 1

    def clean_category(self):
        value = (self.cleaned_data.get('category') or '').strip()
        if not value:
            raise forms.ValidationError('Select a product category (used for shipping weight).')
        return value


class BuyingBatchLineFormSetBase(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        alive = 0
        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                continue
            if form.cleaned_data.get('DELETE'):
                continue
            if form.cleaned_data.get('title') and form.cleaned_data.get('unit_price') is not None:
                alive += 1
        if alive < 1:
            raise forms.ValidationError('Add at least one product line.')


BuyingBatchLineFormSet = forms.inlineformset_factory(
    BuyingBatch,
    BuyingBatchLine,
    form=BuyingBatchLineForm,
    formset=BuyingBatchLineFormSetBase,
    extra=0,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


class QuickSupplierForm(forms.ModelForm):
    """Minimal store create for the calculator (manual-only, no connector)."""

    class Meta:
        model = Supplier
        fields = [
            'name',
            'base_url',
            'country',
            'currency',
            'onex_applicability',
            'tax_display_mode',
            'free_shipping_threshold',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['country'].widget.attrs['placeholder'] = 'US'
        self.fields['currency'].widget.attrs['placeholder'] = 'USD'
        self.fields['base_url'].widget.attrs['placeholder'] = 'https://…'
        self.fields['onex_applicability'].initial = 'supported'
        self.fields['tax_display_mode'].initial = TaxDisplayMode.PRICES_INCLUDE_VAT
        self.fields['free_shipping_threshold'].required = False
        self.fields['free_shipping_threshold'].help_text = (
            'Cart total in store currency for free local shipping to Onex (optional).'
        )
        self.fields['tax_display_mode'].help_text = (
            'Prices include VAT = you type tax-inclusive shelf prices (Onex keeps tax).'
        )

    def save(self, commit=True):
        from django.utils.text import slugify

        instance = super().save(commit=False)
        instance.connector_class = ''
        instance.enabled = True
        base = slugify(instance.name)[:50] or 'store'
        code = base
        n = 2
        while Supplier.objects.filter(code=code).exclude(pk=instance.pk).exists():
            code = f'{base}-{n}'
            n += 1
        instance.code = code
        if not instance.default_destination_country:
            instance.default_destination_country = instance.country
        if not instance.tax_display_mode or instance.tax_display_mode == TaxDisplayMode.UNKNOWN:
            instance.tax_display_mode = TaxDisplayMode.PRICES_INCLUDE_VAT
        if commit:
            instance.save()
        return instance
