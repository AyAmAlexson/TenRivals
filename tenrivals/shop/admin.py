from django.contrib import admin
from .models import (
    BlogPost,
    HomeHeroContent,
    HomeHeroSlide,
    HomePromoStripSettings,
    Product,
    Category,
    Racket,
    Shoe,
    Apparel,
    String,
    Bag,
    Balls,
    Accessory,
    ShopOrder,
    ShopOrderItem,
    ProductListing,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "parent")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "type",
        "brand",
        "initial_price",
        "actual_price",
        "in_stock",
        "is_active",
        "featured_product",
    )
    list_filter = ("type", "brand", "in_stock", "is_active", "featured_product")
    search_fields = ("name", "sku", "brand")


@admin.register(Racket)
class RacketAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "initial_price", "actual_price", "weight_grams", "head_size_sq_in")


@admin.register(Shoe)
class ShoeAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "initial_price", "actual_price", "gender", "surface", "width")


@admin.register(Apparel)
class ApparelAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "initial_price", "actual_price", "gender")


@admin.register(String)
class StringAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "initial_price", "actual_price", "gauge_mm", "material")


@admin.register(Bag)
class BagAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "initial_price", "actual_price", "capacity_rackets")


@admin.register(Balls)
class BallsAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "initial_price", "actual_price", "balls_per_can", "surface")


@admin.register(Accessory)
class AccessoryAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "initial_price", "actual_price")


@admin.register(ProductListing)
class ProductListingAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "channel", "quantity")
    list_filter = ("channel",)
    raw_id_fields = ("product",)


@admin.register(HomePromoStripSettings)
class HomePromoStripSettingsAdmin(admin.ModelAdmin):
    list_display = ("id", "left_visible", "right_visible", "updated_at")

    def has_add_permission(self, request):
        return not HomePromoStripSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HomeHeroContent)
class HomeHeroContentAdmin(admin.ModelAdmin):
    list_display = ("id", "headline", "cta_label", "updated_at")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["cta_url"].help_text = (
            "Path from site root (leading /) or full https URL, e.g. "
            "/shop/stock/?type=RACKET — type codes are RACKET, M_SHOES, W_SHOES, … "
            "(not “RACKETS”)."
        )
        form.base_fields["secondary_link_url"].help_text = (
            "Same as primary URL: prefer /shop/… from the domain root."
        )
        return form

    def has_add_permission(self, request):
        return not HomeHeroContent.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HomeHeroSlide)
class HomeHeroSlideAdmin(admin.ModelAdmin):
    list_display = ("id", "sort_order", "internal_note", "created_at")
    list_editable = ("sort_order",)
    ordering = ("sort_order", "id")


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "slug",
        "is_published",
        "is_featured_on_home",
        "featured_sort_order",
        "published_at",
        "updated_at",
    )
    list_filter = ("is_published", "is_featured_on_home")
    search_fields = ("title", "slug", "lead")
    prepopulated_fields = {"slug": ("title",)}
    ordering = ("-published_at", "-id")


class ShopOrderItemInline(admin.TabularInline):
    model = ShopOrderItem
    extra = 0
    autocomplete_fields = ("product",)
    fields = (
        "product",
        "product_name",
        "brand",
        "sku",
        "variant_label",
        "quantity",
        "unit_price",
        "line_total",
    )


@admin.register(ShopOrder)
class ShopOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "total_amount", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("user__email", "user__first_name", "user__last_name", "internal_note")
    readonly_fields = ("created_at", "updated_at", "total_amount")
    raw_id_fields = ("user",)
    inlines = (ShopOrderItemInline,)
    fieldsets = (
        (None, {"fields": ("user", "status", "total_amount")}),
        ("Notes", {"fields": ("customer_note", "internal_note")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
        obj.recalculate_total()
        obj.save(update_fields=["total_amount", "updated_at"])
