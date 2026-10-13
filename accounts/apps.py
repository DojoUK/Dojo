from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'
    verbose_name = 'Accounts and sign-in'

    def ready(self):
        from auditlog.registry import auditlog

        from .models import TwoFactorDevice
        auditlog.register(TwoFactorDevice, exclude_fields=['secret', 'last_used_step', 'failed_attempts', 'locked_until'])
