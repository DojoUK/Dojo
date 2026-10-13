from django.apps import AppConfig


class MailerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mailer'
    verbose_name = 'Email delivery'

    def ready(self):
        from auditlog.registry import auditlog

        from .models import EmailSettings
        auditlog.register(EmailSettings, exclude_fields=['secrets'])
