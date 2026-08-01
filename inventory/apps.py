from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'inventory'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import Product, ProductVariant, StockMovement
        auditlog.register(Product)
        auditlog.register(ProductVariant)
        auditlog.register(StockMovement)
