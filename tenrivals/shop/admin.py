from django.contrib import admin
from .models import (
    HomeBanner,
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
    list_display = ("name", "type", "brand", "initial_price", "actual_price", "in_stock", "is_active")
    list_filter = ("type", "brand", "in_stock", "is_active")
    search_fields = ("name", "sku", "brand")


@admin.register(Racket)
class RacketAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "initial_price", "actual_price", "weight_grams", "head_size_sq_in")


@admin.register(Shoe)
class ShoeAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "initial_price", "actual_price", "gender", "surface")


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


@admin.register(HomeBanner)
class HomeBannerAdmin(admin.ModelAdmin):
    list_display = ("id", "slot", "archived_at", "link_url", "internal_note", "created_at")
    list_filter = ("slot", "archived_at")
    readonly_fields = ("created_at",)


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
