from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from .models import Product, Category, ProductType, Shoe
from .forms import ProductForm, ShoeForm

def index(request):
    return render(request, 'shop/index.html')


def items_list_for_Laen(request):
    # Tab filter: by category slug or by product type code
    category_slug = request.GET.get('category')
    type_code = request.GET.get('type')

    categories = Category.objects.all().order_by('name')

    products = Product.objects.filter(is_active=True)
    active_tab = 'all'

    if category_slug:
        products = products.filter(category__slug=category_slug)
        active_tab = f'cat:{category_slug}'
    elif type_code:
        products = products.filter(type=type_code)
        active_tab = f'type:{type_code}'

    # Order by ascending price, then by id for stability
    products = products.order_by('price', 'id')

    # For tabs, we provide both: category list and fixed type list
    type_tabs = [(choice.value, choice.label) for choice in ProductType]

    context = {
        'categories': categories,
        'type_tabs': type_tabs,
        'products': products,
        'active_tab': active_tab,
    }
    return render(request, 'shop/items_list_for_Laen.html', context)


@staff_member_required
def product_create(request):
    type_code = request.GET.get('type')
    is_shoe = type_code in {ProductType.MENS_SHOES, ProductType.WOMENS_SHOES, ProductType.JUNIOR_SHOES}
    FormClass = ShoeForm if is_shoe else ProductForm

    if request.method == 'POST':
        # Decide form by posted type
        posted_type = request.POST.get('type')
        is_shoe_post = posted_type in {ProductType.MENS_SHOES, ProductType.WOMENS_SHOES, ProductType.JUNIOR_SHOES}
        FormClass = ShoeForm if is_shoe_post else ProductForm
        form = FormClass(request.POST, request.FILES)
        if form.is_valid():
            obj = form.save()
            return redirect(reverse('shop:product_edit', args=[obj.pk]))
    else:
        form = FormClass(initial={'type': type_code} if type_code else None)

    return render(request, 'shop/product_form.html', {'form': form, 'is_edit': False})


@staff_member_required
def product_edit(request, pk):
    base = get_object_or_404(Product, pk=pk)
    instance = base
    try:
        instance = base.shoe
        FormClass = ShoeForm
    except Shoe.DoesNotExist:
        FormClass = ProductForm

    if request.method == 'POST':
        # Handle delete
        if request.POST.get('_delete') == '1':
            # delete images first
            for field in ('image_1', 'image_2', 'image_3'):
                f = getattr(base, field, None)
                if f and getattr(f, 'name', None):
                    try:
                        f.delete(save=False)
                    except Exception:
                        pass
            base.delete()
            return redirect(reverse('shop:items_list_for_Laen'))
        form = FormClass(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            obj = form.save()
            return redirect(reverse('shop:product_edit', args=[obj.pk]))
    else:
        form = FormClass(instance=instance)

    return render(request, 'shop/product_form.html', {'form': form, 'is_edit': True, 'object': instance})