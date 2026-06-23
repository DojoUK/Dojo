from django.apps import AppConfig


class MembersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'members'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import CustomField, Guardian, Member
        auditlog.register(Member, exclude_fields=['token'])
        auditlog.register(Guardian)
        auditlog.register(CustomField)
