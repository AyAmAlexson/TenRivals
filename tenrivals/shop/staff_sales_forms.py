from collections import defaultdict

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import Customer, SalesOrder, SalesOrderLine
from .sales_order_utils import stock_listing_quantity


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ['first_name', 'last_name', 'phone', 'email', 'newsletter_opt_in', 'address']
        widgets = {
            'address': forms.Textarea(
                attrs={
                    'rows': 3,
                    'style': 'width:100%;padding:9px 12px;border:1px solid #e5e7eb;font-size:14px;font-family:inherit',
                }
            ),
        }


class SalesOrderForm(forms.ModelForm):
    class Meta:
        model = SalesOrder
        fields = [
            'customer',
            'order_date',
            'delivery_gross',
            'fiscal_receipt',
            'payment_method',
            'notes',
        ]
        widgets = {
            'order_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].empty_label = 'Select customer…'
        self.fields['customer'].queryset = Customer.objects.all().order_by(
            'last_name', 'first_name', 'id'
        )


class SalesOrderLineForm(forms.ModelForm):
    class Meta:
        model = SalesOrderLine
        fields = ['product', 'quantity', 'unit_price_gross', 'discount_percent']


class BaseSalesOrderLineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        totals = defaultdict(int)
        for form in self.forms:
            if not hasattr(form, 'cleaned_data'):
                continue
            cd = form.cleaned_data
            if cd.get('DELETE'):
                continue
            p = cd.get('product')
            q = cd.get('quantity')
            if p is not None and q is not None:
                totals[p.pk] += int(q)
        released = defaultdict(int)
        order = self.instance
        if order.pk:
            for line in order.lines.all():
                released[line.product_id] += int(line.quantity)

        errors = []
        for pid, need in sorted(totals.items()):
            avail = stock_listing_quantity(pid) + released.get(pid, 0)
            if need > avail:
                errors.append(
                    f'Not enough stock for product ID {pid}: need {need}, available {avail}.'
                )
        if errors:
            raise ValidationError(errors)


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
