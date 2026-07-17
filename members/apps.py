from django.apps import AppConfig


class MembersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'members'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import CustomField, Guardian, Member, MemberApplication, MemberNote
        auditlog.register(Member, exclude_fields=['token'])
        auditlog.register(Guardian)
        auditlog.register(CustomField)
        auditlog.register(MemberNote)
        auditlog.register(MemberApplication, exclude_fields=['signature_data'])
