from django.contrib import admin
from .models import Product, Category, Racket, Shoe, Apparel, String, Bag, Balls, Accessory


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "parent")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "brand", "price", "in_stock", "is_active")
    list_filter = ("type", "brand", "in_stock", "is_active")
    search_fields = ("name", "sku", "brand")


@admin.register(Racket)
class RacketAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "price", "weight_grams", "head_size_sq_in")


@admin.register(Shoe)
class ShoeAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "price", "gender", "surface")


@admin.register(Apparel)
class ApparelAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "price", "gender")


@admin.register(String)
class StringAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "price", "gauge_mm", "material")


@admin.register(Bag)
class BagAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "price", "capacity_rackets")


@admin.register(Balls)
class BallsAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "price", "balls_per_can", "surface")


@admin.register(Accessory)
class AccessoryAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "price")
