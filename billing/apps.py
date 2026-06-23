from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'billing'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import Invoice, Payment
        auditlog.register(Invoice)
        auditlog.register(Payment)
