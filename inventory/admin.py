from django.contrib import admin

from .models import Product, ProductVariant, StockMovement


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ('size', 'sku', 'price', 'quantity_in_stock', 'low_stock_threshold', 'is_active')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'organisation', 'category', 'total_in_stock', 'is_active')
    list_filter = ('organisation', 'category', 'is_active')
    search_fields = ('name',)
    inlines = [ProductVariantInline]


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ('product', 'size', 'sku', 'price', 'quantity_in_stock', 'is_low_stock', 'is_active')
    list_filter = ('product__organisation', 'is_active')
    search_fields = ('product__name', 'size', 'sku')


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('variant', 'quantity_change', 'reason', 'invoice', 'created_by', 'created_at')
    list_filter = ('reason', 'variant__product__organisation')
    search_fields = ('variant__product__name', 'variant__size', 'notes')
    date_hierarchy = 'created_at'
    readonly_fields = ('variant', 'quantity_change', 'reason', 'invoice', 'created_by', 'created_at')
