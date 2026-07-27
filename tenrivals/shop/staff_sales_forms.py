from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import Customer, SalesOrder, SalesOrderLine
from .sales_order_currency import normalize_payment_currency
from .sales_order_stock import (
    product_requires_variant,
    snapshot_old_lines,
    validate_order_line_demands,
)


def _product_choice_label(obj) -> str:
    from .sales_order_stock import staff_order_product_option_label

    return staff_order_product_option_label(obj)


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = [
            'first_name',
            'last_name',
            'name_local',
            'surname_local',
            'phone',
            'email',
            'tg_account',
            'newsletter_opt_in',
            'address',
        ]
        widgets = {
            'address': forms.Textarea(
                attrs={
                    'rows': 3,
                    'style': 'width:100%;padding:9px 12px;border:1px solid #e5e7eb;font-size:14px;font-family:inherit',
                }
            ),
            'newsletter_opt_in': forms.CheckboxInput(
                attrs={'class': 'customer-form-checkbox'}
            ),
            'tg_account': forms.TextInput(attrs={'placeholder': '@username'}),
        }


class SalesOrderForm(forms.ModelForm):
    # CharField on model has no choices — Select in Meta.widgets renders empty; use TextInput + datalist.
    payment_currency = forms.CharField(
        label='Payment currency',
        max_length=8,
        initial='GEL',
        widget=forms.TextInput(
            attrs={
                'id': 'id_payment_currency',
                'list': 'payment-currency-list',
                'maxlength': '8',
                'autocomplete': 'off',
                'placeholder': 'GEL',
                'style': 'width:100%;padding:9px 12px;border:1px solid #e5e7eb;font-size:14px;font-family:inherit',
            }
        ),
    )

    class Meta:
        model = SalesOrder
        fields = [
            'customer',
            'order_date',
            'status',
            'delivery_gross',
            'fiscal_receipt',
            'payment_method',
            'payment_currency',
            'exchange_rate',
            'notes',
        ]
        widgets = {
            'order_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
            'exchange_rate': forms.NumberInput(
                attrs={
                    'id': 'id_exchange_rate',
                    'step': '0.000001',
                    'min': '0',
                    'inputmode': 'decimal',
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].empty_label = 'Select customer…'
        self.fields['customer'].queryset = Customer.objects.all().order_by(
            'last_name', 'first_name', 'id'
        )
        self.fields['exchange_rate'].required = False
        cur = 'GEL'
        if self.instance.pk:
            cur = normalize_payment_currency(self.instance.payment_currency)
        elif self.initial.get('payment_currency'):
            cur = normalize_payment_currency(self.initial.get('payment_currency'))
        self.fields['payment_currency'].initial = cur
        if self.instance.pk and self.instance.exchange_rate is not None:
            self.fields['exchange_rate'].initial = self.instance.exchange_rate
        elif not self.instance.pk:
            self.fields['exchange_rate'].initial = Decimal('1')

    def clean_payment_currency(self):
        raw = (self.cleaned_data.get('payment_currency') or '').strip()
        if not raw:
            raise ValidationError('Enter a payment currency code.')
        return normalize_payment_currency(raw)

    def clean(self):
        cleaned = super().clean()
        cur = cleaned.get('payment_currency') or 'GEL'
        rate = cleaned.get('exchange_rate')
        if rate is None or rate == '':
            rate = Decimal('1')
        else:
            rate = Decimal(str(rate))
        if cur == 'GEL':
            cleaned['exchange_rate'] = Decimal('1')
        elif rate <= 0:
            self.add_error('exchange_rate', 'Enter a positive exchange rate.')
        else:
            cleaned['exchange_rate'] = rate
        return cleaned


class SalesOrderLineForm(forms.ModelForm):
    # Not a model field: unchecked means a free-text (non-stock) line; persisted as product=None.
    from_stock = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'sales-line-from-stock'}),
    )
    variant_label = forms.CharField(
        required=False,
        max_length=48,
        widget=forms.Select(
            attrs={'class': 'sales-line-variant', 'data-variant-select': '1'}
        ),
    )

    class Meta:
        model = SalesOrderLine
        fields = [
            'product',
            'custom_label',
            'variant_label',
            'quantity',
            'unit_price_gross',
            'discount_percent',
            'landed_cost_gel',
        ]
        widgets = {
            'custom_label': forms.TextInput(
                attrs={
                    'class': 'sales-line-custom',
                    'placeholder': 'Item name (free text, incl. size/grip)',
                    'maxlength': '200',
                    'autocomplete': 'off',
                }
            ),
            # Staff-only column; frozen snapshot, JS refills it only when the
            # product selection changes (never on page load).
            'landed_cost_gel': forms.NumberInput(
                attrs={
                    'class': 'sales-line-cost',
                    'step': '0.01',
                    'min': '0',
                    'inputmode': 'decimal',
                    'placeholder': '—',
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'product' in self.fields:
            self.fields['product'].label_from_instance = _product_choice_label
        if self.instance and getattr(self.instance, 'pk', None):
            self.fields['from_stock'].initial = self.instance.product_id is not None
        vf = self.fields['variant_label']
        vf.label = 'Size / grip'
        vf.widget.choices = [('', '—')]
        if self.instance and getattr(self.instance, 'pk', None) and self.instance.product_id:
            self._set_variant_choices_for_product(self.instance.product)

    def _set_variant_choices_for_product(self, product):
        from .sales_order_stock import get_variant_qty_map, product_requires_variant

        vf = self.fields['variant_label']
        if not product_requires_variant(product):
            vf.widget.choices = [('', '—')]
            vf.required = False
            return
        m = get_variant_qty_map(product)
        opts = [('', '—')]
        for k in sorted(m.keys()):
            q = int(m.get(k, 0) or 0)
            opts.append((k, f'{k} (×{q})'))
        current_variant = ''
        if self.instance and getattr(self.instance, 'pk', None):
            current_variant = (self.instance.variant_label or '').strip()
        if current_variant and current_variant not in m:
            opts.append((current_variant, f'{current_variant} (in this order)'))
        vf.widget.choices = opts

    def clean(self):
        cd = super().clean()
        if cd.get('DELETE'):
            return cd
        from_stock = bool(cd.get('from_stock'))
        product = cd.get('product')
        variant = (cd.get('variant_label') or '').strip()
        if not from_stock:
            # Free-text legacy line: no product link, no stock checks.
            if not (cd.get('custom_label') or '').strip():
                self.add_error('custom_label', 'Enter item name for a non-stock line.')
            cd['product'] = None
            cd['variant_label'] = ''
            cd['custom_label'] = (cd.get('custom_label') or '').strip()
            return cd
        cd['custom_label'] = ''
        if not product:
            self.add_error('product', 'Select a product, or untick "In stock" for a free-text line.')
            return cd
        if product_requires_variant(product) and not variant:
            raise ValidationError('Select size / grip for this product.')
        if not product_requires_variant(product) and variant:
            cd['variant_label'] = ''
        return cd


class BaseSalesOrderLineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        old_lines = []
        if self.instance.pk:
            old_lines = snapshot_old_lines(self.instance)
        old_status = self.instance.status if self.instance.pk else None

        demands = []
        for form in self.forms:
            if not hasattr(form, 'cleaned_data'):
                continue
            cd = form.cleaned_data
            if cd.get('DELETE'):
                continue
            p = cd.get('product')
            q = cd.get('quantity')
            if p is not None and q is not None:
                var = (cd.get('variant_label') or '').strip()
                demands.append((p, var, int(q)))

        errs = validate_order_line_demands(
            demands,
            order_pk=self.instance.pk,
            old_status=old_status,
            old_lines=old_lines,
        )
        if errs:
            raise ValidationError(errs)


SalesOrderLineFormSet = inlineformset_factory(
    SalesOrder,
    SalesOrderLine,
    form=SalesOrderLineForm,
    formset=BaseSalesOrderLineFormSet,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
